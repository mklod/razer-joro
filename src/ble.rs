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
    GattCharacteristic, GattClientCharacteristicConfigurationDescriptorValue,
    GattCommunicationStatus, GattDeviceService, GattSession, GattValueChangedEventArgs,
    GattWriteOption,
};
use windows::Devices::Bluetooth::{BluetoothConnectionStatus, BluetoothLEDevice};
use windows::Devices::Enumeration::DeviceInformation;
use windows::Foundation::{EventRegistrationToken, IClosable, TypedEventHandler};
use windows::Storage::Streams::{DataReader, DataWriter};

// ── Constants ────────────────────────────────────────────────────────────────

const RAZER_SERVICE_UUID: GUID = GUID::from_u128(0x52401523_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_TX_UUID: GUID = GUID::from_u128(0x52401524_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_RX_UUID: GUID = GUID::from_u128(0x52401525_f97c_7f90_0e7f_6c6f4e36db1c);

// Standard BLE Battery Service (org.bluetooth.service.battery_service)
// and its Battery Level characteristic (org.bluetooth.characteristic.battery_level).
// These are SIG-assigned UUIDs in the 16-bit range, expanded to 128-bit form.
// The Battery Level characteristic returns a single byte 0-100 directly.
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
    /// Standard BLE Battery Level characteristic (org.bluetooth 0x2A19).
    /// Optional because not every transport/firmware exposes it.
    char_battery: Option<GattCharacteristic>,
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
        while self.notif_rx.try_recv().is_ok() {}
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

    /// Read battery level. Uses the standard BLE Battery Service (0x180F /
    /// 0x2A19) if the keyboard exposes it — this returns 0-100 directly and
    /// matches what Synapse/OS shows. Falls back to Razer Protocol30
    /// `class=0x07 cmd=0x80` if the standard service isn't available (the
    /// Protocol30 encoding is opaque and gives wrong values; we only use it
    /// as a last-resort fallback).
    pub fn get_battery_percent(&mut self) -> Result<u8, String> {
        if let Some(ref char_bat) = self.char_battery {
            let result = char_bat
                .ReadValueAsync()
                .map_err(|e| format!("battery ReadValue: {e}"))?
                .get()
                .map_err(|e| format!("battery ReadValue get: {e}"))?;
            if result.Status().map_err(|e| format!("battery status: {e}"))?
                != GattCommunicationStatus::Success
            {
                return Err("battery read: GATT communication failure".into());
            }
            let buf = result
                .Value()
                .map_err(|e| format!("battery Value: {e}"))?;
            let reader = DataReader::FromBuffer(&buf)
                .map_err(|e| format!("battery DataReader: {e}"))?;
            let len = reader
                .UnconsumedBufferLength()
                .map_err(|e| format!("battery len: {e}"))? as usize;
            if len == 0 {
                return Err("battery read: empty response".into());
            }
            let mut data = vec![0u8; len];
            reader
                .ReadBytes(&mut data)
                .map_err(|e| format!("battery ReadBytes: {e}"))?;
            let pct = data[0].min(100);
            eprintln!("joro-ble: battery (std BLE svc) = {pct}%");
            return Ok(pct);
        }

        // Fallback: Razer Protocol30 (encoding is opaque on BLE; may be wrong)
        let data = self.send_get(0x07, 0x80, 0, 0)?;
        let hex: String = data.iter().take(8).map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
        eprintln!("joro-ble: battery fallback Protocol30 raw = [{hex}]");
        let raw = *data.get(1).ok_or("get_battery: response too short")?;
        let pct = ((raw as u32) * 100 / 255) as u8;
        Ok(pct)
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
        // Unregister the ValueChanged handler so it stops firing
        let _ = self.char_rx.RemoveValueChanged(self.notif_token);
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

    // Optional: standard BLE Battery Service (0x180F) with Battery Level
    // characteristic (0x2A19). If present, we'll use it for battery reads
    // instead of Razer Protocol30 — it returns a clean 0-100 byte directly.
    let char_battery = find_battery_level_char(&device).ok();
    if char_battery.is_some() {
        eprintln!("joro-ble: standard BLE Battery Service found");
    } else {
        eprintln!("joro-ble: standard BLE Battery Service NOT found — falling back to Protocol30");
    }

    Ok(BleDevice {
        device,
        _session: session,
        char_tx,
        char_rx,
        char_battery,
        notif_rx,
        notif_token,
        txn_id: 0,
        disconnect_count: 0,
    })
}

/// Discover the standard BLE Battery Service (0x180F) and return its
/// Battery Level characteristic (0x2A19), if present.
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
