// src/ble.rs — BLE transport for Razer Joro keyboard
// Last modified: 2026-04-10--1730
//
// Uses Protocol30 over BLE GATT:
//   GET: single 8-byte ATT Write Request to char 1524
//   SET: split write — 8-byte header then data payload as separate ATT writes
//   Responses arrive as notifications on char 1525
//
// TODO: Refactor USB + BLE behind a common JoroDevice trait (Option A)

use btleplug::api::{
    Central, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType,
};
use btleplug::platform::{Manager, Peripheral};
use futures::stream::StreamExt;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::runtime::Runtime;
use tokio::sync::mpsc;
use uuid::Uuid;

// ── Constants ────────────────────────────────────────────────────────────────

const RAZER_SERVICE_UUID: Uuid =
    Uuid::from_u128(0x52401523_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_TX_UUID: Uuid =
    Uuid::from_u128(0x52401524_f97c_7f90_0e7f_6c6f4e36db1c);
const CHAR_RX_UUID: Uuid =
    Uuid::from_u128(0x52401525_f97c_7f90_0e7f_6c6f4e36db1c);

const SCAN_TIMEOUT: Duration = Duration::from_secs(8);
const WRITE_DELAY: Duration = Duration::from_millis(150);
const RESPONSE_TIMEOUT: Duration = Duration::from_millis(2000);

pub const STATUS_SUCCESS: u8 = 0x02;
pub const STATUS_FAILURE: u8 = 0x03;
pub const STATUS_NOT_SUPPORTED: u8 = 0x05;

// ── BleDevice ────────────────────────────────────────────────────────────────

pub struct BleDevice {
    rt: Runtime,
    peripheral: Peripheral,
    char_tx: Characteristic,
    rx_receiver: Arc<Mutex<mpsc::Receiver<Vec<u8>>>>,
    txn_id: u8,
}

impl BleDevice {
    /// Scan for a Joro keyboard over BLE and connect.
    /// Returns None if no Joro found within timeout.
    pub fn open() -> Option<Self> {
        let rt = Runtime::new().ok()?;
        let (peripheral, char_tx, rx_receiver) = rt.block_on(Self::connect_async())?;
        Some(BleDevice {
            rt,
            peripheral,
            char_tx,
            rx_receiver,
            txn_id: 0,
        })
    }

    /// Async connection logic. Returns the components needed by BleDevice.
    async fn connect_async() -> Option<(Peripheral, Characteristic, Arc<Mutex<mpsc::Receiver<Vec<u8>>>>)> {
        let manager = Manager::new().await.ok()?;
        let adapters = manager.adapters().await.ok()?;
        let adapter = adapters.into_iter().next()?;

        // Check already-known peripherals first (handles paired+connected devices)
        eprintln!("joro-ble: checking known peripherals...");
        let mut joro = None;

        if let Ok(peripherals) = adapter.peripherals().await {
            for p in &peripherals {
                if let Ok(Some(props)) = p.properties().await {
                    if let Some(ref name) = props.local_name {
                        eprintln!("joro-ble:   known: {} ({})", name, props.address);
                        if name == "Joro" {
                            joro = Some(p.clone());
                            break;
                        }
                    }
                }
            }
        }

        // If not found in known devices, try scanning
        if joro.is_none() {
            eprintln!("joro-ble: scanning for Joro...");
            adapter.start_scan(ScanFilter::default()).await.ok()?;
            tokio::time::sleep(SCAN_TIMEOUT).await;
            adapter.stop_scan().await.ok()?;

            if let Ok(peripherals) = adapter.peripherals().await {
                for p in peripherals {
                    if let Ok(Some(props)) = p.properties().await {
                        if let Some(ref name) = props.local_name {
                            eprintln!("joro-ble:   scanned: {} ({})", name, props.address);
                            if name == "Joro" {
                                joro = Some(p);
                                break;
                            }
                        }
                    }
                }
            }
        }

        let peripheral = joro?;
        eprintln!("joro-ble: found Joro, connecting...");

        if let Err(e) = peripheral.connect().await {
            eprintln!("joro-ble: connect FAILED: {e}");
            return None;
        }
        eprintln!("joro-ble: connected, discovering services...");

        if let Err(e) = peripheral.discover_services().await {
            eprintln!("joro-ble: service discovery FAILED: {e}");
            return None;
        }

        let chars = peripheral.characteristics();
        eprintln!("joro-ble: found {} characteristics", chars.len());
        for c in &chars {
            eprintln!("  char: {} props={:?}", c.uuid, c.properties);
        }

        let char_tx = match chars.iter().find(|c| c.uuid == CHAR_TX_UUID) {
            Some(c) => c.clone(),
            None => {
                eprintln!("joro-ble: TX characteristic (1524) NOT FOUND");
                return None;
            }
        };
        let char_rx = match chars.iter().find(|c| c.uuid == CHAR_RX_UUID) {
            Some(c) => c,
            None => {
                eprintln!("joro-ble: RX characteristic (1525) NOT FOUND");
                return None;
            }
        };

        if let Err(e) = peripheral.subscribe(char_rx).await {
            eprintln!("joro-ble: subscribe FAILED: {e}");
            return None;
        }
        eprintln!("joro-ble: subscribed to notifications");

        // Notification receiver channel
        let (tx, rx) = mpsc::channel::<Vec<u8>>(32);
        let mut notif_stream = peripheral.notifications().await.ok()?;

        tokio::spawn(async move {
            while let Some(notif) = notif_stream.next().await {
                if notif.uuid == CHAR_RX_UUID {
                    let _ = tx.send(notif.value).await;
                }
            }
        });

        // Drain unsolicited notifications (keyboard sends status on connect)
        let rx = Arc::new(Mutex::new(rx));
        tokio::time::sleep(Duration::from_millis(500)).await;
        {
            let mut guard = rx.lock().unwrap();
            while guard.try_recv().is_ok() {}
        }

        Some((peripheral, char_tx, rx))
    }

    fn next_txn(&mut self) -> u8 {
        self.txn_id = self.txn_id.wrapping_add(1);
        self.txn_id
    }

    /// Send a GET command (8-byte header, no data). Returns response data bytes.
    fn send_get(&mut self, class: u8, cmd: u8, sub1: u8, sub2: u8) -> Result<Vec<u8>, String> {
        let txn = self.next_txn();
        let header = [txn, 0, 0, 0, class, cmd, sub1, sub2];

        self.drain_notifications();
        self.write_bytes(&header)?;
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

        // Split write: header first, then data as separate ATT Write Request
        self.write_bytes(&header)?;
        std::thread::sleep(WRITE_DELAY);
        self.write_bytes(data)?;

        let _response = self.read_response(txn)?;
        // For SET, we just check status (response data is empty)
        Ok(())
    }

    fn write_bytes(&self, data: &[u8]) -> Result<(), String> {
        self.rt
            .block_on(
                self.peripheral
                    .write(&self.char_tx, data, WriteType::WithResponse),
            )
            .map_err(|e| format!("BLE write failed: {e}"))
    }

    fn read_response(&self, _expected_txn: u8) -> Result<Vec<u8>, String> {
        let mut guard = self.rx_receiver.lock().unwrap();

        // Wait for header notification
        let header = self
            .rt
            .block_on(async {
                tokio::time::timeout(RESPONSE_TIMEOUT, guard.recv()).await
            })
            .map_err(|_| "BLE response timeout".to_string())?
            .ok_or("BLE notification channel closed")?;

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
        let data_pkt = self
            .rt
            .block_on(async {
                tokio::time::timeout(RESPONSE_TIMEOUT, guard.recv()).await
            })
            .map_err(|_| "BLE data continuation timeout".to_string())?
            .ok_or("BLE notification channel closed")?;

        Ok(data_pkt[..data_len.min(data_pkt.len())].to_vec())
    }

    fn drain_notifications(&self) {
        let mut guard = self.rx_receiver.lock().unwrap();
        while guard.try_recv().is_ok() {}
    }

    // ── Public API (mirrors RazerDevice) ─────────────────────────────────────

    pub fn is_connected(&self) -> bool {
        self.rt
            .block_on(self.peripheral.is_connected())
            .unwrap_or(false)
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

    pub fn get_brightness(&mut self) -> Result<u8, String> {
        let data = self.send_get(0x10, 0x85, 0, 0)?;
        data.first()
            .copied()
            .ok_or_else(|| "get_brightness: no data".into())
    }

    pub fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        self.send_set(0x10, 0x05, 0x01, 0x00, &[level])
    }

    pub fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        // Effect data: [enabled, 0, 0, effect_type=static, R, G, B]
        self.send_set(0x10, 0x03, 0x01, 0x00, &[0x01, 0x00, 0x00, 0x01, r, g, b])
    }

    pub fn set_breathing_single(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        self.send_set(
            0x10, 0x03, 0x01, 0x00,
            &[0x02, 0x01, 0x00, 0x01, r, g, b],
        )
    }

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

    pub fn set_off(&mut self) -> Result<(), String> {
        // byte0=0x00 disables lighting
        self.send_set(0x10, 0x03, 0x01, 0x00, &[0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
    }

    /// Note: BLE does not support firmware keymap remaps (class 0x02 NOT_SUPPORTED).
    /// All remaps are host-side via WH_KEYBOARD_LL. This is a no-op.
    pub fn set_keymap_entry(&self, _index: u8, _usage: u8) -> Result<(), String> {
        // Intentionally no-op: keymaps not available over BLE
        Ok(())
    }
}
