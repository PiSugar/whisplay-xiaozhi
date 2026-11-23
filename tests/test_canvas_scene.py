import unittest

from canvas.scene import ALLOWED_TYPES, CanvasScene, sanitize_command


class CanvasSceneTests(unittest.TestCase):
    def test_requires_a_selected_position(self):
        scene = CanvasScene()
        with self.assertRaisesRegex(ValueError, "tap"):
            scene.draw({"commands": [{"type": "circle", "x": 0, "y": 0, "r": 20}]})

    def test_accepts_generic_model_authored_primitives(self):
        scene = CanvasScene()
        scene.configure(400, 500)
        scene.select(120, 210)
        result = scene.draw({
            "label": "model drawing",
            "commands": [
                {"type": "polyline", "points": [[-30, 20], [0, -40], [30, 20]], "stroke": "#3366cc"},
                {"type": "ellipse", "x": 0, "y": 0, "rx": 18, "ry": 32, "fill": "#ffee88"},
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["anchor"], [120, 210])
        self.assertEqual(result["primitive_count"], 2)
        snapshot = scene.snapshot()
        self.assertEqual(len(snapshot["drawings"]), 1)

    def test_all_declared_primitive_types_validate(self):
        commands = {
            "line": {"points": [[0, 0], [1, 1]]},
            "polyline": {"points": [[0, 0], [1, 1]]},
            "polygon": {"points": [[0, 0], [1, 1], [2, 0]]},
            "bezier": {"points": [[0, 0], [1, 2], [3, 0]]},
            "spline": {"points": [[0, 0], [1, 2], [3, 0]], "closed": True},
            "circle": {"x": 0, "y": 0, "r": 4},
            "ellipse": {"x": 0, "y": 0, "rx": 4, "ry": 5},
            "rectangle": {"x": 0, "y": 0, "w": 4, "h": 5},
            "arc": {"x": 0, "y": 0, "rx": 4, "ry": 5, "start": 0, "end": 180},
            "text": {"x": 0, "y": 0, "text": "hi"},
        }
        self.assertEqual(set(commands), ALLOWED_TYPES)
        for kind, values in commands.items():
            with self.subTest(kind=kind):
                self.assertEqual(sanitize_command({"type": kind, **values})["type"], kind)

    def test_rich_style_fields_are_sanitized(self):
        command = sanitize_command({
            "type": "spline",
            "points": [[-10, 0], [0, -10], [10, 0]],
            "closed": True,
            "fill": "#AABBCC",
            "opacity": 0.45,
        })
        self.assertTrue(command["closed"])
        self.assertEqual(command["fill"], "#aabbcc")
        self.assertEqual(command["opacity"], 0.45)

    def test_clear_and_undo(self):
        scene = CanvasScene()
        scene.select(10, 10)
        command = {"commands": [{"type": "circle", "r": 4}]}
        scene.draw(command)
        scene.draw(command)
        self.assertEqual(scene.undo()["remaining"], 1)
        self.assertEqual(scene.clear()["removed"], 1)

    def test_invalid_primitive_does_not_discard_the_drawing(self):
        scene = CanvasScene()
        scene.select(10, 10)
        result = scene.draw({
            "commands": [
                {"type": "circle", "r": 12},
                {"type": "line"},
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["primitive_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(len(scene.snapshot()["drawings"]), 1)

    def test_all_invalid_primitives_return_without_raising(self):
        scene = CanvasScene()
        scene.select(10, 10)
        result = scene.draw({"commands": [{"type": "line"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(scene.snapshot()["drawings"], [])

    def test_accepts_common_model_line_variants(self):
        variants = [
            {"type": "line", "start": [-4, 2], "end": [5, 7]},
            {"type": "line", "x1": -4, "y1": 2, "x2": 5, "y2": 7},
            {"type": "line", "points": [{"x": -4, "y": 2}, {"x": 5, "y": 7}]},
        ]
        for command in variants:
            with self.subTest(command=command):
                self.assertEqual(len(sanitize_command(command)["points"]), 2)


if __name__ == "__main__":
    unittest.main()
