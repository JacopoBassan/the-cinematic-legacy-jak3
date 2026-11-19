"""Retain the existing Jak 3 regional-profile regression.

Failure modes: accept an unknown disc serial, accept the wrong executable
revision, accept a missing regional profile, or mutate the source configuration
when applying a regional override. This isolated check uses disposable metadata
and needs no copyrighted game data. It retains the existing test before any
production change; a native build cannot cheaply exercise all rejected inputs.
"""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import zstandard

from build import fr3_asset_hash, regional_config


class Jak3RegionalProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cinematic-jak3-profile-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'

    def test_regional_profile_requires_serial_and_revision(self):
        metadata=self.data/'iso_data/jak3/buildinfo.json'
        metadata.parent.mkdir(parents=True)
        config=dict(supportedSerials=['SCES-52460','SCUS-97330'],
                    inputs={'common':'same','regional':'pal'},fr3Inputs={},
                    regionalProfiles={'SCUS-97330':dict(elfHash=123,inputs={'regional':'us'})})
        metadata.write_text(json.dumps([dict(serial='SCUS-97330',elf_hash=123)]))
        selected=regional_config(self.data,config)
        self.assertEqual(selected['inputs'],{'common':'same','regional':'us'})
        self.assertEqual(config['inputs']['regional'],'pal')
        metadata.write_text(json.dumps([dict(serial='SCUS-97330',elf_hash=456)]))
        with self.assertRaisesRegex(ValueError,'revision'):regional_config(self.data,config)
        metadata.write_text(json.dumps([dict(serial='unknown',elf_hash=123)]))
        with self.assertRaisesRegex(ValueError,'Unsupported'):regional_config(self.data,config)
        metadata.write_text(json.dumps([dict(serial='SCUS-97330',elf_hash=123)]))
        del config['regionalProfiles']
        with self.assertRaisesRegex(ValueError,'Missing verified'):regional_config(self.data,config)

    def test_destrack_fingerprint_ignores_only_kleiver_rider_eye_slots(self):
        # Both eye IDs came from an absent controller. Every other draw byte
        # and the eye IDs of other actors must still affect the fingerprint.
        def model(name, eye_ids, texture):
            encode = lambda count: struct.pack('<Q', count)
            body = encode(len(name)) + name + encode(2)
            for ids in [(255,), eye_ids]:
                body += encode(len(ids))
                for side, eye_id in enumerate(ids):
                    body += struct.pack('<IiBIIIB', 0, texture + side, eye_id, side, 3, 1, 0)
                body += b'\0' * (5 * 8 + 4 + 2 * 8 + 10)
            return body + b'\0' * 20

        def fingerprint(name, kleiver_eyes, other_eyes=(0, 1), texture=2, normalize_envmaps=False):
            first = b'daxter-highres-lod0'
            merc = struct.pack('<Q', 2) + model(first, other_eyes, 0)
            merc += model(b'kleever-rider-lod0', kleiver_eyes, texture)
            raw = b'+\0' + b'\0' * 16 + merc + b'\0' * 16 + b'+\0'
            path = self.data / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(struct.pack('<Q', len(raw)) + zstandard.ZstdCompressor().compress(raw))
            return fr3_asset_hash(path, first.decode(), normalize_envmaps=normalize_envmaps)

        for normalize_envmaps in (False, True):
            options = {'normalize_envmaps': normalize_envmaps}
            base = fingerprint('destrack.fr3', (0, 1), **options)
            self.assertEqual(base, fingerprint('destrack.fr3', (202, 203), **options))
            self.assertEqual(base, fingerprint('destrack.fr3', (254, 255), **options))
            self.assertNotEqual(base, fingerprint('destrack.fr3', (0, 1), (202, 203), **options))
            self.assertNotEqual(base, fingerprint('destrack.fr3', (0, 1), texture=6, **options))
            self.assertNotEqual(fingerprint('other.fr3', (0, 1), **options),
                                fingerprint('other.fr3', (202, 203), **options))

if __name__ == "__main__":
    unittest.main()
