"""Tests fuer den ICO-Generator (ohne Pillow)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import icon  # noqa: E402


class TestIcoStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = icon.build()

    def test_header(self):
        reserved, kind, count = struct.unpack("<HHH", self.data[:6])
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1)                      # 1 = Icon
        self.assertEqual(count, len(icon.SIZES))

    def test_every_entry_points_inside_the_file(self):
        count = struct.unpack("<H", self.data[4:6])[0]
        for i in range(count):
            entry = self.data[6 + 16 * i: 22 + 16 * i]
            w, h, colors, res, planes, bpp, length, offset = struct.unpack("<BBBBHHII", entry)
            self.assertEqual(bpp, 32)
            self.assertLessEqual(offset + length, len(self.data))
            # 256 wird in der Verzeichnisstruktur als 0 kodiert.
            expected = icon.SIZES[i]
            self.assertEqual(w, expected if expected < 256 else 0)
            self.assertEqual(h, expected if expected < 256 else 0)

    def test_pixel_payload_has_the_right_length(self):
        for i, size in enumerate(icon.SIZES):
            entry = self.data[6 + 16 * i: 22 + 16 * i]
            length = struct.unpack("<BBBBHHII", entry)[6]
            mask = ((size + 31) // 32) * 4 * size
            self.assertEqual(length, 40 + size * size * 4 + mask, f"{size}px")


class TestDrawing(unittest.TestCase):
    def test_corners_are_transparent(self):
        for x, y in [(0.01, 0.01), (0.99, 0.01), (0.01, 0.99), (0.99, 0.99)]:
            self.assertFalse(icon._rounded_square(x, y), f"({x}, {y})")

    def test_centre_is_filled(self):
        self.assertTrue(icon._rounded_square(0.5, 0.5))
        self.assertEqual(icon._sample(0.5, 0.5, True)[3], 255)

    def test_background_uses_the_accent_colour(self):
        b, g, r, a = icon._sample(0.5, 0.5, True)
        self.assertEqual((r, g, b), icon.BG)

    def test_main_dot_is_brighter_than_the_background(self):
        cx, cy = icon.DOTS[0][0], icon.DOTS[0][1]
        dot = icon._sample(cx, cy, True)
        plain = icon._sample(0.5, 0.5, True)
        self.assertGreater(sum(dot[:3]), sum(plain[:3]))

    def test_small_sizes_drop_the_faint_dots(self):
        faint = [d for d in icon.DOTS if d[3] < 0.7][0]
        detailed = icon._sample(faint[0], faint[1], True)
        plain = icon._sample(faint[0], faint[1], False)
        self.assertGreater(sum(detailed[:3]), sum(plain[:3]))


class TestEnsure(unittest.TestCase):
    def test_creates_the_file_once(self):
        path = Path(tempfile.mkdtemp()) / "todo.ico"
        icon.ensure(path)
        self.assertTrue(path.is_file())
        stamp = path.stat().st_mtime_ns
        icon.ensure(path)                       # zweiter Aufruf: unveraendert
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_force_rewrites(self):
        path = Path(tempfile.mkdtemp()) / "todo.ico"
        path.write_bytes(b"kaputt")
        icon.ensure(path, force=True)
        self.assertEqual(struct.unpack("<HHH", path.read_bytes()[:6])[1], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
