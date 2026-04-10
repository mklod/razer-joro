# Last modified: 2026-04-10--1530
# Windows BT HCI capture via ETW
# Captures all Bluetooth HCI traffic, converts to pcap for Wireshark analysis
#
# Usage:
#   python bt_hci_capture.py start   — start capture
#   python bt_hci_capture.py stop    — stop capture and convert
#   python bt_hci_capture.py status  — check if capture is running

import subprocess
import sys
import os
import struct
import time
from pathlib import Path

TRACE_NAME = "BT_HCI_Capture"
ETL_PATH = r"L:\PROJECTS\razer-joro\captures\bt_hci.etl"
PCAP_PATH = r"L:\PROJECTS\razer-joro\captures\bt_hci.pcap"
PROVIDER_GUID = "{8A1F9517-3A8C-4A9E-A018-4F17A200F277}"  # Microsoft-Windows-BTH-BTHPORT


def run(cmd, check=True):
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip():
        print(f"    {r.stdout.strip()}")
    if r.stderr.strip():
        print(f"    ERR: {r.stderr.strip()}")
    if check and r.returncode != 0:
        print(f"    Return code: {r.returncode}")
    return r


def start():
    print("Starting BT HCI ETW capture...")
    # Stop any existing trace first
    run(f'logman stop "{TRACE_NAME}" -ets', check=False)
    time.sleep(0.5)

    # Start new trace — capture ALL BT events at maximum verbosity
    r = run(
        f'logman create trace "{TRACE_NAME}" '
        f'-ow -o "{ETL_PATH}" '
        f'-p {PROVIDER_GUID} 0xffffffffffffffff 0xff '
        f'-nb 16 16 -bs 1024 -mode Circular -f bincirc -max 4096 -ets'
    )
    if r.returncode == 0:
        print(f"\nCapture RUNNING. ETL file: {ETL_PATH}")
        print("Now:")
        print("  1. Switch keyboard to BLE mode")
        print("  2. Connect to Windows via Bluetooth")
        print("  3. Open Synapse and let it detect the keyboard")
        print("  4. Toggle lighting on/off in Synapse")
        print("  5. Run: python bt_hci_capture.py stop")
    else:
        print("Failed to start capture. Try running as Administrator.")


def stop():
    print("Stopping BT HCI capture...")
    r = run(f'logman stop "{TRACE_NAME}" -ets')
    if r.returncode == 0:
        print(f"\nCapture stopped. ETL file: {ETL_PATH}")
        size = os.path.getsize(ETL_PATH) if os.path.exists(ETL_PATH) else 0
        print(f"  Size: {size:,} bytes")
        print(f"\nTo analyze:")
        print(f"  1. Try: python bt_hci_capture.py parse")
        print(f"  2. Or open in Windows Performance Analyzer (WPA)")
        print(f"  3. Or use: netsh trace convert input={ETL_PATH} output=bt_hci.txt")
    else:
        print("No active capture found.")


def status():
    r = run(f'logman query "{TRACE_NAME}" -ets', check=False)
    if r.returncode != 0:
        print("No active BT HCI capture.")


def parse_etl():
    """Try to convert ETL to readable format using available tools."""
    if not os.path.exists(ETL_PATH):
        print(f"ETL file not found: {ETL_PATH}")
        return

    print(f"ETL file: {ETL_PATH} ({os.path.getsize(ETL_PATH):,} bytes)")

    # Try netsh trace convert (built-in, gives text output)
    txt_path = ETL_PATH.replace(".etl", ".txt")
    print(f"\nConverting to text via netsh...")
    r = run(f'netsh trace convert input="{ETL_PATH}" output="{txt_path}"', check=False)
    if r.returncode == 0 and os.path.exists(txt_path):
        print(f"Text output: {txt_path}")

    # Try btetlparse if available
    btetlparse = None
    for p in [r"C:\Program Files (x86)\Windows Kits\10\Tools\x64\Bluetooth\btetlparse.exe",
              r"C:\Program Files\Windows Kits\10\Tools\x64\Bluetooth\btetlparse.exe"]:
        if os.path.exists(p):
            btetlparse = p
            break

    if btetlparse:
        print(f"\nFound btetlparse: {btetlparse}")
        cfa_path = ETL_PATH.replace(".etl", ".cfa")
        run(f'"{btetlparse}" "{ETL_PATH}" "{cfa_path}"', check=False)
        if os.path.exists(cfa_path):
            print(f"BTSnoop output: {cfa_path}")
            print(f"Open in Wireshark: wireshark {cfa_path}")
    else:
        print("\nbtetlparse not found. Install Windows Driver Kit (WDK) for .etl → .cfa conversion.")
        print("Or download from: https://learn.microsoft.com/en-us/windows-hardware/drivers/download-the-wdk")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bt_hci_capture.py [start|stop|status|parse]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "parse":
        parse_etl()
    else:
        print(f"Unknown command: {cmd}")
