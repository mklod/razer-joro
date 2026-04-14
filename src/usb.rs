// src/usb.rs — Razer packet builder + USB device communication
// Last modified: 2026-04-13--2119

use rusb::{Context, DeviceHandle, UsbContext};
use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

// ── Constants ────────────────────────────────────────────────────────────────

const PACKET_SIZE: usize = 90;

/// Rotating transaction_id counter. Razer firmware on newer devices (Joro
/// included) silently ignores writes that use a stale/fixed trans_id. Synapse
/// increments per-request; we do the same. Range 0x01..=0xFE (skip 0x00 and
/// 0xFF which some firmwares treat as reserved).
static TRANSACTION_ID_COUNTER: AtomicU8 = AtomicU8::new(0x01);

fn next_transaction_id() -> u8 {
    let mut id = TRANSACTION_ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    if id == 0 || id == 0xFF {
        // Skip reserved values by advancing the counter again.
        TRANSACTION_ID_COUNTER.store(0x01, Ordering::Relaxed);
        id = 0x01;
    }
    id
}

pub const VARSTORE: u8 = 0x01;
pub const BACKLIGHT_LED: u8 = 0x05;
pub const STATUS_NEW: u8 = 0x00;
#[allow(dead_code)]
pub const STATUS_OK: u8 = 0x02;
#[allow(dead_code)]
pub const STATUS_NOT_SUPPORTED: u8 = 0x05;

const RAZER_VID: u16 = 0x1532;
const JORO_PID_WIRED: u16 = 0x02CD;
const JORO_PID_DONGLE: u16 = 0x02CE;

const WINDEX: u16 = 0x03;
const WVALUE: u16 = 0x0300;

// bmRequestType, bRequest
const SET_REPORT_TYPE: u8 = 0x21;
const SET_REPORT_REQ: u8 = 0x09;
const GET_REPORT_TYPE: u8 = 0xA1;
const GET_REPORT_REQ: u8 = 0x01;

const USB_TIMEOUT_MS: u64 = 1000;
const SEND_DELAY_MS: u64 = 20;

// ── Packet builder ───────────────────────────────────────────────────────────

/// Build a 90-byte Razer USB packet.
///
/// Layout:
///   [0x00] status          = STATUS_NEW
///   [0x01] transaction_id  = TRANSACTION_ID
///   [0x02-0x03] remaining  = 0
///   [0x04] protocol_type   = 0
///   [0x05] data_size
///   [0x06] command_class
///   [0x07] command_id
///   [0x08-0x57] arguments  (80 bytes, zero-padded)
///   [0x58] crc             = XOR of bytes [0x02..0x57]
///   [0x59] reserved        = 0
pub fn build_packet(command_class: u8, command_id: u8, data_size: u8, args: &[u8]) -> [u8; PACKET_SIZE] {
    let mut pkt = [0u8; PACKET_SIZE];

    pkt[0x00] = STATUS_NEW;
    pkt[0x01] = next_transaction_id();
    // [0x02-0x03] remaining_packets = 0 (already zero)
    // [0x04] protocol_type = 0 (already zero)
    pkt[0x05] = data_size;
    pkt[0x06] = command_class;
    pkt[0x07] = command_id;

    // Copy args into [0x08 .. 0x57] (up to 80 bytes)
    let arg_len = args.len().min(80);
    pkt[0x08..0x08 + arg_len].copy_from_slice(&args[..arg_len]);

    // CRC = XOR of bytes [0x02 .. 0x57] inclusive (indices 2..88)
    let mut crc: u8 = 0;
    for &b in &pkt[2..88] {
        crc ^= b;
    }
    pkt[0x58] = crc;
    // [0x59] reserved = 0 (already zero)

    pkt
}

// ── Packet parser ────────────────────────────────────────────────────────────

#[allow(dead_code)]
pub struct ParsedPacket {
    pub status: u8,
    pub transaction_id: u8,
    pub data_size: u8,
    pub command_class: u8,
    pub command_id: u8,
    pub args: Vec<u8>,
    pub crc_valid: bool,
}

pub fn parse_packet(buf: &[u8; PACKET_SIZE]) -> ParsedPacket {
    let status = buf[0x00];
    let transaction_id = buf[0x01];
    let data_size = buf[0x05];
    let command_class = buf[0x06];
    let command_id = buf[0x07];

    // Args: up to data_size bytes starting at 0x08, clamped to 80
    let arg_len = (data_size as usize).min(80);
    let args = buf[0x08..0x08 + arg_len].to_vec();

    // Verify CRC
    let mut expected_crc: u8 = 0;
    for &b in &buf[2..88] {
        expected_crc ^= b;
    }
    let crc_valid = buf[0x58] == expected_crc;

    ParsedPacket {
        status,
        transaction_id,
        data_size,
        command_class,
        command_id,
        args,
        crc_valid,
    }
}

// ── RazerDevice ─────────────────────────────────────────────────────────────

#[allow(dead_code)]
pub enum ConnectionType {
    Wired,
    Dongle,
}

pub struct RazerDevice {
    handle: DeviceHandle<Context>,
    #[allow(dead_code)]
    pid: u16,
}

impl RazerDevice {
    /// Scan all USB devices for Joro wired or dongle PID and open the first match.
    pub fn open() -> Option<Self> {
        let ctx = Context::new().ok()?;
        let devices = ctx.devices().ok()?;

        for device in devices.iter() {
            let desc = match device.device_descriptor() {
                Ok(d) => d,
                Err(_) => continue,
            };

            if desc.vendor_id() != RAZER_VID {
                continue;
            }

            let pid = desc.product_id();
            if pid != JORO_PID_WIRED && pid != JORO_PID_DONGLE {
                continue;
            }

            match device.open() {
                Ok(handle) => {
                    // Detach kernel driver and claim interface 3 (HID control interface)
                    let iface = WINDEX as u8; // interface 3
                    let _ = handle.set_auto_detach_kernel_driver(true);
                    if handle.claim_interface(iface).is_err() {
                        // Try without claiming — some Windows setups don't need it
                        eprintln!("Warning: could not claim USB interface {iface}");
                    }
                    return Some(RazerDevice { handle, pid });
                }
                Err(_) => continue,
            }
        }

        None
    }

    #[allow(dead_code)]
    pub fn connection_type(&self) -> ConnectionType {
        if self.pid == JORO_PID_DONGLE {
            ConnectionType::Dongle
        } else {
            ConnectionType::Wired
        }
    }

    /// Returns true if the device responds to a get_firmware query.
    pub fn is_connected(&mut self) -> bool {
        self.get_firmware().is_ok()
    }

    /// Query firmware version string from device.
    pub fn get_firmware(&mut self) -> Result<String, String> {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);

        if !parsed.crc_valid {
            return Err("get_firmware: bad CRC in response".into());
        }

        // Firmware version bytes are in args[0] (major) and args[1] (minor)
        if parsed.args.len() >= 2 {
            Ok(format!("v{}.{:02}", parsed.args[0], parsed.args[1]))
        } else {
            Err("get_firmware: response args too short".into())
        }
    }

    /// Set static RGB color on the Joro.
    pub fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        let args = [VARSTORE, BACKLIGHT_LED, 0x01, 0x00, 0x00, 0x01, r, g, b];
        let pkt = build_packet(0x0F, 0x02, 9, &args);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);

        if !parsed.crc_valid {
            return Err("set_static_color: bad CRC in response".into());
        }
        Ok(())
    }

    /// Set backlight brightness (0-255).
    pub fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        let args = [VARSTORE, BACKLIGHT_LED, level];
        let pkt = build_packet(0x0F, 0x04, 3, &args);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);

        if !parsed.crc_valid {
            return Err("set_brightness: bad CRC in response".into());
        }
        Ok(())
    }

    /// Read battery level via Protocol30 `class=0x07 cmd=0x80`. Per
    /// openrazer (razerchromacommon.c): the 0-255 value lives in arg[1].
    /// Maps to a 0-100 percent. Returns Err on read or CRC failure.
    pub fn get_battery_percent(&mut self) -> Result<u8, String> {
        // Request 2-byte response (matches openrazer's get_razer_report(0x07, 0x80, 0x02))
        let pkt = build_packet(0x07, 0x80, 2, &[]);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);
        if !parsed.crc_valid {
            return Err("get_battery: bad CRC in response".into());
        }
        // Log raw bytes for debugging USB↔BLE discrepancies
        let hex: String = parsed.args.iter().take(8).map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
        eprintln!("joro-usb: battery raw args = [{hex}]");
        let raw = *parsed
            .args
            .get(1)
            .ok_or("get_battery: response too short")?;
        let pct = ((raw as u32) * 100 / 255) as u8;
        Ok(pct)
    }

    /// Write a firmware Hypershift (Fn-layer) keymap entry over USB.
    ///
    /// Packet: `class=0x02 cmd=0x0d` with a 10-byte data payload. Reverse-
    /// engineered from Razer Synapse USB captures:
    /// `captures/synapse_hypershift_u3.pcap` shows byte-identical writes on
    /// the Hypershift tab. Example: Right Ctrl → F2 capture = `01 40 01 02
    /// 02 00 3b 00 00 00`.
    ///
    /// **Verified 2026-04-13--2257**: this writes the Hypershift layer.
    /// Confirmed by programming Left→Home / Right→End, cycling transport
    /// once (wired→BLE→wired), and observing Fn+Left=Home / Fn+Right=End
    /// working on **both** wired and BLE — i.e. both transports read from
    /// the same Hypershift storage slot.
    ///
    /// **Commit semantics**: the firmware stores the write immediately
    /// (status=0x02 OK, `cmd=0x8d` readback confirms persistence) but
    /// does NOT refresh the runtime Hypershift table until a transport
    /// mode switch (wired↔BLE). A previous session mis-concluded this
    /// was a "dead end" because writes didn't appear to take effect —
    /// they just needed a transport cycle to go live. See memory
    /// `project_hypershift_commit_trigger.md`.
    ///
    /// **Base-layer writes**: untested. We don't yet know what packet
    /// Synapse uses to program the *plain* (non-Fn) keymap, or whether
    /// a different `args[2]` value here would target base layer.
    ///
    /// **F-row caveat**: F4 and the other media keys emit from a
    /// separate Consumer Control pipeline that bypasses this matrix
    /// entirely. To intercept those, use host-side HID interception on
    /// the Consumer Control interface.
    ///
    /// **BLE equivalent**: not yet implemented. The Protocol30 wrapping
    /// of this packet for BLE transport is currently unknown — a fresh
    /// Windows HCI capture of Synapse doing a Hypershift write over BLE
    /// is needed. See CHANGELOG TODO.
    ///
    /// Args:
    ///   src_matrix - Razer matrix index of the source key
    ///   modifier   - HID modifier byte for the output combo (0 = none)
    ///   dst_usage  - HID keyboard usage code of the output key
    pub fn set_layer_remap(
        &mut self,
        src_matrix: u8,
        modifier: u8,
        dst_usage: u8,
    ) -> Result<(), String> {
        // 10-byte args (matches Synapse's captured Hypershift packet exactly):
        //   [0] 0x01 constant              [3] output type = 0x02 (HID kbd)
        //   [1] source matrix index        [4] output payload size = 0x02
        //   [2] 0x01 profile/var-store     [5] output modifier byte
        //                                  [6] output HID usage code
        //                                  [7..10] padding
        let mut args = [0u8; 10];
        args[0] = 0x01;
        args[1] = src_matrix;
        args[2] = 0x01;
        args[3] = 0x02;
        args[4] = 0x02;
        args[5] = modifier;
        args[6] = dst_usage;
        let pkt = build_packet(0x02, 0x0D, 10, &args);
        self.send_only(&pkt)?;
        std::thread::sleep(Duration::from_millis(SEND_DELAY_MS));
        Ok(())
    }

    /// Write a single keymap entry. No response expected.
    /// `index`: logical key index; `usage`: HID usage code to map to.
    pub fn set_keymap_entry(&mut self, index: u8, usage: u8) -> Result<(), String> {
        let mut args = [0u8; 18];
        // 10-byte zero header, then: index, 0x02, 0x02, 0x00, usage, 0x00, 0x00, 0x00
        args[10] = index;
        args[11] = 0x02;
        args[12] = 0x02;
        args[13] = 0x00;
        args[14] = usage;
        args[15] = 0x00;
        args[16] = 0x00;
        args[17] = 0x00;

        let pkt = build_packet(0x02, 0x0F, 18, &args);
        self.send_only(&pkt)
    }

    // ── Internal USB helpers ─────────────────────────────────────────────────

    pub fn send_receive(&self, pkt: &[u8; PACKET_SIZE]) -> Result<[u8; PACKET_SIZE], String> {
        let timeout = Duration::from_millis(USB_TIMEOUT_MS);

        // SET_REPORT (write packet to device)
        self.handle
            .write_control(SET_REPORT_TYPE, SET_REPORT_REQ, WVALUE, WINDEX, pkt, timeout)
            .map_err(|e| format!("write_control failed: {e}"))?;

        std::thread::sleep(Duration::from_millis(SEND_DELAY_MS));

        // GET_REPORT (read response from device)
        let mut buf = [0u8; PACKET_SIZE];
        self.handle
            .read_control(GET_REPORT_TYPE, GET_REPORT_REQ, WVALUE, WINDEX, &mut buf, timeout)
            .map_err(|e| format!("read_control failed: {e}"))?;

        Ok(buf)
    }

    fn send_only(&self, pkt: &[u8; PACKET_SIZE]) -> Result<(), String> {
        let timeout = Duration::from_millis(USB_TIMEOUT_MS);

        self.handle
            .write_control(SET_REPORT_TYPE, SET_REPORT_REQ, WVALUE, WINDEX, pkt, timeout)
            .map_err(|e| format!("write_control failed: {e}"))?;

        std::thread::sleep(Duration::from_millis(SEND_DELAY_MS));

        Ok(())
    }
}

impl crate::device::JoroDevice for RazerDevice {
    fn is_connected(&mut self) -> bool { RazerDevice::is_connected(self) }
    fn get_firmware(&mut self) -> Result<String, String> { RazerDevice::get_firmware(self) }
    fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        RazerDevice::set_static_color(self, r, g, b)
    }
    fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        RazerDevice::set_brightness(self, level)
    }
    fn set_keymap_entry(&mut self, index: u8, usage: u8) -> Result<(), String> {
        RazerDevice::set_keymap_entry(self, index, usage)
    }
    fn set_layer_remap(
        &mut self,
        src_matrix: u8,
        modifier: u8,
        dst_usage: u8,
    ) -> Result<(), String> {
        RazerDevice::set_layer_remap(self, src_matrix, modifier, dst_usage)
    }
    fn get_battery_percent(&mut self) -> Result<u8, String> {
        RazerDevice::get_battery_percent(self)
    }
    fn transport_name(&self) -> &'static str { "USB" }
}

// ── Unit tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_packet_size() {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        assert_eq!(pkt.len(), 90);
    }

    #[test]
    fn test_build_packet_header() {
        let pkt = build_packet(0x0F, 0x02, 9, &[0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]);
        assert_eq!(pkt[0], STATUS_NEW);
        // trans_id is a rotating counter — verify it's non-zero, non-0xFF.
        assert!(pkt[1] != 0 && pkt[1] != 0xFF);
        assert_eq!(pkt[5], 9);
        assert_eq!(pkt[6], 0x0F);
        assert_eq!(pkt[7], 0x02);
    }

    #[test]
    fn test_build_packet_args() {
        let pkt = build_packet(0x0F, 0x02, 3, &[0x01, 0x05, 0xC8]);
        assert_eq!(pkt[8], 0x01);
        assert_eq!(pkt[9], 0x05);
        assert_eq!(pkt[10], 0xC8);
        assert_eq!(pkt[11], 0x00);
    }

    #[test]
    fn test_build_packet_crc() {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        let mut expected_crc: u8 = 0;
        for &b in &pkt[2..88] {
            expected_crc ^= b;
        }
        assert_eq!(pkt[0x58], expected_crc);
    }

    #[test]
    fn test_parse_packet_roundtrip() {
        let pkt = build_packet(0x0F, 0x04, 3, &[0x01, 0x05, 0x80]);
        let parsed = parse_packet(&pkt);
        assert_eq!(parsed.status, STATUS_NEW);
        assert!(parsed.transaction_id != 0 && parsed.transaction_id != 0xFF);
        assert_eq!(parsed.command_class, 0x0F);
        assert_eq!(parsed.command_id, 0x04);
        assert_eq!(parsed.data_size, 3);
        assert_eq!(&parsed.args, &[0x01, 0x05, 0x80]);
        assert!(parsed.crc_valid);
    }

    #[test]
    fn test_parse_packet_detects_bad_crc() {
        let mut pkt = build_packet(0x00, 0x81, 0, &[]);
        pkt[0x58] ^= 0xFF;
        let parsed = parse_packet(&pkt);
        assert!(!parsed.crc_valid);
    }

    #[test]
    fn test_get_firmware_packet() {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        assert_eq!(pkt[6], 0x00);
        assert_eq!(pkt[7], 0x81);
        assert_eq!(pkt[5], 0);
    }

    #[test]
    fn test_set_static_color_packet() {
        let args = [VARSTORE, BACKLIGHT_LED, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00];
        let pkt = build_packet(0x0F, 0x02, 9, &args);
        assert_eq!(pkt[6], 0x0F);
        assert_eq!(pkt[7], 0x02);
        assert_eq!(pkt[5], 9);
        assert_eq!(pkt[8], VARSTORE);
        assert_eq!(pkt[9], BACKLIGHT_LED);
        assert_eq!(pkt[14], 0xFF);
        assert_eq!(pkt[15], 0x00);
        assert_eq!(pkt[16], 0x00);
    }

    #[test]
    fn test_keymap_entry_packet() {
        let mut args = [0u8; 18];
        args[10] = 30;
        args[11] = 0x02;
        args[12] = 0x02;
        args[14] = 0x29;
        let pkt = build_packet(0x02, 0x0F, 18, &args);
        assert_eq!(pkt[6], 0x02);
        assert_eq!(pkt[7], 0x0F);
        assert_eq!(pkt[5], 18);
        assert_eq!(pkt[8 + 10], 30);
        assert_eq!(pkt[8 + 14], 0x29);
    }
}
