// src/ble.rs — BLE transport for Razer Joro keyboard via direct WinRT
// Last modified: 2026-04-12
//
// Replaces the previous btleplug-based implementation. btleplug 0.12 does not
// configure GattSession.MaintainConnection on Windows, which causes the GATT
// session to close within ~1 second of connect. By owning the WinRT lifecycle
// directly through the `windows` crate, we can set MaintainConnection=true on
// our session and hold the reference for the lifetime of the connection.
//
// Protocol30 over GATT:
//   GET: single 8-byte ATT Write Request to char 1524
//   SET: split write — 8-byte header then data payload as separate ATT writes
//   Responses arrive as notifications on char 1525

use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use windows::core::{Error as WinError, Interface, Result as WinResult, GUID};
use windows::Devices::Bluetooth::Advertisement::{
    BluetoothLEAdvertisementReceivedEventArgs, BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
};
use windows::Devices::Bluetooth::GenericAttributeProfile::{
    GattCharacteristic, GattCharacteristicProperties,
    GattClientCharacteristicConfigurationDescriptorValue, GattCommunicationStatus,
    GattDeviceService, GattSession, GattValueChangedEventArgs, GattWriteOption,
};
use windows::Devices::Bluetooth::{
    BluetoothCacheMode, BluetoothConnectionStatus, BluetoothLEDevice,
};
use windows::Devices::Enumeration::DeviceInformation;
use windows::Foundation::{EventRegistrationToken, IClosable, TypedEventHandler};
use windows::Storage::Streams::{DataReader, DataWriter};

// ── Constants ────────────────────────────────────────────────────────────────

const RAZER_SERVICE_UUID: GUID = GUID::from_u128(0x52401523_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_TX_UUID: GUID = GUID::from_u128(0x52401524_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_RX_UUID: GUID = GUID::from_u128(0x52401525_f97c_7f90_0e7f_6c6f4e36db1c);

// Standard BLE Battery Service (0x180F) / Battery Level char (0x2A19).
// REHABILITATED 2026-07-07: the May "frozen at 100%" verdict blamed this
// characteristic, but the culprit was almost certainly Windows' GATT READ
// CACHE (default BluetoothCacheMode::Cached). Read UNCACHED, 0x2A19 exactly
// matches the wired Protocol30 register (both said 45% while the BLE-side
// Protocol30 0x07:80 served a months-stale 76%). 0x2A19 is the PRIMARY
// battery source over BLE — always read with BluetoothCacheMode::Uncached,
// never Cached.
const BATTERY_SERVICE_UUID: GUID = GUID::from_u128(0x0000180f_0000_1000_8000_00805f9b34fb);
const BATTERY_LEVEL_UUID: GUID = GUID::from_u128(0x00002a19_0000_1000_8000_00805f9b34fb);

const SCAN_TIMEOUT: Duration = Duration::from_millis(1500);
const WRITE_DELAY: Duration = Duration::from_millis(150);
const RESPONSE_TIMEOUT: Duration = Duration::from_millis(2000);

pub const STATUS_SUCCESS: u8 = 0x02;

// ── BleDevice ────────────────────────────────────────────────────────────────

pub struct BleDevice {
    device: BluetoothLEDevice,
    // Hold the GATT session with MaintainConnection=true so WinRT does not
    // idle-close the connection. Dropping this releases the setting.
    _session: GattSession,
    char_tx: GattCharacteristic,
    char_rx: GattCharacteristic,
    /// Standard Battery Level char (0x2A19) — primary battery source over
    /// BLE (uncached reads + change notifications). See the const comment.
    char_battery: Option<GattCharacteristic>,
    battery_notif_token: Option<EventRegistrationToken>,
    // Channel of received notification payloads from the ValueChanged callback
    notif_rx: mpsc::Receiver<Vec<u8>>,
    // Token for unregistering the ValueChanged handler in Drop
    notif_token: EventRegistrationToken,
    txn_id: u8,
    // Counter for consecutive is_connected=false polls. Windows reports
    // momentary "Disconnected" immediately after connect even though the
    // device is fine. We tolerate a few consecutive false readings before
    // declaring the connection dead.
    disconnect_count: u32,
}

impl BleDevice {
    /// Find a Joro keyboard and set up GATT.
    ///
    /// Strategy:
    ///   1. First, enumerate *paired* BLE devices via `DeviceInformation`
    ///      (`GetDeviceSelectorFromPairingState(true)`). This works even when
    ///      the keyboard is already connected to Windows and not advertising.
    ///   2. Fall back to a live advertisement scan for unpaired first-time use.
    pub fn open() -> Option<Self> {
        // Path 1: try already-paired devices (fast, works on reconnect)
        match find_paired_joro() {
            Ok(Some(device)) => {
                eprintln!("joro-ble: found paired Joro — attaching");
                match connect_from_device(device) {
                    Ok(dev) => {
                        eprintln!("joro-ble: connected and GATT ready");
                        return Some(dev);
                    }
                    Err(e) => {
                        eprintln!("joro-ble: paired attach failed: {e:?}");
                        // fall through to advertisement scan
                    }
                }
            }
            Ok(None) => {
                eprintln!("joro-ble: no paired Joro found, falling back to advertisement scan");
            }
            Err(e) => {
                eprintln!("joro-ble: paired enumeration failed: {e:?}, falling back to scan");
            }
        }

        // Path 2: advertisement scan (first-time pairing / unpaired devices)
        let addr = scan_for_joro(SCAN_TIMEOUT)?;
        eprintln!("joro-ble: scan found Joro at {:012X}", addr);
        match connect_from_address(addr) {
            Ok(dev) => {
                eprintln!("joro-ble: connected and GATT ready");
                Some(dev)
            }
            Err(e) => {
                eprintln!("joro-ble: connect failed: {e:?}");
                None
            }
        }
    }

    fn next_txn(&mut self) -> u8 {
        self.txn_id = self.txn_id.wrapping_add(1);
        self.txn_id
    }

    fn drain_notifications(&self) {
        // Log what we throw away: unsolicited keyboard→host frames on the
        // Razer RX char are the BLE analogue of the dongle's `09 31`
        // heartbeat — the one PROVEN-live battery telemetry channel. If the
        // keyboard pushes periodic telemetry here, these lines reveal it
        // (frames accumulate between commands and surface at the next
        // 60s battery poll's drain).
        while let Ok(frame) = self.notif_rx.try_recv() {
            let hex: String = frame
                .iter()
                .take(24)
                .map(|b| format!("{b:02x}"))
                .collect::<Vec<_>>()
                .join(" ");
            eprintln!("joro-ble: unsolicited notification ({} B) [{hex}]", frame.len());
        }
    }

    /// Write bytes to char_tx as an ATT Write Request (with response).
    fn write_char(&self, data: &[u8]) -> Result<(), String> {
        let buf = vec_to_buffer(data).map_err(|e| format!("DataWriter: {e}"))?;
        let result = self
            .char_tx
            .WriteValueWithResultAndOptionAsync(&buf, GattWriteOption::WriteWithResponse)
            .map_err(|e| format!("WriteValueWithResult: {e}"))?
            .get()
            .map_err(|e| format!("WriteValueWithResult get: {e}"))?;

        let status = result.Status().map_err(|e| format!("Status: {e}"))?;
        if status != GattCommunicationStatus::Success {
            return Err(format!("write status: {:?}", status));
        }
        Ok(())
    }

    /// Wait for a notification payload from char_rx (via ValueChanged).
    fn read_notification(&self) -> Result<Vec<u8>, String> {
        self.notif_rx
            .recv_timeout(RESPONSE_TIMEOUT)
            .map_err(|_| "BLE response timeout".to_string())
    }

    /// Send a GET command (8-byte header, no data). Returns response data bytes.
    fn send_get(&mut self, class: u8, cmd: u8, sub1: u8, sub2: u8) -> Result<Vec<u8>, String> {
        let txn = self.next_txn();
        let header = [txn, 0, 0, 0, class, cmd, sub1, sub2];

        self.drain_notifications();
        self.write_char(&header)?;
        self.read_response(txn)
    }

    /// Send a SET command (split write: 8-byte header + data payload).
    fn send_set(
        &mut self,
        class: u8,
        cmd: u8,
        sub1: u8,
        sub2: u8,
        data: &[u8],
    ) -> Result<(), String> {
        let txn = self.next_txn();
        let dlen = data.len() as u8;
        let header = [txn, dlen, 0, 0, class, cmd, sub1, sub2];

        self.drain_notifications();
        self.write_char(&header)?;
        std::thread::sleep(WRITE_DELAY);
        self.write_char(data)?;
        let _ = self.read_response(txn)?;
        Ok(())
    }

    fn read_response(&self, _expected_txn: u8) -> Result<Vec<u8>, String> {
        // Wait for header notification
        let header = self.read_notification()?;
        if header.len() < 8 {
            return Err(format!("BLE response too short: {} bytes", header.len()));
        }
        let status = header[7];
        if status != STATUS_SUCCESS {
            return Err(format!(
                "BLE command failed: status=0x{:02x} (txn=0x{:02x})",
                status, header[0]
            ));
        }
        let data_len = header[1] as usize;
        if data_len == 0 {
            return Ok(vec![]);
        }
        // Wait for data continuation notification
        let data_pkt = self.read_notification()?;
        Ok(data_pkt[..data_len.min(data_pkt.len())].to_vec())
    }

    // ── Public API (mirrors RazerDevice) ─────────────────────────────────────

    /// Check the BluetoothLEDevice.ConnectionStatus property, with tolerance
    /// for Windows' momentary "Disconnected" flaps right after connect. Only
    /// returns false after N consecutive failures; any single success resets
    /// the counter. This is a cheap property read, not a GATT operation.
    pub fn is_connected(&mut self) -> bool {
        const DISCONNECT_THRESHOLD: u32 = 3;
        let status_ok = self
            .device
            .ConnectionStatus()
            .map(|s| s == BluetoothConnectionStatus::Connected)
            .unwrap_or(false);
        if status_ok {
            self.disconnect_count = 0;
            true
        } else {
            self.disconnect_count += 1;
            if self.disconnect_count >= DISCONNECT_THRESHOLD {
                false
            } else {
                eprintln!(
                    "joro-ble: transient disconnect ({}/{})",
                    self.disconnect_count, DISCONNECT_THRESHOLD
                );
                true // still consider connected
            }
        }
    }

    pub fn get_firmware(&mut self) -> Result<String, String> {
        let data = self.send_get(0x00, 0x81, 0, 0)?;
        if data.len() >= 4 {
            Ok(format!("v{}.{}.{}.{}", data[0], data[1], data[2], data[3]))
        } else if data.len() >= 2 {
            Ok(format!("v{}.{}", data[0], data[1]))
        } else {
            Err("get_firmware: response too short".into())
        }
    }

    #[allow(dead_code)]
    pub fn get_brightness(&mut self) -> Result<u8, String> {
        let data = self.send_get(0x10, 0x85, 0, 0)?;
        data.first()
            .copied()
            .ok_or_else(|| "get_brightness: no data".into())
    }

    /// Read battery level. PRIMARY: standard GATT Battery Level (0x2A19)
    /// with an UNCACHED read — proven to match the wired live register
    /// (both 45% on 2026-07-07). The Razer Protocol30 `0x07:0x80` register
    /// over BLE serves a months-stale snapshot (sat at 76% from May through
    /// July, unmoved by charging/draining/transport cycles) and is only a
    /// last-resort fallback here. Returns Err on transport failure; a
    /// genuine 0% is Ok(0).
    pub fn get_battery_percent(&mut self) -> Result<u8, String> {
        if let Some(ch) = self.char_battery.as_ref() {
            // MUST be Uncached: the default Cached mode returns Windows'
            // stale copy — the original source of the "frozen at 100%" bug.
            let res = ch
                .ReadValueWithCacheModeAsync(BluetoothCacheMode::Uncached)
                .and_then(|op| op.get())
                .map_err(|e| format!("battery 0x2A19 read: {e}"))?;
            let status = res.Status().map_err(|e| e.to_string())?;
            if status != GattCommunicationStatus::Success {
                return Err(format!("battery 0x2A19 read status: {status:?}"));
            }
            let buf = res.Value().map_err(|e| e.to_string())?;
            let reader = DataReader::FromBuffer(&buf).map_err(|e| e.to_string())?;
            let len = reader.UnconsumedBufferLength().unwrap_or(0) as usize;
            let mut data = vec![0u8; len];
            reader.ReadBytes(&mut data).map_err(|e| e.to_string())?;
            let pct = *data.first().ok_or("battery 0x2A19: empty read")?;
            eprintln!("joro-ble: battery (GATT 0x2A19 uncached) -> {pct}%");
            return Ok(pct.min(100));
        }
        // FALLBACK (Battery Service missing — unexpected): Protocol30
        // 0x07:80 arg[1]. Known-stale over BLE; better than nothing.
        let data = self.send_get(0x07, 0x80, 0, 0)?;
        let hex: String = data.iter().take(8).map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
        let raw = *data.get(1).ok_or("get_battery: response too short")?;
        let pct = (((raw as u32) * 100 + 127) / 255).min(100) as u8;
        eprintln!("joro-ble: battery FALLBACK (Protocol30 0x07:80, may be stale) raw=[{hex}] -> {pct}%");
        Ok(pct)
    }

    /// Diagnostic raw GET for register sweeps (diag-battsweep CLI). Exposes
    /// the private send_get without widening the normal API surface.
    pub fn diag_get(&mut self, class: u8, cmd: u8, sub1: u8, sub2: u8) -> Result<Vec<u8>, String> {
        self.send_get(class, cmd, sub1, sub2)
    }

    pub fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        self.send_set(0x10, 0x05, 0x01, 0x00, &[level])
    }

    pub fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        self.send_set(0x10, 0x03, 0x01, 0x00, &[0x01, 0x00, 0x00, 0x01, r, g, b])
    }

    pub fn set_breathing_single(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        self.send_set(
            0x10, 0x03, 0x01, 0x00,
            &[0x02, 0x01, 0x00, 0x01, r, g, b],
        )
    }

    #[allow(dead_code)]
    pub fn set_breathing_dual(
        &mut self,
        r1: u8, g1: u8, b1: u8,
        r2: u8, g2: u8, b2: u8,
    ) -> Result<(), String> {
        self.send_set(
            0x10, 0x03, 0x01, 0x00,
            &[0x02, 0x02, 0x00, 0x02, r1, g1, b1, r2, g2, b2],
        )
    }

    pub fn set_spectrum(&mut self) -> Result<(), String> {
        self.send_set(0x10, 0x03, 0x01, 0x00, &[0x03, 0x00, 0x00, 0x00])
    }

    #[allow(dead_code)]
    pub fn set_off(&mut self) -> Result<(), String> {
        self.send_set(0x10, 0x03, 0x01, 0x00, &[0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
    }

    /// BLE does not support firmware keymap remaps. No-op.
    pub fn set_keymap_entry(&mut self, _index: u8, _usage: u8) -> Result<(), String> {
        Ok(())
    }

    /// Set Joro's firmware-level device mode. This is the fn↔mm toggle Synapse
    /// exposes as "Function Keys Primary". In Fn mode, F5-F12 emit plain
    /// scancodes; in MM mode, they emit consumer usages (mute/vol/brightness).
    /// F4 also toggles. F1/F2/F3 are BLE slot keys and are unaffected.
    ///
    /// Protocol30: SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0]
    /// mode 0x03 = driver/Fn primary; mode 0x00 = normal/MM primary.
    /// See memory/project_fnmm_toggle_solved.md for the reverse-engineering
    /// history.
    pub fn set_device_mode(&mut self, fn_primary: bool) -> Result<(), String> {
        let mode_byte = if fn_primary { 0x03 } else { 0x00 };
        self.send_set(0x01, 0x02, 0x00, 0x00, &[mode_byte, 0x00])
    }

    /// Idle/sleep timeout: Protocol30 SET class=0x07 cmd=0x83 (the cmd
    /// Synapse uses for Joro — NOT the daemon's old 0x03). `data` is the
    /// raw idle parameter (semantics empirical: try 00 00 = disable/never,
    /// or a u16 seconds value). Reversible runtime command, zero flash.
    pub fn set_idle_raw(&mut self, data: &[u8]) -> Result<(), String> {
        self.send_set(0x07, 0x83, 0x00, 0x00, data)
    }

    /// Read current idle/power state: GET class=0x07 cmd=0x84.
    pub fn get_idle_raw(&mut self) -> Result<Vec<u8>, String> {
        self.send_get(0x07, 0x84, 0x00, 0x00)
    }

    /// Read the current firmware mode. Returns true if Fn-primary (mode 3),
    /// false if MM-primary (mode 0).
    pub fn get_device_mode(&mut self) -> Result<bool, String> {
        let data = self.send_get(0x01, 0x82, 0x00, 0x00)?;
        if data.is_empty() {
            return Err("get_device_mode: empty response".into());
        }
        Ok(data[0] == 0x03)
    }
}

impl Drop for BleDevice {
    fn drop(&mut self) {
        eprintln!("joro-ble: Drop — releasing GATT session");
        // Unregister the ValueChanged handlers so they stop firing
        let _ = self.char_rx.RemoveValueChanged(self.notif_token);
        if let (Some(ch), Some(tok)) = (self.char_battery.as_ref(), self.battery_notif_token) {
            let _ = ch.RemoveValueChanged(tok);
        }
        // Close the device handle so Windows releases the BLE link.
        // Without this, the keyboard can stay invisible to scans after disconnect.
        if let Ok(closable) = self.device.cast::<IClosable>() {
            let _ = closable.Close();
        }
        // _session drops automatically, releasing MaintainConnection
    }
}

impl crate::device::JoroDevice for BleDevice {
    fn is_connected(&mut self) -> bool { BleDevice::is_connected(self) }
    fn get_firmware(&mut self) -> Result<String, String> { BleDevice::get_firmware(self) }
    fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        BleDevice::set_static_color(self, r, g, b)
    }
    fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        BleDevice::set_brightness(self, level)
    }
    fn set_effect_breathing(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        BleDevice::set_breathing_single(self, r, g, b)
    }
    fn set_effect_spectrum(&mut self) -> Result<(), String> {
        BleDevice::set_spectrum(self)
    }
    fn set_keymap_entry(&mut self, index: u8, usage: u8) -> Result<(), String> {
        BleDevice::set_keymap_entry(self, index, usage)
    }
    fn get_battery_percent(&mut self) -> Result<u8, String> {
        BleDevice::get_battery_percent(self)
    }
    fn transport_name(&self) -> &'static str { "BLE" }
    fn set_device_mode(&mut self, fn_primary: bool) -> Result<(), String> {
        BleDevice::set_device_mode(self, fn_primary)
    }
}

// ── Free functions ──────────────────────────────────────────────────────────

/// Run a BluetoothLEAdvertisementWatcher for `timeout`, watching for any
/// advertisement whose LocalName equals "Joro". Returns the first matching
/// Bluetooth address (u64) seen, or None if none arrived in time.
fn scan_for_joro(timeout: Duration) -> Option<u64> {
    eprintln!("joro-ble: starting advertisement watcher...");

    let watcher = BluetoothLEAdvertisementWatcher::new().ok()?;
    watcher
        .SetScanningMode(BluetoothLEScanningMode::Active)
        .ok()?;

    let (tx, rx) = mpsc::channel::<u64>();
    let tx = Arc::new(Mutex::new(Some(tx)));

    let tx_for_handler = tx.clone();
    let handler = TypedEventHandler::<
        BluetoothLEAdvertisementWatcher,
        BluetoothLEAdvertisementReceivedEventArgs,
    >::new(move |_sender, args| {
        if let Some(args) = args.as_ref() {
            if let Ok(adv) = args.Advertisement() {
                if let Ok(name) = adv.LocalName() {
                    let name_str = name.to_string_lossy();
                    if name_str == "Joro" {
                        if let Ok(addr) = args.BluetoothAddress() {
                            // Take the sender so we only send once
                            if let Ok(mut guard) = tx_for_handler.lock() {
                                if let Some(sender) = guard.take() {
                                    let _ = sender.send(addr);
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    });

    let token = watcher.Received(&handler).ok()?;
    watcher.Start().ok()?;

    let result = rx.recv_timeout(timeout).ok();

    let _ = watcher.Stop();
    let _ = watcher.RemoveReceived(token);

    if result.is_none() {
        eprintln!("joro-ble: no Joro advertisements received in {timeout:?}");
    }
    result
}

/// Enumerate paired BLE devices via DeviceInformation and return a
/// BluetoothLEDevice named "Joro" if one is paired. Works even when the
/// device isn't currently advertising (e.g. Windows already has it connected).
fn find_paired_joro() -> WinResult<Option<BluetoothLEDevice>> {
    let selector = BluetoothLEDevice::GetDeviceSelectorFromPairingState(true)?;
    let devices = DeviceInformation::FindAllAsyncAqsFilter(&selector)?.get()?;
    let size = devices.Size()?;
    eprintln!("joro-ble: enumerated {} paired BLE device(s)", size);
    for i in 0..size {
        let info = devices.GetAt(i)?;
        let name = info.Name()?.to_string_lossy();
        if name == "Joro" {
            let id = info.Id()?;
            eprintln!("joro-ble:   paired '{}' at {}", name, id.to_string_lossy());
            match BluetoothLEDevice::FromIdAsync(&id)?.get() {
                Ok(dev) => return Ok(Some(dev)),
                Err(e) => {
                    eprintln!("joro-ble: FromIdAsync failed for paired device: {e}");
                    continue;
                }
            }
        }
    }
    Ok(None)
}

/// Diagnostic (diag-gatt CLI): enumerate the ENTIRE GATT database, read every
/// readable characteristic once, subscribe to every notify/indicate-capable
/// one, and listen. Hunts for a live push-telemetry channel (battery) — the
/// dongle's `09 31` heartbeat proved the keyboard measures battery live and
/// PUSHES it; every host-queried register over BLE serves stale snapshots
/// (0x07:80 frozen at 76% since May while wired ground truth said 45%).
/// Run with the daemon STOPPED.
pub fn diag_gatt_probe(listen_secs: u64) -> Result<(), String> {
    let device = find_paired_joro()
        .map_err(|e| format!("enumerate: {e}"))?
        .ok_or("no paired Joro found")?;
    let dev_id = device.BluetoothDeviceId().map_err(|e| e.to_string())?;
    let session = GattSession::FromDeviceIdAsync(&dev_id)
        .and_then(|op| op.get())
        .map_err(|e| e.to_string())?;
    session.SetMaintainConnection(true).map_err(|e| e.to_string())?;

    let svcs = device
        .GetGattServicesAsync()
        .and_then(|op| op.get())
        .map_err(|e| e.to_string())?;
    let services = svcs.Services().map_err(|e| e.to_string())?;
    let n = services.Size().map_err(|e| e.to_string())?;
    println!("diag-gatt: {n} services");
    // Keep subscriptions alive for the listen window.
    let mut _subs: Vec<(GattCharacteristic, EventRegistrationToken)> = Vec::new();

    for i in 0..n {
        let svc = services.GetAt(i).map_err(|e| e.to_string())?;
        let su = svc.Uuid().map_err(|e| e.to_string())?;
        println!("service {su:?}");
        let chars_result = match svc.GetCharacteristicsAsync().and_then(|op| op.get()) {
            Ok(c) => c,
            Err(e) => {
                println!("  (characteristics error: {e})");
                continue;
            }
        };
        let chars = match chars_result.Characteristics() {
            Ok(c) => c,
            Err(e) => {
                println!("  (characteristics list error: {e})");
                continue;
            }
        };
        let cn = chars.Size().unwrap_or(0);
        for j in 0..cn {
            let Ok(ch) = chars.GetAt(j) else { continue };
            let cu = ch.Uuid().map(|u| format!("{u:?}")).unwrap_or_default();
            let props = ch
                .CharacteristicProperties()
                .unwrap_or(GattCharacteristicProperties::None);
            println!("  char {cu} props=0x{:x}", props.0);

            if (props.0 & GattCharacteristicProperties::Read.0) != 0 {
                match ch.ReadValueAsync().and_then(|op| op.get()) {
                    Ok(res) if res.Status().map_err(|e| e.to_string())? == GattCommunicationStatus::Success => {
                        if let Ok(buf) = res.Value() {
                            if let Ok(reader) = DataReader::FromBuffer(&buf) {
                                let len = reader.UnconsumedBufferLength().unwrap_or(0) as usize;
                                let mut data = vec![0u8; len];
                                if reader.ReadBytes(&mut data).is_ok() {
                                    let hex: String = data
                                        .iter()
                                        .take(32)
                                        .map(|b| format!("{b:02x}"))
                                        .collect::<Vec<_>>()
                                        .join(" ");
                                    println!("    read ({len} B): [{hex}]");
                                }
                            }
                        }
                    }
                    Ok(res) => println!("    read status: {:?}", res.Status()),
                    Err(e) => println!("    read error: {e}"),
                }
            }

            let can_notify = (props.0 & GattCharacteristicProperties::Notify.0) != 0;
            let can_indicate = (props.0 & GattCharacteristicProperties::Indicate.0) != 0;
            if can_notify || can_indicate {
                let cccd = if can_notify {
                    GattClientCharacteristicConfigurationDescriptorValue::Notify
                } else {
                    GattClientCharacteristicConfigurationDescriptorValue::Indicate
                };
                match ch
                    .WriteClientCharacteristicConfigurationDescriptorAsync(cccd)
                    .and_then(|op| op.get())
                {
                    Ok(GattCommunicationStatus::Success) => {
                        let label = format!("{su:?}/{cu}");
                        let handler = TypedEventHandler::<
                            GattCharacteristic,
                            GattValueChangedEventArgs,
                        >::new(move |_sender, args| {
                            if let Some(args) = args.as_ref() {
                                if let Ok(buf) = args.CharacteristicValue() {
                                    if let Ok(reader) = DataReader::FromBuffer(&buf) {
                                        let len =
                                            reader.UnconsumedBufferLength().unwrap_or(0) as usize;
                                        let mut data = vec![0u8; len];
                                        if reader.ReadBytes(&mut data).is_ok() {
                                            let hex: String = data
                                                .iter()
                                                .take(32)
                                                .map(|b| format!("{b:02x}"))
                                                .collect::<Vec<_>>()
                                                .join(" ");
                                            println!("NOTIFY {label} ({len} B): [{hex}]");
                                        }
                                    }
                                }
                            }
                            Ok(())
                        });
                        if let Ok(tok) = ch.ValueChanged(&handler) {
                            println!("    subscribed ({})", if can_notify { "notify" } else { "indicate" });
                            _subs.push((ch, tok));
                        }
                    }
                    Ok(other) => println!("    subscribe status: {other:?}"),
                    Err(e) => println!("    subscribe error: {e}"),
                }
            }
        }
    }

    println!("diag-gatt: listening {listen_secs}s — type on the keyboard, plug/unplug the cable...");
    std::thread::sleep(Duration::from_secs(listen_secs));
    for (ch, tok) in _subs {
        let _ = ch.RemoveValueChanged(tok);
    }
    println!("diag-gatt: done");
    Ok(())
}

/// Connect to a Joro at the given Bluetooth address (used for the
/// advertisement-scan path). Resolves the address to a BluetoothLEDevice
/// and delegates to `connect_from_device`.
fn connect_from_address(addr: u64) -> WinResult<BleDevice> {
    let device = BluetoothLEDevice::FromBluetoothAddressAsync(addr)?.get()?;
    eprintln!("joro-ble: BluetoothLEDevice acquired");
    connect_from_device(device)
}

/// Set up GATT session with MaintainConnection=true, discover the Razer
/// service, find char_tx/char_rx, subscribe to notifications, and return a
/// ready BleDevice. The BluetoothLEDevice can come from either the paired
/// enumeration path or the advertisement scan path.
fn connect_from_device(device: BluetoothLEDevice) -> WinResult<BleDevice> {
    let dev_id = device.BluetoothDeviceId()?;
    let session = GattSession::FromDeviceIdAsync(&dev_id)?.get()?;
    session.SetMaintainConnection(true)?;
    eprintln!("joro-ble: GattSession with MaintainConnection=true");

    // Find the Razer custom service
    let svcs_result = device.GetGattServicesForUuidAsync(RAZER_SERVICE_UUID)?.get()?;
    let svcs_status = svcs_result.Status()?;
    if svcs_status != GattCommunicationStatus::Success {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            format!("service discovery status: {:?}", svcs_status),
        ));
    }
    let services = svcs_result.Services()?;
    if services.Size()? == 0 {
        return Err(WinError::new(windows::core::HRESULT(0), "Razer service not found"));
    }
    let service: GattDeviceService = services.GetAt(0)?;
    eprintln!("joro-ble: Razer service found");

    // Find char_tx (1524)
    let char_tx = find_char(&service, CHAR_TX_UUID, "TX (1524)")?;
    let char_rx = find_char(&service, CHAR_RX_UUID, "RX (1525)")?;
    eprintln!("joro-ble: TX/RX characteristics found");

    // Subscribe to notifications on char_rx
    let cccd_result = char_rx
        .WriteClientCharacteristicConfigurationDescriptorAsync(
            GattClientCharacteristicConfigurationDescriptorValue::Notify,
        )?
        .get()?;
    if cccd_result != GattCommunicationStatus::Success {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            format!("CCCD write status: {:?}", cccd_result),
        ));
    }
    eprintln!("joro-ble: notifications subscribed");

    // Wire up the ValueChanged event to a channel
    let (notif_tx, notif_rx) = mpsc::channel::<Vec<u8>>();
    let notif_tx = Arc::new(Mutex::new(notif_tx));

    let tx_for_handler = notif_tx.clone();
    let trace = std::env::var("JORO_BLE_TRACE").is_ok();
    let handler = TypedEventHandler::<GattCharacteristic, GattValueChangedEventArgs>::new(
        move |_sender, args| {
            if let Some(args) = args.as_ref() {
                if let Ok(buf) = args.CharacteristicValue() {
                    if let Ok(reader) = DataReader::FromBuffer(&buf) {
                        let len = reader.UnconsumedBufferLength().unwrap_or(0) as usize;
                        let mut data = vec![0u8; len];
                        if reader.ReadBytes(&mut data).is_ok() {
                            if trace {
                                let hex: String = data
                                    .iter()
                                    .map(|b| format!("{:02x}", b))
                                    .collect::<Vec<_>>()
                                    .join(" ");
                                eprintln!("joro-ble-notif ({} bytes): {}", data.len(), hex);
                            }
                            if let Ok(guard) = tx_for_handler.lock() {
                                let _ = guard.send(data);
                            }
                        }
                    }
                }
            }
            Ok(())
        },
    );
    let notif_token = char_rx.ValueChanged(&handler)?;

    // Drain any unsolicited notifications the keyboard sends on connect
    std::thread::sleep(Duration::from_millis(500));
    while notif_rx.try_recv().is_ok() {}

    // Battery Level char (0x2A19) — primary battery source over BLE (see
    // the const comment). Subscribe to change notifications so the UI
    // updates the moment the firmware publishes a new level, independent
    // of the 60 s poll.
    let char_battery = find_battery_level_char(&device).ok();
    let battery_notif_token = char_battery.as_ref().and_then(|ch| {
        let cccd_ok = ch
            .WriteClientCharacteristicConfigurationDescriptorAsync(
                GattClientCharacteristicConfigurationDescriptorValue::Notify,
            )
            .and_then(|op| op.get())
            .map(|s| s == GattCommunicationStatus::Success)
            .unwrap_or(false);
        if !cccd_ok {
            eprintln!("joro-ble: 0x2A19 notify subscribe failed — 60s polling still covers it");
            return None;
        }
        let handler = TypedEventHandler::<GattCharacteristic, GattValueChangedEventArgs>::new(
            move |_sender, args| {
                if let Some(args) = args.as_ref() {
                    if let Ok(buf) = args.CharacteristicValue() {
                        if let Ok(reader) = DataReader::FromBuffer(&buf) {
                            let len = reader.UnconsumedBufferLength().unwrap_or(0) as usize;
                            let mut data = vec![0u8; len];
                            if reader.ReadBytes(&mut data).is_ok() {
                                if let Some(&pct) = data.first() {
                                    eprintln!("joro-ble: battery notify (0x2A19) -> {pct}%");
                                    crate::post_user_event(crate::UserEvent::BatteryObserved(
                                        pct.min(100),
                                    ));
                                }
                            }
                        }
                    }
                }
                Ok(())
            },
        );
        ch.ValueChanged(&handler).ok()
    });
    if char_battery.is_some() {
        eprintln!(
            "joro-ble: battery source = GATT 0x2A19 (uncached reads{})",
            if battery_notif_token.is_some() { " + notify" } else { "" }
        );
    } else {
        eprintln!("joro-ble: Battery Service NOT found — falling back to stale Protocol30 register");
    }

    Ok(BleDevice {
        device,
        _session: session,
        char_tx,
        char_rx,
        char_battery,
        battery_notif_token,
        notif_rx,
        notif_token,
        txn_id: 0,
        disconnect_count: 0,
    })
}

/// Discover the standard Battery Service (0x180F) and return its Battery
/// Level characteristic (0x2A19), if present.
fn find_battery_level_char(device: &BluetoothLEDevice) -> WinResult<GattCharacteristic> {
    let svcs = device
        .GetGattServicesForUuidAsync(BATTERY_SERVICE_UUID)?
        .get()?;
    if svcs.Status()? != GattCommunicationStatus::Success {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            "battery service not found",
        ));
    }
    let services = svcs.Services()?;
    if services.Size()? == 0 {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            "battery service empty",
        ));
    }
    let svc: GattDeviceService = services.GetAt(0)?;
    find_char(&svc, BATTERY_LEVEL_UUID, "Battery Level (0x2A19)")
}

fn find_char(
    service: &GattDeviceService,
    uuid: GUID,
    label: &str,
) -> WinResult<GattCharacteristic> {
    let result = service.GetCharacteristicsForUuidAsync(uuid)?.get()?;
    let status = result.Status()?;
    if status != GattCommunicationStatus::Success {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            format!("char {} status: {:?}", label, status),
        ));
    }
    let chars = result.Characteristics()?;
    if chars.Size()? == 0 {
        return Err(WinError::new(
            windows::core::HRESULT(0),
            format!("char {} not found", label),
        ));
    }
    Ok(chars.GetAt(0)?)
}

/// Convert a byte slice to a WinRT IBuffer via DataWriter.
fn vec_to_buffer(data: &[u8]) -> WinResult<windows::Storage::Streams::IBuffer> {
    let writer = DataWriter::new()?;
    writer.WriteBytes(data)?;
    writer.DetachBuffer()
}
