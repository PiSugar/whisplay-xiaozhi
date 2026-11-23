import unittest
from unittest.mock import Mock

from display.plus_canvas import PlusCanvas


class PlusCanvasTests(unittest.TestCase):
    def setUp(self):
        self.canvas = object.__new__(PlusCanvas)
        self.canvas.widget = Mock()
        self.canvas.widget.get_reveal_child.return_value = True
        self.canvas._picture = Mock()
        self.canvas._picture.get_width.return_value = 616
        self.canvas._picture.get_height.return_value = 354
        self.canvas._viewport_size = (640, 480)

    def test_opening_animation_has_no_drawable_size(self):
        self.canvas.widget.get_child_revealed.return_value = False
        self.assertIsNone(self.canvas._paper_size())

    def test_oversized_revealer_allocation_is_rejected(self):
        self.canvas.widget.get_child_revealed.return_value = True
        self.canvas._picture.get_width.return_value = 6437
        self.assertIsNone(self.canvas._paper_size())
        self.canvas._picture.get_width.return_value = 616
        self.assertEqual(self.canvas._paper_size(), (616, 354))
