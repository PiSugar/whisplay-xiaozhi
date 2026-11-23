from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hardware.whisplay_model import detect_plus_output


class WhisplayModelTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.drm = root / "drm"
        self.device_tree = root / "device-tree"

    def add_panel(self, compatible, status=b"okay\0"):
        panel = self.device_tree / "soc" / "dsi@0" / "panel@0"
        panel.mkdir(parents=True)
        (panel / "compatible").write_bytes(compatible)
        (panel / "status").write_bytes(status)

    def add_connector(self, name, mode, status="connected"):
        connector = self.drm / name
        connector.mkdir(parents=True)
        (connector / "status").write_text(status + "\n")
        (connector / "modes").write_text(mode + "\n")

    def test_other_dsi_panel_with_same_resolution_uses_regular_ui(self):
        self.add_panel(b"other,480x640\0")
        self.add_connector("card1-DSI-1", "480x640")
        self.assertIsNone(detect_plus_output(self.drm, self.device_tree))

    def test_plus_selects_its_connected_dsi_output(self):
        self.add_panel(b"pisugar,st7701p-480x640\0")
        self.add_connector("card0-HDMI-A-1", "480x640")
        self.add_connector("card0-DSI-1", "800x480")
        self.add_connector("card0-DSI-2", "480x640")
        self.assertEqual(detect_plus_output(self.drm, self.device_tree), "DSI-2")

    def test_disabled_plus_overlay_uses_regular_ui(self):
        self.add_panel(b"pisugar,st7701p-480x640\0", status=b"disabled\0")
        self.assertIsNone(detect_plus_output(self.drm, self.device_tree))

    def test_configured_plus_is_recognized_before_drm_is_ready(self):
        self.add_panel(b"pisugar,st7701p-480x640\0")
        self.assertEqual(detect_plus_output(self.drm, self.device_tree), "DSI-1")
