# proto/usb_transport.py
# Last modified: 2026-04-09--1530
"""
USB transport for Razer Joro via pyusb control transfers.

Uses raw USB SET_REPORT/GET_REPORT control messages (not hidapi feature reports).
This matches how openrazer communicates with the device on Linux.

Requires: pyusb + libusb backend (pip install pyusb libusb)
"""

import time
import usb.core
import usb.backend.libusb1

RAZER_VID = 0x1532
JORO_PID = 0x02CD
PACKET_SIZE = 90

# USB control transfer parameters (Joro-specific)
WINDEX = 0x03              # wIndex: HID interface index for Joro commands
WVALUE = 0x0300            # Feature report type (0x03) + report ID (0x00)
SET_REPORT_BMRT = 0x21     # USB_TYPE_CLASS | USB_RECIP_INTERFACE | USB_DIR_OUT
SET_REPORT_BREQ = 0x09     # HID_REQ_SET_REPORT
GET_REPORT_BMRT = 0xA1     # USB_TYPE_CLASS | USB_RECIP_INTERFACE | USB_DIR_IN
GET_REPORT_BREQ = 0x01     # HID_REQ_GET_REPORT

# Default libusb DLL path (bundled with pip install libusb)
_LIBUSB_DLL = r"C:\Users\mklod\AppData\Local\razer-joro-venv\Lib\site-packages\libusb\_platform\windows\x86_64\libusb-1.0.dll"


def _get_backend():
    return usb.backend.libusb1.get_backend(find_library=lambda x: _LIBUSB_DLL)


class UsbTransport:
    def __init__(self):
        self.device = None

    def open(self):
        """Open the Joro USB device via libusb."""
        backend = _get_backend()
        self.device = usb.core.find(idVendor=RAZER_VID, idProduct=JORO_PID, backend=backend)
        if self.device is None:
            raise IOError("Razer Joro not found. Is it connected via USB?")
        print(f"Opened: {self.device.product}")

    def close(self):
        if self.device:
            usb.util.dispose_resources(self.device)
            self.device = None

    def send_packet(self, packet: bytes) -> bytes:
        """Send a 90-byte packet via USB control transfer, read response.

        Returns the 90-byte response packet.
        """
        if self.device is None:
            raise IOError("Device not open")

        self.device.ctrl_transfer(SET_REPORT_BMRT, SET_REPORT_BREQ, WVALUE, WINDEX, packet)
        time.sleep(0.01)
        resp = self.device.ctrl_transfer(GET_REPORT_BMRT, GET_REPORT_BREQ, WVALUE, WINDEX, PACKET_SIZE)
        return bytes(resp)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
