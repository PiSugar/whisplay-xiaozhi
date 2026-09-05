import importlib.util
import sys
import types
import unittest
from importlib.machinery import ModuleSpec

if importlib.util.find_spec("dotenv") is None:
    dotenv = types.ModuleType("dotenv")
    dotenv.__spec__ = ModuleSpec("dotenv", loader=None)
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

from hardware.battery import BatteryMonitor


class BatteryMonitorTests(unittest.TestCase):
    def test_known_battery_level_is_white_when_not_charging(self):
        battery = BatteryMonitor()
        for level in (5, 25, 72, 100):
            battery.level = level
            battery.charging = False
            self.assertEqual(battery.get_color(), (255, 255, 255))

    def test_charging_battery_is_green(self):
        battery = BatteryMonitor()
        battery.level = 72
        battery.charging = True
        self.assertEqual(battery.get_color(), (52, 211, 81))

    def test_unknown_battery_level_remains_neutral(self):
        battery = BatteryMonitor()
        self.assertEqual(battery.get_color(), (128, 128, 128))


if __name__ == "__main__":
    unittest.main()
