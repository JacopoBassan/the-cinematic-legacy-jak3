"""Canonicalize undefined fields in format 43 Merc fingerprints.

OpenGOAL v0.3.9 leaves the environment-map texture ID uninitialized when
has_envmap is false. Merc2 reads it only inside should_envmap branches.
The extractor also emits undefined eye IDs for Kleiver's controller-less rider.
The mod derives that rider's eye IDs from the cinematic donor.
"""
from __future__ import annotations

import struct


def canonical_merc(section, *, normalize_envmaps=True, normalize_kleiver_eyes=False):
    result = bytearray(section)
    cursor = 0

    def skip(size):
        nonlocal cursor
        if size < 0 or cursor+size > len(result):
            raise ValueError('Truncated native Merc fingerprint input')
        cursor += size

    def count():
        skip(8)
        return struct.unpack_from('<Q', result, cursor-8)[0]

    def vector(size):
        skip(count()*size)

    models = count()
    if not 0 < models <= len(result)//28:
        raise ValueError('Unsupported native Merc model count')
    for _ in range(models):
        name_length = count()
        name_start = cursor
        skip(name_length)
        is_kleiver = normalize_kleiver_eyes and result[name_start:cursor] == b'kleever-rider-lod0'
        effects = count()
        if not 0 < effects < 100:
            raise ValueError('Unsupported native Merc effect count')
        for effect in range(effects):
            if is_kleiver and effect == 1:
                if count() != 2:
                    raise ValueError('Unsupported Kleiver rider eye draw layout')
                for side in range(2):
                    draw = cursor
                    skip(22)
                    result[draw+8] = side  # Eye ID follows the draw mode and texture ID.
            else:
                vector(22)  # all_draws.
            vector(22)  # mod_draw.
            vector(22)  # fix_draw.
            vector(64)  # Modifiable vertices.
            vector(2)   # Vertex addresses.
            vector(1)   # Fragment mask.
            skip(4)     # Expected vertex end.
            vector(32)  # Blerc float data.
            vector(4)   # Blerc integer data.
            skip(4)     # Draw mode remains checked.
            texture = cursor
            skip(6)     # Texture ID, has_envmap, has_mod_draw.
            if result[texture+4] not in (0, 1) or result[texture+5] not in (0, 1):
                raise ValueError('Unsupported native Merc effect flags')
            if normalize_envmaps and result[texture+4] == 0:
                result[texture:texture+4] = b'\0'*4
        skip(20)  # Model limits and scale remain checked.
    return result
