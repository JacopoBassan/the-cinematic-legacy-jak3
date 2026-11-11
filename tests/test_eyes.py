"""Packed-control regression tests without copyrighted game resources."""
import struct
import unittest
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('jak3_cinematic_eyes_under_test', Path(__file__).resolve().parents[1] / 'builder/cinematic_eyes.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
with_automatic_blink = _module.with_automatic_blink


class AutomaticBlinkBaselineTests(unittest.TestCase):
    @staticmethod
    def frame(lid):
        # Unequal signed gaze, nondefault scales and unused bytes make accidental
        # whole-word replacement visible. Only the third byte is a lid control.
        return int.from_bytes(struct.pack('<8b', -33, 18, lid, 37, 38, 19, 64, 53), 'little')

    @staticmethod
    def lid(frame):
        return struct.unpack('<b', bytes([(frame >> 16) & 255]))[0] / 128

    def test_every_encoded_lid_preserves_rest_and_other_control_bytes(self):
        mask = (1 << 64) - 1 ^ (255 << 16)
        for lid in range(-128, 128):
            original = self.frame(lid)
            fixed = with_automatic_blink(original)
            resting = 1 + lid / 128 if lid < 0 else lid / 128
            self.assertEqual(fixed & mask, original & mask)
            self.assertLess(self.lid(fixed), 0)
            self.assertEqual(1 + self.lid(fixed), resting)
            self.assertEqual(1 + self.lid(fixed) - self.lid(fixed), 1)
            self.assertEqual(with_automatic_blink(fixed), fixed)
            if lid < 0:
                self.assertEqual(fixed, original)
            for blink in (0, .125, .5, .875, 1):
                closure = 1 + self.lid(fixed) - self.lid(fixed) * blink
                self.assertAlmostEqual(closure, resting + (1 - resting) * blink)

    def test_invalid_packed_controls_are_rejected(self):
        for frame in (-1, 1 << 64, 1.5, True, None):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                with_automatic_blink(frame)


if __name__ == '__main__':
    unittest.main()
