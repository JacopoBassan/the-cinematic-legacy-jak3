"""Canonicalize unused environment-map texture IDs in format 43 Merc data.

OpenGOAL v0.3.9 serializes this uninitialized field when has_envmap is false.
Merc2 reads it only inside should_envmap branches. Game files stay unchanged;
this representation is used only for compatibility fingerprints.
"""
from __future__ import annotations

import struct


def canonical_merc(section):
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
        vector(1)
        effects = count()
        if not 0 < effects < 100:
            raise ValueError('Unsupported native Merc effect count')
        for _ in range(effects):
            for _ in range(3):
                vector(22)  # all_draws, mod_draw, fix_draw.
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
            if result[texture+4] == 0:
                result[texture:texture+4] = b'\0'*4
        skip(20)  # Model limits and scale remain checked.
    return result
