# Joro BLE Protocol Reverse Engineering

Last modified: 2026-04-10--1630

## Background

The Joro keyboard works over BLE without Synapse for standard HID input. However, the custom Razer BLE service (`5240xxxx`) is needed for lighting, remaps, and other configuration. This service uses **Razer Protocol30** — a different protocol than USB, designed for 20-byte BLE payloads.

## Protocol30 — BLE Command Protocol

Discovered by decompiling Synapse 3 Electron app (cached web modules in Service Worker CacheStorage).

### Packet Format

**Request (write to char 1524):**
```
Byte 0:     transactionId (auto-increment, echoed in response)
Bytes 1-3:  data payload length (24-bit: [0, hi, lo] for small values)
Byte 4:     commandClass1
Byte 5:     commandClass2
Byte 6:     commandId
Byte 7:     subCommandId
Bytes 8+:   data payload
```

Total packet size: 8-byte header + payload, padded to 20 bytes for BLE MTU.

**Response (notification on char 1525):**
```
Byte 0:     echoed transactionId
Bytes 1-3:  response data length
Byte 4-5:   echoed command class
Byte 6:     echoed commandId
Byte 7:     status code
Bytes 8+:   response data
```

### Status Codes (response byte 7)

| Code | Name | Description |
|------|------|-------------|
| 0x01 | BUSY | Command in progress, retry |
| 0x02 | SUCCESS | Command completed |
| 0x03 | FAILURE | Command failed |
| 0x04 | TIMEOUT | No response timeout |
| 0x05 | NOT_SUPPORTED | Command not supported |
| 0x06 | PROFILE_NOT_SUPPORTED | Profile not supported |
| 0x07 | TARGET_NOT_SUPPORTED | Target ID not supported |

### Multi-packet Responses

For responses larger than 20 bytes, Protocol30 uses a multi-packet scheme:
- First packet: header (8 bytes) with data length in bytes 1-3
- Subsequent packets: raw data continuation (no header)
- Reassembly based on declared length vs received bytes

### Key Constants (from `constants.js`)

```
PROTOCOL30_SERVICE_UUID:    "52401523f97c7f900e7f6c6f4e36db1c"
PROTOCOL30_WRITE_UUID:      "52401524f97c7f900e7f6c6f4e36db1c"
PROTOCOL30_READ_UUID:       "52401525f97c7f900e7f6c6f4e36db1c"
PROTOCOL30_NOTIFY_UUID:     "52401525f97c7f900e7f6c6f4e36db1c"
PROTOCOL30_HEADER_LENGTH:   8
PROTOCOL30_DATA_LENGTH:     20
TIMEOUT_PROTOCOL30_COMMAND: 500ms
RAZER_BLE_VENDOR_IDS:       [0x1532, 0x068E]
```

### Confirmed Byte Mapping (verified on hardware)

The Synapse JS source uses a different mapping than what the firmware actually expects. Verified mapping:

```
Byte 0: transactionId (auto-increment, echoed in response)
Bytes 1-3: response data length in response; 0 in request for header-only
Byte 4: USB command class (same values as USB protocol byte 6)
Byte 5: USB command ID (same values as USB protocol byte 7)
Byte 6: sub-parameter 1 (0 for simple GET)
Byte 7: sub-parameter 2 (0 for simple GET)
Bytes 8+: data payload (if any)
```

**Critical:** Send exactly 8 bytes for header-only commands. Padding to 20 bytes causes FAILURE (0x03). Only include bytes 8+ when sending data.

**Multi-packet response:** When response byte 1 > 0, a second notification follows with the actual data.

### Verified Commands

**GET commands (single 8-byte ATT Write Request, no payload):**

| Command | Class/Cmd | TX (8 bytes) | Status | Response Data | Notes |
|---------|-----------|-------------|--------|---------------|-------|
| GET firmware | 0x00/0x81 | `[txn, 0,0,0, 0x00, 0x81, 0,0]` | SUCCESS | `01 02 02 00` | FW v1.2.2.0 |
| GET device type | 0x00/0x83 | `[txn, 0,0,0, 0x00, 0x83, 0,0]` | SUCCESS | `14` | Type = 0x14 (20) |
| GET battery | 0x07/0x80 | `[txn, 0,0,0, 0x07, 0x80, 0,0]` | SUCCESS | `40 c2 c5 86` | Battery data |
| GET 0x01/0xA0 | 0x01/0xA0 | `[txn, 0,0,0, 0x01, 0xA0, 0,0]` | SUCCESS | `02` | Device status (driver init) |
| GET 0x05/0x87 | 0x05/0x87 | `[txn, 0,0,0, 0x05, 0x87, 0,1]` | SUCCESS | `01` | Config state |
| GET 0x05/0x84 | 0x05/0x84 | `[txn, 0,0,0, 0x05, 0x84, 0,0]` | SUCCESS | `84 03` | Config state |
| GET 0x01/0x86 | 0x01/0x86 | `[txn, 0,0,0, 0x01, 0x86, 0,0]` | SUCCESS | 3 bytes | Status |
| GET BLE FW | 0x10/0x81 | `[txn, 0,0,0, 0x10, 0x81, 0,0]` | SUCCESS | `19 01 06 16` | BLE firmware version |
| GET HW rev | 0x10/0x82 | `[txn, 0,0,0, 0x10, 0x82, 0,0]` | SUCCESS | `00 01 02` | Hardware revision |
| GET lighting | 0x10/0x83 | `[txn, 0,0,0, 0x10, 0x83, 0,0]` | SUCCESS | 10 bytes | Lighting state |
| GET brightness | 0x10/0x85 | `[txn, 0,0,0, 0x10, 0x85, 0,0]` | SUCCESS | `66` | Brightness (0-FF) |

**Commands that are NOT_SUPPORTED or fail:**

| Command | Class/Cmd | Status | Notes |
|---------|-----------|--------|-------|
| GET serial | 0x00/0x82 | NOT_SUPPORTED | Not available over BLE |
| GET keymap | 0x02/0x8F | NOT_SUPPORTED | Keymaps not available over BLE |
| USB-style lighting | 0x0F/* | NOT_SUPPORTED | USB class 0x0F doesn't exist on BLE |
| BLE class 0x10 cmds 0x00,0x06,0x84,0x86 | 0x10/* | NOT_SUPPORTED | Only 0x81-0x83, 0x85 work |
| Effect types 0x00, 0x02-0x06 | via SET 0x10/0x03 | FAILURE | Only static (0x01) works in 7B format |

### SET Commands — WORKING (split write protocol)

**BREAKTHROUGH (2026-04-10):** SET commands require **split writes** — the 8-byte header and data payload must be sent as TWO SEPARATE ATT Write Requests to characteristic 1524. Concatenating header+data into a single write returns FAILURE (0x03).

Discovered by capturing the Razer kernel driver's (`RzDev_02ce.sys`) BLE HCI traffic via Windows ETW tracing (`logman` + BTHPORT provider), converting to XML with `tracerpt`, then parsing ATT Write Request opcodes (0x12) to GATT handle 0x45 (69).

**Protocol for SET commands:**
```
1. ATT Write Request → char 1524:  8-byte header [txn, dlen, 0, 0, class, cmd, sub1, sub2]
2. ATT Write Request → char 1524:  raw data payload [byte0, ..., byteN] (exactly dlen bytes)
3. Keyboard responds via notification on char 1525
```

**GET commands** use a single 8-byte write (no data, no split needed).

**Verified SET commands:**

| Command | Header (8B) | Data | Result |
|---------|-------------|------|--------|
| SET brightness | `[txn, 01, 0, 0, 10, 05, 01, 00]` | `[brightness 0x00-0xFF]` | SUCCESS |
| SET static color | `[txn, 07, 0, 0, 10, 03, 01, 00]` | `[01, 00, 00, 01, R, G, B]` | SUCCESS |
| SET disable (lights off) | `[txn, 07, 0, 0, 10, 03, 01, 00]` | `[00, 00, 00, 01, R, G, B]` | SUCCESS |
| SET 0x05/0x07 | `[txn, 01, 0, 0, 05, 07, 00, 01]` | `[00]` | SUCCESS (driver init) |

**Lighting state data (7 bytes for SET, 10 bytes from GET 0x10/0x83):**
```
Byte 0: enabled (0x01=on, 0x00=off)
Byte 1: 0x00 (reserved?)
Byte 2: 0x00 (reserved?)
Byte 3: effect type (0x01=static; 0x02-0x06 UNSUPPORTED in 7B format)
Byte 4: Red   (0-255) — for static effect
Byte 5: Green (0-255)
Byte 6: Blue  (0-255)
GET response also includes bytes 7-9 (padding/extra state)
```

**Effect data format (variable length):**
```
SET 0x10/0x03 sub=01,00
Data: [effect, param, 0x00, num_colors, R1,G1,B1, R2,G2,B2, ...]
dlen = 4 + (num_colors * 3)
```

| Effect | ID | param | num_colors | dlen | Data Example |
|--------|-----|-------|------------|------|---|
| Static | 0x01 | 0x00 | 0x01 | 7 | `01 00 00 01 FF 00 00` |
| Breathing (1 color) | 0x02 | 0x01 | 0x01 | 7 | `02 01 00 01 00 FF 00` |
| Breathing (2 color) | 0x02 | 0x02 | 0x02 | 10 | `02 02 00 02 00 FF 00 FF 00 00` |
| Spectrum cycling | 0x03 | 0x00 | 0x00 | 4 | `03 00 00 00` |
| Wave | 0x04 | ? | ? | ? | Not yet captured |
| Reactive | 0x05 | ? | ? | ? | Not yet captured |
| Starlight | 0x06 | ? | ? | ? | Not yet captured |

**Note:** The initial effect sweep failed because dlen was fixed at 7 for all effects. Spectrum needs dlen=4, breathing-2-color needs dlen=10. The `param` byte (byte 1) mirrors `num_colors` for breathing, is 0x00 for static/spectrum.

**Key requirements:**
- **Split writes mandatory** — header and data as TWO separate ATT Write Requests
- **sub1=0x01** for SET brightness and SET color (not 0x00 or 0x05)
- **cmd=0x03** for SET color (mirrors GET 0x83 with high bit cleared)
- **cmd=0x05** for SET brightness (mirrors GET 0x85 with high bit cleared)
- **No BLE encryption/pairing required** — SMP pairing returns PAIR_NOT_ALLOWED but all commands work unencrypted
- **ATT Write Request** (opcode 0x12) used; Write Without Response may also work for GETs

### Razer Driver Init Sequence (from HCI capture)

The `RzDev_02ce.sys` driver sends this sequence after BLE connection + GATT discovery:

```
1. GET 0x01/0xA0 (x2)        → 0x02 (device status check, sent twice)
2. GET 0x05/0x87 sub=00,01   → 0x01 (config state)
3. GET 0x05/0x84              → [0x84, 0x03] (config state)
4. SET 0x05/0x07 sub=00,01 data=[0x00] (config write — may not be required for lighting)
5. SET 0x10/0x05 sub=01,00 data=[brightness] (set brightness)
6. GET 0x01/0x86              → 3 bytes (status check)
7. SET 0x10/0x03 sub=01,00 data=[7 bytes] (set lighting effect)
```

Steps 1-4 appear to be init/status queries. SET brightness and SET color work without them.

### Additional Driver Commands (from effects HCI capture)

Commands observed during Chroma Studio effect changes:

| Command | Type | Notes |
|---------|------|-------|
| SET 0x01/0x02 data=[03,00] | Config | Device mode write |
| SET 0x06/0x02 sub=00,08 data=[00] | Config | Idle/sleep config (class 0x06 — previously TARGET_NS with sub=00,00) |
| GET 0x01/0x82 | Status | Device state query |
| GET 0x01/0x83 | Status | Connection state? |
| GET 0x05/0x80 sub=00,01 | Config | |
| GET 0x05/0x85 sub=00,01 | Config | |
| GET 0x05/0x81 sub=00,01 | Config | |
| GET 0x05/0x8a sub=00,01 | Config | |
| GET 0x05/0x8d sub=00,01 | Config | |

**Notable:** Class 0x06 SET works with sub2=0x08 (previously failed with sub=00,00 returning TARGET_NS). The sub-params encode the target ID.

### Class Support over BLE

| Class (byte 4) | Working Cmds | Status | Notes |
|-----------------|-------------|--------|-------|
| 0x00 | 0x81 (fw), 0x83 (type) | SUCCESS | Device info (USB-compatible class) |
| 0x01 | 0xA0 (status), 0x86 (status) | SUCCESS | Init/status queries |
| 0x02-0x04 | none | NOT_SUPPORTED | |
| 0x05 | 0x80, 0x81, 0x84, 0x87 (GET); 0x07 (SET) | SUCCESS | Config state + write |
| 0x06 | none | TARGET_NOT_SUPPORTED | Idle/sleep (needs target?) |
| 0x07 | 0x80 (battery) | SUCCESS | Battery (USB-compatible class) |
| 0x08-0x0F | none | NOT_SUPPORTED | USB lighting class 0x0F doesn't exist on BLE |
| **0x10** | **0x81-0x83, 0x85 (GET); 0x03, 0x05 (SET)** | **SUCCESS** | **BLE-native lighting** |

### No Encryption Required

Protocol30 is plaintext — no authentication, encryption, or challenge-response at the Protocol30 level. BLE SMP pairing is NOT required for any command (SMP returns PAIR_NOT_ALLOWED anyway). The three bugs that caused all SET commands to fail were:
1. **20-byte padding** — keyboard requires exact byte lengths
2. **Single-write SETs** — SET commands need split writes (header + data as separate ATT writes)
3. **Wrong sub-param** — SET brightness/color need sub1=0x01 (not 0x00)

### Synapse Source Structure

Synapse 3 is an Electron app. BLE device logic:
- `electron/modules/noble/constants.js` — UUIDs, timeouts, vendor IDs
- `electron/modules/noble/strategy/protocol30.js` — transport (write/read/notify)
- `electron/modules/noble/strategy/protocol30Layla.js` — keyboard-specific transport variant
- `electron/modules/noble/index.js` — BLE connection manager, status decoder, IPC bridge
- Service Worker cache `6886.d85cef2c.chunk.js` — `rzDevice30Layla` class (keyboard command builder)

The `rzDevice30Layla` class constructs commands via `_createDataSend(commandClassArray, commandId, subCommandId, dataArray, packetSize)` and sends through `rzBLE.send()`.

## BLE GATT Service Map

Enumerated 2026-04-10 via WinRT and confirmed via MITM proxy GATT discovery:

| Service UUID | Name | Notes |
|---|---|---|
| 0x1800 | GAP | Device name "Joro", appearance 0x03C1 (keyboard) |
| 0x1801 | GATT | Service Changed indication |
| 0x180A | Device Info | Manufacturer "Razer", VID=0x068E, PID=0x02CE |
| 0x180F | Battery | Level: 0x64 (100%) — read + notify |
| 0x1812 | HID over GATT | Standard HID keyboard — works without Synapse |
| 52401523-... | **Razer Protocol30** | Synapse config channel |

### Razer Protocol30 Service (52401523-f97c-7f90-0e7f-6c6f4e36db1c)

| Characteristic | UUID suffix | Properties | GATT Handle | Purpose |
|---|---|---|---|---|
| TX (command) | `...1524` | write (0x08) — NO write-without-response | 69 | Command input |
| RX (response) | `...1525` | read, notify | 71 | Response/notification |
| RX2 (secondary) | `...1526` | read, notify | 74 | Secondary channel (reads as 8 zero bytes) |

## MITM Proxy

### Architecture

nRF52840 dongle (PCA10059) running Zephyr RTOS v4.1.0:
- **Central role:** connects to real Joro keyboard, discovers GATT, subscribes to notifications
- **Peripheral role:** advertises as "Joro", exposes mirrored GATT service for Synapse/scripts
- **Relay:** forwards writes PC→keyboard, notifications keyboard→PC, logs all traffic
- **Logging:** USB CDC serial (COM12) + SEGGER RTT via J-Link

### Source

`firmware/ble-mitm-proxy/` — 4 source files:
- `main.c` — boot, BT init, connection callbacks, main loop
- `central.c` — scan, connect, GATT discovery, subscribe, write upstream
- `peripheral.c` — GATT service definition, advertising, notify downstream
- `relay.c` — bidirectional relay with hex logging

### Build & Flash

```bash
export ZEPHYR_SDK_INSTALL_DIR="C:/Users/mklod/zephyr-sdk-0.17.0"
export ZEPHYR_BASE="C:/Users/mklod/zephyrproject/zephyr"
export PATH="/c/Program Files/CMake/bin:$PATH"
cd C:/Users/mklod/zephyrproject

# Copy source (west can't handle cross-drive L: → C:)
cp -r L:/PROJECTS/razer-joro/firmware/ble-mitm-proxy/* ble-mitm-proxy/

# Build
west build -b nrf52840dongle/nrf52840 ble-mitm-proxy --build-dir ble-mitm-proxy/build

# Flash via J-Link SWD
nrfutil device program --firmware ble-mitm-proxy/build/zephyr/zephyr.hex \
  --options chip_erase_mode=ERASE_ALL --traits jlink
nrfutil device reset --traits jlink
```

### Key Zephyr Config

```
CONFIG_FLASH_LOAD_OFFSET=0x0    # No bootloader, direct SWD flash
CONFIG_BT_MAX_CONN=3            # upstream + downstream + reconnect spare
CONFIG_LOG_MODE_DEFERRED=y      # Prevents stack overflow in BT RX thread
CONFIG_BT_RX_STACK_SIZE=8192    # BLE central+peripheral needs large stacks
CONFIG_BT_SMP=y                 # Windows requires SMP for pairing
CONFIG_BT_SETTINGS=y            # Persist bonds across resets (NVS)
CONFIG_BT_DIS=y                 # Device Information Service (VID/PID)
CONFIG_BT_BAS=y                 # Battery Service
```

### Status

- **Upstream (proxy→keyboard): WORKING** — connects, discovers GATT, receives notifications
- **Downstream (PC→proxy): PARTIALLY WORKING** — Windows pairs, Synapse sees device, but Synapse doesn't write to custom service (likely needs HID-over-GATT)
- **Python probe scripts work** — successfully wrote to proxy and received keyboard responses via relay

## Probe Results

### Raw Byte Probes (via `ble_auth_probe.py`)

Sent various byte patterns to char 1524, received responses on 1525:

```
TX: 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
RX: 01 00 00 00 00 00 00 03 2a e5 10 14 67 a7 71 31 ed f5 60 d9

TX: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
RX: 00 00 00 00 00 00 00 03 2a e5 10 14 67 a7 71 31 ed f5 60 d9

TX: ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff
RX: ff 00 00 00 00 00 00 03 2a e5 10 14 67 a7 71 31 ed f5 60 d9
```

**Pattern:** Keyboard echoes byte 0 (transactionId), always returns status 0x03 (Failure) + 12 bytes session data. Only byte 0 is reflected; bytes 1-19 ignored. The 12-byte session data is constant within a session.

### Protocol30 Formatted Probes — 20-byte padded (via `ble_proto30_probe.py`)

All 20-byte padded commands return status 0x03 (FAILURE). But 8-byte header-only returns 0x05 (NOT_SUPPORTED) — proving the keyboard parses headers correctly when not padded.

### Protocol30 8-byte Header Probes — BREAKTHROUGH (via `ble_proto30_8byte.py`)

Two commands returned SUCCESS:

```
TX: 02 00 00 00 00 81 00 00  (GET firmware: class=0x00, cmd=0x81)
RX: 02 04 00 00 00 00 00 02  (status=SUCCESS, data_len=4)
RX: 01 00 04 00 ...          (firmware version 1.0.4.0)

TX: 07 00 00 00 07 80 00 00  (GET battery: class=0x07, cmd=0x80)
RX: 07 04 00 00 00 00 00 02  (status=SUCCESS, data_len=4)
RX: 40 c2 c5 86 ...          (battery data)
```

This confirms: **byte 4 = USB class, byte 5 = USB command ID. Same class/cmd values as USB protocol.**

## Keyboard BLE Behavior

- **3 BLE pairing slots** — exclusive (one active at a time), switched via Fn+1/2/3
- **Long-press slot button (5s):** clears bond, enters pairing mode (slow blink)
- **Short-press slot button:** reconnects to existing bond (fast blink)
- **Each slot uses different MAC:** seen suffixes 2F:9F, 2F:A2, 2F:A3, 2F:A4, 2F:A5
- **USB/BLE switch:** physical switch. USB mode disables BLE advertising entirely
- **Bonded slot stays connected** — keyboard maintains connection with bonded central indefinitely
- **Unbonded slot disconnects after ~2s** — keyboard terminates if central doesn't match bonded address
- **Unsolicited notification on connect:** keyboard sends Protocol30 status on 1525 immediately after GATT subscription

## Firmware Update

- **DFU PID:** `0x110E` (VID `0x1532`) — keyboard switches to this PID during firmware update
- **DFU mode:** single HID interface, accessed via USB only
- **Normal PID:** `0x02CD` (wired), `0x02CE` (BLE/dongle)
- **FW update requires USB cable** — not available over BLE
- Captured PID transition in `captures/fw_update_usb.log`

### Firmware Version Impact on BLE

| Behavior | Old FW (v1.0.4.0) | New FW (v1.2.2.0) |
|----------|-------------------|---------------------|
| GET firmware (8B, no padding) | SUCCESS | SUCCESS |
| GET battery (8B, no padding) | SUCCESS | SUCCESS |
| SET brightness (split write, sub1=01) | Not tested | SUCCESS |
| SET color (split write, sub1=01) | Not tested | SUCCESS |
| Response byte 8+ | `2a e5 10 14 ...` (session data) | `00 00 00 ...` or `ca 00 00 ...` |

**CORRECTION:** The earlier conclusion that "new firmware requires auth" was WRONG. The failures were caused by the 20-byte padding bug in the MITM proxy's `central_write_to_keyboard()`, which padded all writes to 20 bytes. Once fixed to send exact byte lengths, all GET commands work on both firmware versions. SET commands require the split write protocol (not auth).

## Hardware

- **nRF52840 Dongle (PCA10059):** MITM proxy firmware, J-Link via SWD headers
- **J-Link Mini:** SWD programming + RTT debug output. S/N: 801043164
- **Barrot BLE 5.4 Adapter:** USB\VID_33FA&PID_0010, driver v17.55.18.936. For Windows BLE pairing.
- **Intel Wireless Bluetooth:** Onboard, in Error state, unusable.

## Tools Installed

- `nrfutil` v8.1.1 at `C:\Users\mklod\bin\nrfutil.exe`
- `nrfutil ble-sniffer` v0.18.0
- `nrfutil device` — J-Link programming + reset
- Wireshark 4.6.4 + tshark
- SEGGER J-Link V9.34a at `C:\Program Files\SEGGER\JLink_V934a\`
- Python bleak + winrt packages
- Zephyr SDK 0.17.0 at `C:\Users\mklod\zephyr-sdk-0.17.0\`
- Zephyr workspace at `C:\Users\mklod\zephyrproject\` (v4.1.0)
- CMake 4.3.1, Ninja 1.13.2

## Scripts

- `scripts/ble_gatt_enum.py` — enumerate Joro GATT services via WinRT
- `scripts/ble_test_command.py` — early BLE command testing (pre-MITM, failed due to protocol mismatch)
- `scripts/ble_auth_probe.py` — raw byte probing through MITM proxy (worked, revealed response pattern)
- `scripts/ble_auth_probe2.py` — comprehensive byte scanning (all byte positions, nonce echo tests)
- `scripts/ble_proto30_probe.py` — Protocol30 formatted command probing (needs re-run with active connection)

## Reverse Engineering History

### Option 1: BLE Sniffer (FAILED)
Single-radio nRF52840 sniffer couldn't follow connections to data channels. 8+ attempts, CONNECT_IND captured but marked malformed.

### Option 2: MITM Proxy (CURRENT — WORKING)
nRF52840 dongle with Zephyr firmware acts as GATT relay. Upstream to keyboard works. Python scripts can send/receive through proxy. Need to send properly formatted Protocol30 commands.

### Option 3: Synapse Source Decompilation (BREAKTHROUGH)
Extracted Protocol30 packet format from Synapse Electron app's cached web modules. No encryption. The protocol is structurally similar to USB but with a different header format (8-byte header vs USB 90-byte packet).
