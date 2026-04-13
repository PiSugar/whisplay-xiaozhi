"""
WhisplayBoard — hardware abstraction for PiSugar Whisplay HAT.

Adapted from whisplay-chatbot python/whisplay.py.
Supports Raspberry Pi and Radxa platforms.
"""

import time
import threading
import logging

log = logging.getLogger("whisplay_board")

try:
    import spidev
except ImportError:
    spidev = None


# ==================== Platform Detection ====================
def _detect_platform():
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip("\0").strip()
            if "Raspberry" in model:
                return "rpi", model
            elif "Radxa" in model:
                return "radxa", model
    except Exception:
        pass
    try:
        with open("/proc/device-tree/compatible", "r") as f:
            compat = f.read()
            if "radxa" in compat.lower():
                parts = compat.split("\0")
                model = parts[0] if parts else "Unknown Radxa"
                return "radxa", model
    except Exception:
        pass
    return "unknown", "Unknown"


PLATFORM, PLATFORM_MODEL = _detect_platform()

lgpio = None
gpiod = None

if PLATFORM == "rpi":
    try:
        import lgpio as _lgpio
        lgpio = _lgpio
    except ImportError:
        raise RuntimeError(
            "lgpio not found.\n"
            "Install: sudo apt install python3-lgpio  (or: pip install lgpio)"
        )
elif PLATFORM == "radxa":
    try:
        import gpiod as _gpiod
        gpiod = _gpiod
    except ImportError:
        pass
else:
    try:
        import lgpio as _lgpio
        lgpio = _lgpio
        PLATFORM = "rpi"
        PLATFORM_MODEL = "Unknown Raspberry Pi"
    except ImportError:
        try:
            import gpiod as _gpiod
            gpiod = _gpiod
            PLATFORM = "radxa"
            PLATFORM_MODEL = "Unknown Radxa"
        except ImportError:
            pass


# ==================== Raspberry Pi Pin Mapping ====================
# Physical BOARD pin -> BCM GPIO number
RPI_BOARD_TO_BCM = {
    3: 2, 5: 3, 7: 4, 8: 14, 10: 15, 11: 17, 12: 18, 13: 27,
    15: 22, 16: 23, 18: 24, 19: 10, 21: 9, 22: 25, 23: 11, 24: 8,
    26: 7, 27: 0, 28: 1, 29: 5, 31: 6, 32: 12, 33: 13, 35: 19,
    36: 16, 37: 26, 38: 20, 40: 21,
}


def _detect_rpi_gpiochip():
    """Detect GPIO chip number. Pi 5 uses gpiochip4 (RP1), older models use gpiochip0."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            if "Pi 5" in f.read():
                return 4
    except Exception:
        pass
    return 0


# ==================== Radxa Pin Mappings ====================
RADXA_ZERO3_PIN_MAP = {
    3: (1, 0), 5: (1, 1), 7: (3, 20), 8: (0, 25),
    10: (0, 24), 11: (3, 1), 12: (3, 3), 13: (3, 2),
    15: (3, 8), 16: (3, 9), 18: (3, 10), 19: (4, 19),
    21: (4, 21), 22: (3, 17), 23: (4, 18), 24: (4, 22),
    26: (4, 25), 27: (4, 10), 28: (4, 11), 29: (3, 11),
    31: (3, 12), 32: (3, 18), 33: (3, 19), 35: (3, 4),
    36: (3, 7), 37: (1, 4), 38: (3, 6), 40: (3, 5),
}

RADXA_CUBIE_A7Z_PIN_MAP = {
    3: (0, 311), 5: (0, 310), 7: (0, 32), 8: (0, 41),
    10: (0, 42), 11: (0, 33), 12: (0, 37), 13: (1, 6),
    15: (1, 7), 16: (0, 312), 18: (0, 313), 19: (0, 108),
    21: (0, 109), 22: (1, 5), 23: (0, 107), 24: (0, 106),
    26: (0, 110), 27: (0, 113), 28: (0, 112), 29: (0, 34),
    31: (0, 35), 32: (1, 37), 33: (1, 35), 35: (0, 38),
    36: (0, 36), 37: (1, 36), 38: (0, 40), 40: (0, 39),
}


def _detect_radxa_board():
    try:
        with open("/proc/device-tree/compatible", "r") as f:
            compat = f.read().lower()
            if "cubie-a7z" in compat:
                return "cubie-a7z"
            elif "cubie-a7a" in compat:
                return "cubie-a7a"
            elif "cubie-a7s" in compat:
                return "cubie-a7s"
    except Exception:
        pass
    return "zero3w"


# ==================== Software PWM ====================
class SoftPWM:
    def __init__(self, set_value_func, frequency=100, stop_value=0):
        self._set_value = set_value_func
        self.frequency = frequency
        self.stop_value = stop_value
        self.duty_cycle = 0.0
        self._running = False
        self._thread = None

    def start(self, duty_cycle=0):
        self.duty_cycle = float(duty_cycle)
        self._running = True
        self._thread = threading.Thread(target=self._pwm_loop, daemon=True)
        self._thread.start()

    def ChangeDutyCycle(self, duty_cycle):
        self.duty_cycle = max(0.0, min(100.0, float(duty_cycle)))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        try:
            self._set_value(self.stop_value)
        except Exception:
            pass

    def _pwm_loop(self):
        while self._running:
            period = 1.0 / self.frequency
            dc = self.duty_cycle
            if dc <= 0:
                self._set_value(0)
                time.sleep(period)
            elif dc >= 100:
                self._set_value(1)
                time.sleep(period)
            else:
                on_time = period * dc / 100.0
                off_time = period - on_time
                self._set_value(1)
                time.sleep(on_time)
                self._set_value(0)
                time.sleep(off_time)


# ==================== WhisplayBoard ====================
class WhisplayBoard:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280
    CornerHeight = 20

    # Physical BOARD pin numbers
    DC_PIN = 13
    RST_PIN = 7
    LED_PIN = 15
    RED_PIN = 22
    GREEN_PIN = 18
    BLUE_PIN = 16
    BUTTON_PIN = 11

    def __init__(self):
        self.platform = PLATFORM
        self.backlight_pwm = None
        self._current_r = 0
        self._current_g = 0
        self._current_b = 0
        self.button_press_callback = None
        self.button_release_callback = None

        if self.platform == "rpi":
            self._init_rpi()
        elif self.platform == "radxa":
            self._init_radxa()
        else:
            raise RuntimeError(f"Unsupported platform: {self.platform} (no GPIO library found)")

        self.previous_frame = None
        self._detect_hardware_version()
        self.set_backlight(0)
        self._reset_lcd()
        self._init_display()
        self.fill_screen(0)

    # ==================== Raspberry Pi ====================
    def _init_rpi(self):
        chip_num = _detect_rpi_gpiochip()
        self._lgpio_h = lgpio.gpiochip_open(chip_num)
        h = self._lgpio_h

        # Build BCM pin lookup for our BOARD pins
        self._bcm = {pin: RPI_BOARD_TO_BCM[pin] for pin in [
            self.DC_PIN, self.RST_PIN, self.LED_PIN,
            self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN,
            self.BUTTON_PIN,
        ]}

        # LCD control pins (output)
        for pin in [self.DC_PIN, self.RST_PIN]:
            lgpio.gpio_claim_output(h, self._bcm[pin], 0)
        lgpio.gpio_claim_output(h, self._bcm[self.LED_PIN], 0)  # LOW = backlight on

        # RGB LED pins (output, initial HIGH = LED off, active-low)
        for pin in [self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN]:
            lgpio.gpio_claim_output(h, self._bcm[pin], 1)
        self.red_pwm = self._create_rpi_rgb_pwm(self.RED_PIN, "red")
        self.green_pwm = self._create_rpi_rgb_pwm(self.GREEN_PIN, "green")
        self.blue_pwm = self._create_rpi_rgb_pwm(self.BLUE_PIN, "blue")
        self.red_pwm.start(0)
        self.green_pwm.start(0)
        self.blue_pwm.start(0)

        # Button (input with edge detection via lgpio alerts)
        lgpio.gpio_claim_alert(h, self._bcm[self.BUTTON_PIN],
                               lgpio.BOTH_EDGES, lgpio.SET_PULL_NONE)
        self._lgpio_cb = lgpio.callback(
            h, self._bcm[self.BUTTON_PIN], lgpio.BOTH_EDGES,
            self._button_event_lgpio)

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 100_000_000
        self.spi.mode = 0b00

    def _rpi_pin_can_drive_low(self, pin):
        h = self._lgpio_h
        gpio = self._bcm[pin]
        lgpio.gpio_claim_output(h, gpio, 1)
        time.sleep(0.02)
        lgpio.gpio_write(h, gpio, 0)
        time.sleep(0.02)
        can_drive_low = lgpio.gpio_read(h, gpio) == 0
        lgpio.gpio_claim_output(h, gpio, 1)
        return can_drive_low

    def _rpi_set_rgb_sink_state(self, pin, value):
        if not hasattr(self, "_rgb_lock"):
            self._rgb_lock = threading.Lock()
        h = self._lgpio_h
        gpio = self._bcm[pin]
        with self._rgb_lock:
            try:
                if value:
                    lgpio.gpio_claim_output(h, gpio, 1)
                else:
                    lgpio.gpio_claim_input(h, gpio, lgpio.SET_PULL_DOWN)
            except Exception:
                try:
                    lgpio.gpio_write(h, gpio, 1 if value else 0)
                except Exception:
                    pass

    def _rpi_set_rgb_output_state(self, pin, value):
        lgpio.gpio_write(self._lgpio_h, self._bcm[pin], 1 if value else 0)

    def _rpi_set_backlight_state(self, value):
        lgpio.gpio_write(self._lgpio_h, self._bcm[self.LED_PIN], 1 if value else 0)

    def _create_rpi_rgb_pwm(self, pin, color_name):
        h = self._lgpio_h
        gpio = self._bcm[pin]

        if self._rpi_pin_can_drive_low(pin):
            return SoftPWM(
                lambda value, g=gpio: lgpio.gpio_write(h, g, value),
                100, stop_value=1,
            )
        print(f"Warning: GPIO pin {pin} for {color_name} LED cannot drive LOW; using fallback.")
        lgpio.gpio_claim_output(h, gpio, 1)
        return SoftPWM(
            lambda value, gpio_pin=pin: self._rpi_set_rgb_sink_state(gpio_pin, value),
            100, stop_value=1,
        )

    # ==================== Radxa ====================
    def _init_radxa(self):
        self._radxa_board = _detect_radxa_board()
        pin_map = RADXA_CUBIE_A7Z_PIN_MAP if self._radxa_board == "cubie-a7z" else RADXA_ZERO3_PIN_MAP

        self._gpio_chips = {}
        self._gpio_lines = {}

        pins_used = [self.DC_PIN, self.RST_PIN, self.LED_PIN,
                     self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN, self.BUTTON_PIN]

        for pin in pins_used:
            if pin not in pin_map:
                raise RuntimeError(f"Physical pin {pin} not in Radxa pin map")
            chip_num, _ = pin_map[pin]
            if chip_num not in self._gpio_chips:
                self._gpio_chips[chip_num] = gpiod.Chip(f"gpiochip{chip_num}")

        output_pins = [self.DC_PIN, self.RST_PIN, self.LED_PIN,
                       self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN]
        for pin in output_pins:
            chip_num, line_offset = pin_map[pin]
            chip = self._gpio_chips[chip_num]
            line = chip.get_line(line_offset)
            line.request(consumer="whisplay", type=gpiod.LINE_REQ_DIR_OUT, default_val=0)
            self._gpio_lines[pin] = line

        self._gpio_lines[self.LED_PIN].set_value(0)

        self.red_pwm = SoftPWM(self._gpio_lines[self.RED_PIN].set_value, 100, stop_value=1)
        self.green_pwm = SoftPWM(self._gpio_lines[self.GREEN_PIN].set_value, 100, stop_value=1)
        self.blue_pwm = SoftPWM(self._gpio_lines[self.BLUE_PIN].set_value, 100, stop_value=1)
        self.red_pwm.start(0)
        self.green_pwm.start(0)
        self.blue_pwm.start(0)

        chip_num, line_offset = pin_map[self.BUTTON_PIN]
        chip = self._gpio_chips[chip_num]
        btn_line = chip.get_line(line_offset)
        try:
            btn_line.request(consumer="whisplay-btn", type=gpiod.LINE_REQ_DIR_IN,
                             flags=gpiod.LINE_REQ_FLAG_BIAS_DISABLE)
        except Exception:
            btn_line.request(consumer="whisplay-btn", type=gpiod.LINE_REQ_DIR_IN)
        self._gpio_lines[self.BUTTON_PIN] = btn_line

        self._btn_thread_running = True
        self._btn_thread = threading.Thread(target=self._button_monitor_radxa, daemon=True)
        self._btn_thread.start()

        self.spi = spidev.SpiDev()
        if self._radxa_board == "cubie-a7z":
            self.spi.open(1, 0)
            self.spi.max_speed_hz = 48_000_000
        else:
            self.spi.open(3, 0)
            self.spi.max_speed_hz = 48_000_000
        self.spi.mode = 0b00

    def _button_monitor_radxa(self):
        btn_line = self._gpio_lines[self.BUTTON_PIN]
        last_state = btn_line.get_value()
        while self._btn_thread_running:
            try:
                state = btn_line.get_value()
                if state != last_state:
                    last_state = state
                    if state == 1 and self.button_press_callback:
                        self.button_press_callback()
                    elif state == 0 and self.button_release_callback:
                        self.button_release_callback()
            except Exception:
                pass
            time.sleep(0.01)

    # ==================== Cross-platform helpers ====================
    def _gpio_output(self, pin, value):
        if self.platform == "rpi":
            lgpio.gpio_write(self._lgpio_h, self._bcm[pin], 1 if value else 0)
        elif self.platform == "radxa":
            self._gpio_lines[pin].set_value(1 if value else 0)

    def _gpio_input(self, pin):
        if self.platform == "rpi":
            return lgpio.gpio_read(self._lgpio_h, self._bcm[pin])
        elif self.platform == "radxa":
            return self._gpio_lines[pin].get_value()

    # ==================== Hardware detection ====================
    def _detect_hardware_version(self):
        try:
            model = PLATFORM_MODEL
            if self.platform == "rpi":
                self.backlight_mode = "Zero" not in model or "2" in model
            else:
                self.backlight_mode = True
            print(f"Detected: {model}, Backlight PWM: {self.backlight_mode}")
        except Exception:
            self.backlight_mode = True

    # ==================== Backlight ====================
    def set_backlight(self, brightness):
        if self.backlight_mode:
            if self.backlight_pwm is None:
                if self.platform == "rpi":
                    self.backlight_pwm = SoftPWM(self._rpi_set_backlight_state, 1000, stop_value=1)
                elif self.platform == "radxa":
                    self.backlight_pwm = SoftPWM(
                        self._gpio_lines[self.LED_PIN].set_value, 1000, stop_value=1
                    )
                self.backlight_pwm.start(100)
            if 0 <= brightness <= 100:
                self.backlight_pwm.ChangeDutyCycle(100 - brightness)
        else:
            self._gpio_output(self.LED_PIN, 1 if brightness == 0 else 0)

    # ==================== LCD ====================
    def _reset_lcd(self):
        self._gpio_output(self.RST_PIN, 1)
        time.sleep(0.1)
        self._gpio_output(self.RST_PIN, 0)
        time.sleep(0.1)
        self._gpio_output(self.RST_PIN, 1)
        time.sleep(0.12)

    def _init_display(self):
        self._send_command(0x11)
        time.sleep(0.12)
        self._send_command(0x36, 0xC0)  # USE_HORIZONTAL = 1
        self._send_command(0x3A, 0x05)
        self._send_command(0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33)
        self._send_command(0xB7, 0x35)
        self._send_command(0xBB, 0x32)
        self._send_command(0xC2, 0x01)
        self._send_command(0xC3, 0x15)
        self._send_command(0xC4, 0x20)
        self._send_command(0xC6, 0x0F)
        self._send_command(0xD0, 0xA4, 0xA1)
        self._send_command(0xE0, 0xD0, 0x08, 0x0E, 0x09, 0x09, 0x05,
                           0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34)
        self._send_command(0xE1, 0xD0, 0x08, 0x0E, 0x09, 0x09, 0x15,
                           0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34)
        self._send_command(0x21)
        self._send_command(0x29)

    def _send_command(self, cmd, *args):
        self._gpio_output(self.DC_PIN, 0)
        self.spi.xfer2([cmd])
        if args:
            self._gpio_output(self.DC_PIN, 1)
            self._send_data(list(args))

    def _send_data(self, data):
        self._gpio_output(self.DC_PIN, 1)
        try:
            self.spi.writebytes2(data)
        except AttributeError:
            max_chunk = 4096
            for i in range(0, len(data), max_chunk):
                self.spi.writebytes(data[i:i + max_chunk])

    def set_window(self, x0, y0, x1, y1):
        # USE_HORIZONTAL = 1
        self._send_command(0x2A, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)
        self._send_command(0x2B, (y0 + 20) >> 8, (y0 + 20) & 0xFF,
                           (y1 + 20) >> 8, (y1 + 20) & 0xFF)
        self._send_command(0x2C)

    def fill_screen(self, color):
        self.set_window(0, 0, self.LCD_WIDTH - 1, self.LCD_HEIGHT - 1)
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        buf = bytes([high, low]) * (self.LCD_WIDTH * self.LCD_HEIGHT)
        self._send_data(list(buf))

    def draw_image(self, x, y, width, height, pixel_data):
        if (x + width > self.LCD_WIDTH) or (y + height > self.LCD_HEIGHT):
            return
        self.set_window(x, y, x + width - 1, y + height - 1)
        self._send_data(pixel_data)

    # ==================== RGB LED ====================
    def set_rgb(self, r, g, b):
        self.red_pwm.ChangeDutyCycle(100 - (r / 255 * 100))
        self.green_pwm.ChangeDutyCycle(100 - (g / 255 * 100))
        self.blue_pwm.ChangeDutyCycle(100 - (b / 255 * 100))
        self._current_r = r
        self._current_g = g
        self._current_b = b

    # ==================== Button ====================
    def button_pressed(self):
        return self._gpio_input(self.BUTTON_PIN) == 1

    def on_button_press(self, callback):
        self.button_press_callback = callback

    def on_button_release(self, callback):
        self.button_release_callback = callback

    def _button_event_lgpio(self, chip, gpio, level, tick):
        """lgpio alert callback for button edge events."""
        if level == 1:
            if self.button_press_callback:
                self.button_press_callback()
        elif level == 0:
            if self.button_release_callback:
                self.button_release_callback()

    # ==================== Cleanup ====================
    def cleanup(self):
        if self.backlight_pwm is not None:
            self.backlight_pwm.stop()
        self.spi.close()
        self.red_pwm.stop()
        self.green_pwm.stop()
        self.blue_pwm.stop()

        if self.platform == "rpi":
            if hasattr(self, '_lgpio_cb'):
                self._lgpio_cb.cancel()
            if hasattr(self, '_lgpio_h'):
                lgpio.gpiochip_close(self._lgpio_h)
        elif self.platform == "radxa":
            self._btn_thread_running = False
            if hasattr(self, "_btn_thread") and self._btn_thread:
                self._btn_thread.join(timeout=2)
            for line in self._gpio_lines.values():
                try:
                    line.release()
                except Exception:
                    pass
            for chip in self._gpio_chips.values():
                try:
                    chip.close()
                except Exception:
                    pass
