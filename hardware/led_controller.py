"""
LED controller — maps application states to RGB LED colors.
"""

from hardware.whisplay_board import WhisplayBoard


# State → (R, G, B)
STATE_COLORS = {
    "idle":        (0,   0,   0),
    "activating":  (255, 165, 0),
    "connecting":  (0,   0,   255),
    "listening":   (0,   255, 0),
    "speaking":    (255, 102, 0),
    "thinking":    (128, 0,   255),
    "error":       (255, 0,   0),
    "wake_word":   (0,   200, 255),
}


class LedController:
    def __init__(self, board: WhisplayBoard):
        self._board = board

    def set_state(self, state: str):
        r, g, b = STATE_COLORS.get(state, (0, 0, 0))
        self._board.set_rgb(r, g, b)

    def set_rgb(self, r: int, g: int, b: int):
        self._board.set_rgb(r, g, b)

    def off(self):
        self._board.set_rgb(0, 0, 0)
