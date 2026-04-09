# proto/usb_transport.py
# Last modified: 2026-04-09
"""
USB HID transport for Razer Joro.

Sends and receives 90-byte feature reports via hidapi.
"""

import hid
import time

RAZER_VID = 0x1532
JORO_PID = 0x02CD
PACKET_SIZE = 90


class UsbTransport:
    def __init__(self, device_path: bytes | None = None):
        self.device = hid.device()
        self._path = device_path

    def open(self, device_path: bytes | None = None):
        """Open the Joro HID device.

        Args:
            device_path: Specific HID path from enumeration. If None, tries
                         to open by VID/PID (uses first matching interface).
        """
        path = device_path or self._path
        if path:
            self.device.open_path(path)
        else:
            self.device.open(RAZER_VID, JORO_PID)
        print(f"Opened: {self.device.get_product_string()}")

    def close(self):
        self.device.close()

    def send_packet(self, packet: bytes) -> bytes:
        """Send a 90-byte packet as feature report, read response.

        Returns the 90-byte response packet.
        """
        # Feature report: prepend report_id 0x00
        # hidapi send_feature_report expects [report_id, ...data]
        report = bytes([0x00]) + packet[1:]  # packet[0] is already report_id
        written = self.device.send_feature_report(report)
        if written < 0:
            raise IOError(f"send_feature_report failed: {self.device.error()}")

        # Small delay for device to process
        time.sleep(0.02)

        # Read feature report response
        response = self.device.get_feature_report(0x00, PACKET_SIZE)
        if not response:
            raise IOError(f"get_feature_report failed: {self.device.error()}")

        return bytes(response)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
