import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from grid import render  # noqa: E402


class GridTest(unittest.TestCase):
    def test_wraps_cells_and_keeps_borders_aligned(self):
        out = render([["SPACE Name", 8], ["Pane", 6]],
                     [["example-project-data", "wZ:p1"]])
        lines = out.splitlines()
        self.assertEqual(len({len(l) for l in lines}), 1)  # every line same width
        self.assertTrue(lines[0].startswith("┌") and lines[-1].startswith("└"))
        self.assertIn("wZ:p1", out)
        self.assertGreater(len(lines), 5)  # long cell wrapped onto extra lines

    def test_rejects_ragged_rows(self):
        with self.assertRaises(ValueError):
            render([["A", 3], ["B", 3]], [["only one"]])

    def test_empty_and_none_cells(self):
        out = render([["A", 3]], [[None], [""]])
        self.assertEqual(len({len(l) for l in out.splitlines()}), 1)


if __name__ == "__main__":
    unittest.main()
