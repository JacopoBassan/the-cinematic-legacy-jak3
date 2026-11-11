"""Retain the existing Jak 3 regional-profile regression.

Failure modes: accept an unknown disc serial, accept the wrong executable
revision, accept a missing regional profile, or mutate the source configuration
when applying a regional override. This isolated check uses disposable metadata
and needs no copyrighted game data. It retains the existing test before any
production change; a native build cannot cheaply exercise all rejected inputs.
"""
import json
from pathlib import Path
import tempfile
import unittest

from build import regional_config


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



if __name__ == "__main__":
    unittest.main()
