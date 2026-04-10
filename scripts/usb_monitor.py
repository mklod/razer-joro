# Last modified: 2026-04-10--1440
# USB monitor — captures HID reports from the Joro during firmware update
# Polls the interrupt endpoint and logs all data to a file

import usb.core
import usb.util
import time
import sys
import os

VID = 0x1532
PID_WIRED = 0x02CD

outfile = "L:/PROJECTS/razer-joro/captures/fw_update_usb.log"

def hex_dump(data):
    return " ".join(f"{b:02x}" for b in data)

def find_joro():
    dev = usb.core.find(idVendor=VID, idProduct=PID_WIRED)
    if dev is None:
        # Try DFU mode PID if keyboard switches during update
        for pid in [PID_WIRED, 0x02CD, 0x0300, 0x0301, 0xFF00]:
            dev = usb.core.find(idVendor=VID, idProduct=pid)
            if dev:
                print(f"Found device VID={VID:04x} PID={pid:04x}")
                return dev
        return None
    print(f"Found Joro at VID={VID:04x} PID={PID_WIRED:04x}")
    return dev

def monitor():
    f = open(outfile, "w")
    f.write(f"# USB FW Update Monitor - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    print(f"Logging to {outfile}")
    print("Monitoring USB... Press Ctrl+C to stop.")
    print("Start the firmware update now.")

    last_vid_pid = None
    while True:
        try:
            dev = find_joro()
            if dev is None:
                # Scan for any Razer device
                for d in usb.core.find(find_all=True, idVendor=VID):
                    vid_pid = f"{d.idVendor:04x}:{d.idProduct:04x}"
                    if vid_pid != last_vid_pid:
                        msg = f"[{time.strftime('%H:%M:%S')}] Razer device: {vid_pid}"
                        print(msg)
                        f.write(msg + "\n")
                        last_vid_pid = vid_pid
                    dev = d
                    break
                if dev is None:
                    time.sleep(0.5)
                    continue

            vid_pid = f"{dev.idVendor:04x}:{dev.idProduct:04x}"
            if vid_pid != last_vid_pid:
                msg = f"[{time.strftime('%H:%M:%S')}] Device: {vid_pid}, configs={dev.bNumConfigurations}"
                print(msg)
                f.write(msg + "\n")
                # Log device descriptor
                f.write(f"  bcdDevice={dev.bcdDevice:04x}\n")
                for cfg in dev:
                    f.write(f"  Config {cfg.bConfigurationValue}: {cfg.bNumInterfaces} interfaces\n")
                    for intf in cfg:
                        f.write(f"    Interface {intf.bInterfaceNumber} alt={intf.bAlternateSetting} "
                                f"class={intf.bInterfaceClass:02x} subclass={intf.bInterfaceSubClass:02x} "
                                f"protocol={intf.bInterfaceProtocol:02x}\n")
                        for ep in intf:
                            f.write(f"      EP 0x{ep.bEndpointAddress:02x} "
                                    f"type={usb.util.endpoint_type(ep.bmAttributes)} "
                                    f"maxpkt={ep.wMaxPacketSize}\n")
                f.flush()
                last_vid_pid = vid_pid

            time.sleep(1)

        except usb.core.USBError as e:
            msg = f"[{time.strftime('%H:%M:%S')}] USB Error: {e}"
            print(msg)
            f.write(msg + "\n")
            f.flush()
            time.sleep(1)
        except KeyboardInterrupt:
            break

    f.close()
    print(f"\nDone. Log saved to {outfile}")

if __name__ == "__main__":
    monitor()
