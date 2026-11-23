import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hardware import usb_camera
from protocol import camera_tool


class UsbCameraTests(unittest.TestCase):
    def test_auto_detection_skips_metadata_and_tracks_replug(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        by_id = root / "by-id"
        by_id.mkdir()
        capture = by_id / "usb-webcam-video-index0"
        (by_id / "usb-webcam-video-index1").symlink_to("/dev/null")
        with patch.object(usb_camera.config, "USB_CAMERA_DEVICE", "auto"):
            self.assertIsNone(usb_camera.usb_camera_device(root))
            capture.symlink_to("/dev/null")
            self.assertEqual(usb_camera.usb_camera_device(root), capture)
            capture.unlink()
            self.assertIsNone(usb_camera.usb_camera_device(root))
            capture.symlink_to("/dev/null")
            self.assertEqual(usb_camera.usb_camera_device(root), capture)

    def test_explicit_disconnected_camera_does_not_switch_to_csi(self):
        with patch.object(usb_camera.config, "USB_CAMERA_DEVICE", "/nonexistent/usb-camera"):
            with self.assertRaisesRegex(RuntimeError, "USB camera is disconnected"):
                camera_tool._selected_usb("")
            self.assertIsNone(camera_tool._selected_usb("rpicam-vid"))
