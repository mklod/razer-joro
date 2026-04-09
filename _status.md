# Razer Joro — Status

## Current milestone
Stage 1: Python Prototype — Tasks 1-5 complete. Key remapping confirmed working (SET 0x02/0x0F). Finding CapsLock matrix index in progress.

## Last session (2026-04-09) — Tasks 1-6 (partial) + Protocol RE

### Completed
- Created full Python prototype: `razer_packet.py`, `usb_transport.py`, `commands.py`, `test_lighting.py`, `enumerate.py`
- Cloned openrazer PR branch to `openrazer-ref/`
- Installed Wireshark + USBPcap (USBPcap device nodes broken on this system, not needed)
- **Verified lighting control on hardware** — static color + brightness confirmed
- **Brute-force command scan** — discovered all supported command class/id pairs
- **Key remapping confirmed working** — SET 0x02/0x0F changes keymap, verified backtick->A remap on hardware
- CapsLock index search in progress (confirmed in range 20-35, currently mapped to unique F-keys for identification)

### Major Protocol Discoveries
1. **Transport:** pyusb raw USB control transfers required (hidapi feature reports return 0x05)
   - SET_REPORT: bmRequestType=0x21, bRequest=0x09, wValue=0x0300, wIndex=0x03
   - GET_REPORT: bmRequestType=0xA1, bRequest=0x01, wValue=0x0300, wIndex=0x03
2. **Packet struct:** openrazer `razer_report` layout
   - status@0, txn_id@1(0x1F), remaining_pkts@2-3, proto@4, data_size@5, class@6, cmd@7, args@8-87, crc@88, reserved@89
3. **LED constants:** VARSTORE=0x01, BACKLIGHT_LED=0x05
4. **Lighting commands (verified on hardware):**
   - Static color: class=0x0F id=0x02 size=9 args=[01,05,01,00,00,01,R,G,B]
   - Set brightness: class=0x0F id=0x04 size=3 args=[01,05,brightness]
   - Get brightness: class=0x0F id=0x84 size=1 args=[01] -> resp [01,05,brightness]
   - Get firmware: class=0x00 id=0x81 size=0 -> resp [major,minor]
5. **Key remapping (verified on hardware):**
   - GET keymap: class=0x02 cmd=0x8F — returns 10-byte header + 8x8-byte entries per page
   - SET keymap: class=0x02 cmd=0x0F — write entries, format: `[idx, 0x02, 0x02, 0x00, target_usage, 0x00, 0x00, 0x00]`
   - Entry index is keyboard matrix position (NOT HID usage ID)
   - Known indices: 1=backtick, 2-8=digit keys 1-7
   - After SET, no GET_REPORT response (IO error) — must send-only, then read separately
   - USB replug resets all remaps to defaults
6. **Idle/sleep config (discovered, not yet tested):**
   - GET: class=0x06 cmd=0x86 -> [00,00,00,64,00,00,00,00,00,04,A0,00] (0x64=100, 0x04A0=1184)
   - GET extended: class=0x06 cmd=0x8E -> 14 bytes with duplicate timing values
7. **Full supported command map:** classes 0x00,0x02,0x03,0x04,0x05,0x06,0x07,0x0A,0x0F all have supported commands

### Venv & Environment
- Python venv: `C:/Users/mklod/AppData/Local/razer-joro-venv` (not on SMB share)
- Must kill `razer_elevation_service.exe` before USB access
- libusb DLL: bundled via `pip install libusb`
- Deps: hidapi, bleak, pyusb, libusb

## Next immediate task
- Identify CapsLock's matrix index (currently indices 20-35 mapped to F1-F12+extras for ID)
- Test modifier combo remaps (e.g., CapsLock -> Ctrl+F12)
- Decode and test sleep/idle config SET commands (class 0x06)
- Write `proto/commands.py` remap functions and `proto/test_remap.py`
- Commit all proto scan scripts
- Proceed to Rust phase (Tasks 10+)

## Blockers
- None (USBPcap not needed, brute-force scan + direct command testing works)

## Key decisions
- Skipped Wireshark sniffing — brute-force command scan + direct testing more efficient
- pyusb + libusb (not hidapi) for all USB communication
- openrazer PR #2683 as reference for lighting commands
- Razer services must be killed before device access
- Key remap uses matrix index, not HID usage — need index mapping table
- USB replug resets remaps (no onboard storage for custom maps)
