"""Exercise asset validation and overlay against disposable FR3 fixtures.

Failure modes include admitting non-pack pixels or geometry changes, losing an
HD resize, replacing a cinematic texture, or moving a texture's slot. Fixture
FR3s make these negative cases reproducible without damaging an installation.
"""
import struct
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import zstandard

from texture_packs import Fr3, apply_texture_changes, texture_changes


def sized(value):
    return struct.pack('<Q', len(value)) + value


def texture(name, page, combo, size, rgba):
    pixels = rgba * (size[0] * size[1])
    return (struct.pack('<HHIQ', *size, combo, len(pixels)//4) + pixels
            + sized(name.encode()) + sized(page.encode()) + b'\1')


def write_fr3(path, textures, merc=b'geometry', other=b'unused'):
    raw = (b'+\0' + sized(b'GAME') + struct.pack('<Q', len(textures))
           + b''.join(textures) + other + struct.pack('<Q', 1)
           + sized(b'first-model') + merc + b'+\0')
    path.write_bytes(struct.pack('<Q', len(raw)) + zstandard.ZstdCompressor().compress(raw))


class TexturePackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pack = self.root/'texture_replacements'
        (self.pack/'hud-page').mkdir(parents=True)
        self.original = texture('reticle', 'hud-page', 10, (1, 1), b'\x01\x02\x03\xff')
        self.changed = texture('reticle', 'hud-page', 10, (2, 2), b'\x04\x05\x06\xff')
        self.skin = texture('skin', 'actor-page', 11, (1, 1), b'\x09\x08\x07\xff')
        self.clean = self.root/'clean.fr3'
        self.active = self.root/'active.fr3'
        write_fr3(self.clean, [self.original, self.skin])
        write_fr3(self.active, [self.changed, self.skin], other=b'unused platform data')
        Image.new('RGBA', (2, 2), (4, 5, 6, 255)).save(self.pack/'hud-page/reticle.png')

    def merge(self):
        return texture_changes(Fr3(self.clean), Fr3(self.active), self.pack, 'first-model')

    def test_hd_texture_overlay_keeps_cinematic_order_and_geometry(self):
        generated = self.root/'generated.fr3'
        wanted = self.root/'wanted.fr3'
        write_fr3(generated, [self.skin, self.original], b'cinematic')
        write_fr3(wanted, [self.skin, self.changed], b'cinematic')
        self.assertEqual(apply_texture_changes(generated, self.merge()), 1)
        self.assertEqual(Fr3(generated).raw, Fr3(wanted).raw)

    def test_clean_build_has_no_texture_changes(self):
        self.assertEqual(texture_changes(Fr3(self.clean), Fr3(self.clean), self.pack, 'first-model'), {})
        before = self.clean.read_bytes()
        self.assertEqual(apply_texture_changes(self.clean, {}), 0)
        self.assertEqual(self.clean.read_bytes(), before)

    def test_duplicates_preserve_all_matching_texture_slots(self):
        variant = texture('reticle', 'hud-page', 10, (1, 2), b'\x09\x08\x07\xff')
        write_fr3(self.clean, [self.original, self.skin, self.original, variant])
        write_fr3(self.active, [self.changed, self.skin, self.changed, self.changed])
        generated = self.root/'generated.fr3'
        wanted = self.root/'wanted.fr3'
        write_fr3(generated, [variant, self.original, self.skin, self.original], b'cinematic')
        write_fr3(wanted, [self.changed, self.changed, self.skin, self.changed], b'cinematic')
        self.assertEqual(apply_texture_changes(generated, self.merge()), 3)
        self.assertEqual(Fr3(generated).raw, Fr3(wanted).raw)

    def test_rejects_changed_geometry_and_unlisted_or_stale_texture(self):
        write_fr3(self.active, [self.changed, self.skin], b'altered geometry')
        with self.assertRaisesRegex(ValueError, 'geometry'):
            self.merge()
        write_fr3(self.active, [self.changed, self.skin])
        (self.pack/'hud-page/reticle.png').unlink()
        with self.assertRaisesRegex(ValueError, 'reticle'):
            self.merge()
        Image.new('RGBA', (2, 2)).save(self.pack/'hud-page/reticle.png')
        with self.assertRaisesRegex(ValueError, 'reticle'):
            self.merge()

    def test_rejects_texture_conflict_before_writing(self):
        generated = self.root/'generated.fr3'
        edited = texture('reticle', 'hud-page', 10, (1, 1), b'\0\0\0\0')
        write_fr3(generated, [self.skin, edited], b'cinematic')
        before = generated.read_bytes()
        with self.assertRaisesRegex(ValueError, 'reticle'):
            apply_texture_changes(generated, self.merge())
        self.assertEqual(generated.read_bytes(), before)
