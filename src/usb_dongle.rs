// src/usb_dongle.rs — Joro via Razer DA V2 X HyperSpeed multi-device dongle (PID 0x009C)
// Last modified: 2026-04-24--1745
//
// Talks to Joro through the dongle's MI_00 vendor HID interface using
// HidD_SetFeature / HidD_GetFeature (via the `hidapi` crate). Wire format
// is the same Razer Protocol30 packet used by `src/usb.rs`, with one
// firmware-level difference:
//
//   - Direct-USB Joro (PID 0x02CD) uses LED_ID = 0x05 (BACKLIGHT_LED).
//   - Joro through this dongle uses LED_ID = 0x00.
//
// Verified 2026-04-24 with HID Set_Feature_Report writes against
// `\\?\HID#VID_1532&PID_009C&MI_00#...`:
//   - Brightness sweep 0xFF/0x10/0xFF/0x10/0xFF/0x80 — keyboard pulsed.
//   - set_static_color(0xFF, 0x00, 0x00) — keyboard turned red.
// See memory/project_dongle_protocol_format.md.

use crate::device::JoroDevice;
use crate::usb::{build_packet, parse_packet, VARSTORE};
use hidapi::{HidApi, HidDevice};
use std::time::Duration;

const RAZER_VID: u16 = 0x1532;
const DONGLE_PID: u16 = 0x009C;
/// Dongle-side LED index for Joro's keyboard backlight. NOT the same as
/// `usb::BACKLIGHT_LED` (0x05) which targets direct-USB Joro.
const DONGLE_LED: u8 = 0x00;
const PACKET_SIZE: usize = 90;
const SEND_DELAY_MS: u64 = 20;

pub struct RazerDongle {
    handle: HidDevice,
}

impl RazerDongle {
    /// Find PID 0x009C interface 0 (the vendor HID control channel) and
    /// open it. Returns None if the dongle isn't enumerated or hidapi
    /// can't open it (e.g. another process holds it exclusively).
    pub fn open() -> Option<Self> {
        let api = match HidApi::new() {
            Ok(a) => a,
            Err(e) => {
                eprintln!("joro-dongle: HidApi::new failed: {e}");
                return None;
            }
        };
        let info = api.device_list().find(|d| {
            d.vendor_id() == RAZER_VID
                && d.product_id() == DONGLE_PID
                && d.interface_number() == 0
        })?;
        let path = info.path().to_owned();
        match api.open_path(&path) {
            Ok(h) => {
                eprintln!(
                    "joro-dongle: opened MI_00 path={}",
                    path.to_string_lossy()
                );
                Some(RazerDongle { handle: h })
            }
            Err(e) => {
                eprintln!("joro-dongle: open_path failed: {e}");
                None
            }
        }
    }

    /// Connectivity probe: is the dongle HID device still enumerated?
    ///
    /// This is a pure Windows device-list walk — NO Protocol30 / USB
    /// control traffic. The old implementation queried battery every
    /// poll, which (a) almost always timed out through the dongle's
    /// bridged RF link giving false "disconnected", and (b) flooded the
    /// dongle's control pipe and starved keyboard input (severe typing
    /// lag). "Connected" = the dongle is plugged in. The keyboard itself
    /// may be asleep, but it wakes instantly on keypress, so from the
    /// user's perspective it's connected.
    pub fn is_connected(&mut self) -> bool {
        match HidApi::new() {
            Ok(api) => api
                .device_list()
                .any(|d| d.vendor_id() == RAZER_VID && d.product_id() == DONGLE_PID),
            // Can't enumerate (transient hidapi error) — assume still
            // present rather than flapping the tray to disconnected.
            Err(_) => true,
        }
    }

    /// Is a keyboard actually BRIDGING through the dongle (vs. the
    /// receiver merely being plugged in with nothing bonded)?
    ///
    /// The keyboard PUSHES a periodic `09 31 <raw> ..` heartbeat on a
    /// dongle HID collection while bonded+awake — and with FW sleep
    /// patched off it's continuous. Seeing it within the budget ⇒ the
    /// keyboard is on the dongle. NOT seeing it ⇒ the receiver is
    /// plugged but unbonded (e.g. bond wiped by a reflash) ⇒ the caller
    /// must fall through to BLE instead of mis-committing to a dead
    /// dongle. Fully PASSIVE (reads existing input reports; no solicited
    /// Protocol30, so no input-lag regression — cf.
    /// project_dongle_battery_passive).
    pub fn dongle_bridging_keyboard() -> bool {
        let api = match HidApi::new() {
            Ok(a) => a,
            Err(_) => return false,
        };
        let paths: Vec<_> = api
            .device_list()
            .filter(|d| d.vendor_id() == RAZER_VID && d.product_id() == DONGLE_PID)
            .map(|d| d.path().to_owned())
            .collect();
        let mut handles = Vec::new();
        for p in &paths {
            if let Ok(h) = api.open_path(p) {
                let _ = h.set_blocking_mode(false);
                handles.push(h);
            }
        }
        if handles.is_empty() {
            return false;
        }
        let deadline = std::time::Instant::now() + Duration::from_millis(2000);
        let mut buf = [0u8; 64];
        while std::time::Instant::now() < deadline {
            for h in &handles {
                if let Ok(n) = h.read_timeout(&mut buf, 60) {
                    // `09 31` = the keyboard's periodic battery heartbeat
                    // (same marker BLE uses). Its presence = bonded+live.
                    if n >= 3 && buf[0] == 0x09 && buf[1] == 0x31 {
                        return true;
                    }
                }
            }
        }
        false
    }

    /// Best-effort firmware query. The dongle does not always route the
    /// standard class=0x00 cmd=0x81 query through to Joro — fall back to
    /// a stub string so callers get a usable label.
    pub fn get_firmware(&mut self) -> Result<String, String> {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        if let Ok(response) = self.send_receive(&pkt) {
            let parsed = parse_packet(&response);
            if parsed.crc_valid && parsed.args.len() >= 2 {
                return Ok(format!("v{}.{:02}", parsed.args[0], parsed.args[1]));
            }
        }
        Ok("(dongle)".into())
    }

    /// Dongle custom-lighting INIT sequence. Byte-diffing the daemon's
    /// dongle-lighting USBPcap against Synapse's (2026-05-22) showed the
    /// daemon's 0f:10/0f:02/0f:03/0f:04 frames were already byte-identical
    /// to Synapse's — but Synapse ALSO sends three session-init frames the
    /// daemon never did, and without them the keyboard ignores the 0f:03
    /// colour frames. Captured order:
    ///   0f:80 dsize=80 [00 × 80]      — clear/arm the custom framebuffer
    ///   0f:84 dsize=3  [01 05 00]     — framebuffer config
    ///   0f:90 dsize=1  [00]           — ?
    ///   0f:10 dsize=1  [01]           — enable custom lighting
    ///   0f:02 dsize=6  [00 00 08 …]   — effect-select (08 = custom)
    /// All fire-and-forget (send_only) — the dongle's bridged-RF GET
    /// round-trip times out (0x04) for nearly every solicited query, which
    /// is normal (project_dongle_battery_passive); the SET still lands.
    /// SET a Protocol30 frame, then GET (drain) the dongle's response and
    /// IGNORE it entirely. This dongle is a request/response RF bridge:
    /// USBPcap diffing (2026-05-22) proved the daemon's lighting frames are
    /// byte-identical to Synapse's, yet only Synapse's worked — the one
    /// behavioral delta is that Synapse does SET *then* GET on every frame.
    /// The bridge appears to need the host to drain the GET to advance its
    /// forward queue; `send_only` (SET, no GET) leaves an undelivered
    /// response that stalls the bridge. We do the GET but ignore the result
    /// (status 0x04 = "no keyboard data" is normal and harmless — the SET
    /// itself is what carries the command).
    fn send_drain(&self, pkt: &[u8; PACKET_SIZE]) -> Result<(), String> {
        // send_receive does SET + 20ms + GET. We don't care what the GET
        // returns — draining it is the point.
        let _ = self.send_receive(pkt);
        Ok(())
    }

    fn lighting_init(&self) -> Result<(), String> {
        self.send_drain(&build_packet(0x0F, 0x80, 80, &[0u8; 80]))?;
        self.send_drain(&build_packet(0x0F, 0x84, 3, &[0x01, 0x05, 0x00]))?;
        self.send_drain(&build_packet(0x0F, 0x90, 1, &[0x00]))?;
        self.send_drain(&build_packet(0x0F, 0x10, 1, &[0x01]))?;
        self.send_drain(&build_packet(0x0F, 0x02, 6, &[0x00, 0x00, 0x08, 0x00, 0x00, 0x00]))?;
        Ok(())
    }

    pub fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        self.lighting_init()?;
        self.send_drain(&build_packet(0x0F, 0x03, 8, &[0x00, 0x00, 0x00, 0x00, 0x00, r, g, b]))?;
        eprintln!("joro-dongle: set_static_color({r:02x}{g:02x}{b:02x}) — sent (init+0f:03, SET+GET drain)");
        Ok(())
    }

    pub fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        self.lighting_init()?;
        self.send_drain(&build_packet(0x0F, 0x04, 3, &[VARSTORE, DONGLE_LED, level]))?;
        eprintln!("joro-dongle: set_brightness({level}) — sent (init+0f:04, SET+GET drain)");
        Ok(())
    }

    /// Set Joro's firmware mode (MM-primary vs Fn-primary). Same Protocol30
    /// packet as BLE per memory `project_fnmm_toggle_solved.md`:
    ///   `SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0]`
    ///   mode: 0 = MM-primary (F-row emits consumer/firmware combos)
    ///         3 = Fn-primary (F-row emits plain VK_F4..VK_F12)
    pub fn set_device_mode(&mut self, fn_primary: bool) -> Result<(), String> {
        let mode_byte: u8 = if fn_primary { 3 } else { 0 };
        let args = [mode_byte, 0];
        let pkt = build_packet(0x01, 0x02, 2, &args);
        self.send_only(&pkt)
    }

    /// "Begin keymap edit session" precursor. Synapse sends this ONCE per
    /// session before any `cmd=0x0d` Hypershift writes. Without it, the
    /// dongle silently drops keymap writes. Verified by Frida trace
    /// 2026-04-24: Synapse's pattern was two cmd=0xa4 calls (~30 s before
    /// any cmd=0x0d), then the actual writes worked.
    ///
    /// Packet: `class=0x02 cmd=0xa4 dsize=1 args=[0x00]`.
    pub fn unlock_keymap_writes(&mut self) -> Result<(), String> {
        let pkt = build_packet(0x02, 0xA4, 1, &[0x00]);
        self.send_only(&pkt)
    }

    /// Send Synapse's "begin/commit transaction" wrapper:
    /// `class=0x03 cmd=0x00 args=[0x00, 0x08, 0x00]`. Synapse sends this
    /// before AND after batches of cmd=0x0d writes — likely a transaction
    /// bracket that commits writes to flash. Without it, writes only go
    /// to RAM and are lost on power cycle.
    pub fn keymap_transaction(&self) -> Result<(), String> {
        let pkt = build_packet(0x03, 0x00, 3, &[0x00, 0x08, 0x00]);
        self.send_only(&pkt)
    }

    /// Read a single keymap entry (for the given layer + matrix index).
    /// Per Synapse USBPcap analysis, the dongle firmware silently drops
    /// `set_layer_remap` (cmd=0x0d) writes unless the target entry has been
    /// read via `cmd=0x8d` in the same session. Calling this on the keys
    /// you intend to overwrite is a required precursor.
    ///
    /// Packet: class=0x02 cmd=0x8d args=[0x01, matrix, layer, 0,0,0,0,0,0,0]
    pub fn read_keymap_entry(&self, matrix: u8, layer: u8) -> Result<[u8; 10], String> {
        let read_args = [0x01u8, matrix, layer, 0, 0, 0, 0, 0, 0, 0];
        let pkt = build_packet(0x02, 0x8D, 10, &read_args);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);
        if !parsed.crc_valid {
            return Err("read_keymap_entry: bad CRC".into());
        }
        let mut out = [0u8; 10];
        for (i, b) in parsed.args.iter().take(10).enumerate() {
            out[i] = *b;
        }
        Ok(out)
    }

    /// Write a firmware Fn-layer (Hypershift) keymap entry.
    ///
    /// Same Protocol30 `class=0x02 cmd=0x0d` packet as direct-USB Joro per
    /// `src/usb.rs::set_layer_remap`. Verified through dongle by Frida-tracing
    /// Synapse during a Hypershift remap save (2026-04-24): exact same byte
    /// layout, ack arrives via standard HID GET_FEATURE.
    ///
    /// args[2] = 0x01 → Fn (Hypershift) layer. (0x00 = base layer.)
    /// Through dongle the write commits immediately — no transport cycle
    /// required (unlike direct USB per `project_hypershift_commit_trigger.md`).
    ///
    /// **Caller MUST call `unlock_keymap_writes()` once before the first
    /// `set_layer_remap` call** in a session — otherwise the dongle ignores
    /// the write silently.
    pub fn set_layer_remap(
        &mut self,
        src_matrix: u8,
        modifier: u8,
        dst_usage: u8,
    ) -> Result<(), String> {
        let mut args = [0u8; 10];
        args[0] = 0x01;
        args[1] = src_matrix;
        args[2] = 0x01; // Fn / Hypershift layer
        args[3] = 0x02; // output type = HID kbd
        args[4] = 0x02; // output payload size
        args[5] = modifier;
        args[6] = dst_usage;
        let pkt = build_packet(0x02, 0x0D, 10, &args);
        self.send_only(&pkt)
    }

    /// Disable / configure firmware sleep idle timer.
    /// Razer Protocol30 `class=0x07 cmd=0x03 args=[secs_lo, secs_hi]` —
    /// 0 = never sleep (per openrazer reference), 1..0xFFFF = idle timeout
    /// in seconds before keyboard enters power-save. Useful when you want
    /// the keyboard responsive immediately on every keypress and don't
    /// care about extra battery drain.
    pub fn set_idle_time(&mut self, seconds: u16) -> Result<(), String> {
        let args = [(seconds & 0xff) as u8, ((seconds >> 8) & 0xff) as u8];
        let pkt = build_packet(0x07, 0x03, 2, &args);
        self.send_only(&pkt)
    }

    /// Read battery percent. Same Protocol30 query as direct USB
    /// (`class=0x07 cmd=0x80`, value at args[1]).
    pub fn get_battery_percent(&mut self) -> Result<u8, String> {
        // SINGLE control round-trip per call. Multi-attempt retries hammered
        // the dongle's control pipe and starved keyboard input forwarding,
        // causing dropped keys mid-typing. A non-OK status (0x04 =
        // bridged-query timeout, keyboard idle on RF — common, transient)
        // is an Err so callers can't confuse it with a genuinely dead
        // battery: the old `Ok(0)` sentinel made real 0% and "no answer"
        // indistinguishable.
        let pkt = build_packet(0x07, 0x80, 2, &[]);
        let response = self.send_receive(&pkt)?;
        let parsed = parse_packet(&response);
        if !parsed.crc_valid {
            return Err("get_battery: bad CRC".into());
        }
        if parsed.status != 0x02 {
            return Err(format!(
                "get_battery: bridged query status 0x{:02x} (transient — heartbeat will fill in)",
                parsed.status
            ));
        }
        let raw = parsed.args.get(1).copied().unwrap_or(0);
        // Rounded — same formula on every transport (see ble.rs).
        Ok((((raw as u32) * 100 + 127) / 255).min(100) as u8)
    }

    /// Persist the live Hypershift keymap to flash by faithfully replaying
    /// Synapse's proven class-0x0F VARSTORE commit transaction.
    ///
    /// `assets/hypershift_replay.bin` = 639 × 90-byte Protocol30 frames
    /// captured from a known-good `synapse_hypershift_save_u2.pcap`
    /// session. Every frame is sent byte-identical EXCEPT
    /// `class=0x02 cmd=0x0d` (set_layer_remap), whose 10-byte payload is
    /// rebuilt from `bindings` (our own remaps) — faithful replay +
    /// binding substitution, the strategy chosen 2026-05-18.
    ///
    /// Firmware path (verified by RE, see project_fw_keymap_persistence):
    /// the 0f:10/0f:02 frames arm the VARSTORE persist mode and the 0f:03
    /// frames flush each 40-byte record to flash at 0xf2000 via
    /// SoftDevice svc #0x29. Writes the config record store only — NOT
    /// firmware code — and is recoverable (re-write / Synapse reset).
    pub fn persist_keymap(&mut self, bindings: &[(u8, u8, u8)]) -> Result<(), String> {
        const REPLAY: &[u8] = include_bytes!("../assets/hypershift_replay.bin");
        if REPLAY.len() % PACKET_SIZE != 0 {
            return Err(format!(
                "hypershift_replay.bin not a multiple of {PACKET_SIZE} ({})",
                REPLAY.len()
            ));
        }
        let total = REPLAY.len() / PACKET_SIZE;
        let mut bind_i = 0usize;
        let mut sent_0d = 0usize;
        for (idx, frame) in REPLAY.chunks_exact(PACKET_SIZE).enumerate() {
            let (class, cmd) = (frame[0x06], frame[0x07]);
            if class == 0x02 && cmd == 0x0D {
                // Substitute our own binding. If the caller has none,
                // skip the frame (commit whatever RAM already holds).
                if bindings.is_empty() {
                    continue;
                }
                let (matrix, modifier, dst) = bindings[bind_i % bindings.len()];
                bind_i += 1;
                let args = [0x01u8, matrix, 0x01, 0x02, 0x02, modifier, dst, 0, 0, 0];
                let pkt = build_packet(0x02, 0x0D, 10, &args);
                self.send_only(&pkt)?;
                sent_0d += 1;
            } else {
                // Verbatim replay of the proven frame.
                let mut pkt = [0u8; PACKET_SIZE];
                pkt.copy_from_slice(frame);
                self.send_only(&pkt)?;
            }
            if idx % 128 == 0 {
                eprintln!("joro-dongle: persist replay {idx}/{total}");
            }
        }
        eprintln!(
            "joro-dongle: persist_keymap done — {total} frames replayed, \
             {sent_0d} binding writes ({} distinct)",
            bindings.len()
        );
        Ok(())
    }

    fn send_only(&self, pkt: &[u8; PACKET_SIZE]) -> Result<(), String> {
        // hidapi's send_feature_report expects buf[0] = report ID.
        let mut buf = [0u8; PACKET_SIZE + 1];
        buf[1..].copy_from_slice(pkt);
        self.handle
            .send_feature_report(&buf)
            .map_err(|e| format!("send_feature_report: {e}"))?;
        std::thread::sleep(Duration::from_millis(SEND_DELAY_MS));
        Ok(())
    }

    fn send_receive(&self, pkt: &[u8; PACKET_SIZE]) -> Result<[u8; PACKET_SIZE], String> {
        self.send_only(pkt)?;
        let mut buf = [0u8; PACKET_SIZE + 1];
        // buf[0] = report ID 0
        let n = self
            .handle
            .get_feature_report(&mut buf)
            .map_err(|e| format!("get_feature_report: {e}"))?;
        if n < PACKET_SIZE {
            return Err(format!("get_feature_report short read: {n} bytes"));
        }
        let mut out = [0u8; PACKET_SIZE];
        // hidapi returns the full buffer including report ID at [0].
        out.copy_from_slice(&buf[1..PACKET_SIZE + 1]);
        Ok(out)
    }
}

impl JoroDevice for RazerDongle {
    fn is_connected(&mut self) -> bool {
        RazerDongle::is_connected(self)
    }
    fn get_firmware(&mut self) -> Result<String, String> {
        RazerDongle::get_firmware(self)
    }
    fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        RazerDongle::set_static_color(self, r, g, b)
    }
    fn set_brightness(&mut self, level: u8) -> Result<(), String> {
        RazerDongle::set_brightness(self, level)
    }
    fn set_keymap_entry(&mut self, _index: u8, _usage: u8) -> Result<(), String> {
        // Not yet decoded for dongle — return Ok so callers don't fail.
        // TODO: capture Synapse base-layer keymap writes through dongle.
        Ok(())
    }
    fn set_layer_remap(
        &mut self,
        src_matrix: u8,
        modifier: u8,
        dst_usage: u8,
    ) -> Result<(), String> {
        RazerDongle::set_layer_remap(self, src_matrix, modifier, dst_usage)
    }
    fn get_battery_percent(&mut self) -> Result<u8, String> {
        RazerDongle::get_battery_percent(self)
    }
    fn set_device_mode(&mut self, fn_primary: bool) -> Result<(), String> {
        RazerDongle::set_device_mode(self, fn_primary)
    }
    fn unlock_keymap_writes(&mut self) -> Result<(), String> {
        RazerDongle::unlock_keymap_writes(self)
    }
    fn set_idle_time(&mut self, seconds: u16) -> Result<(), String> {
        RazerDongle::set_idle_time(self, seconds)
    }
    fn persist_keymap(&mut self, bindings: &[(u8, u8, u8)]) -> Result<(), String> {
        RazerDongle::persist_keymap(self, bindings)
    }
    fn transport_name(&self) -> &'static str {
        "DONGLE"
    }
}
