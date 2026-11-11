"""Restore the seven cinematic Daxter effects before native facial retargeting.

Only the Daxter model metadata changes. Geometry, indices, textures and other
models remain byte-for-byte unchanged. Cinematic image pixels come from the
user's local extraction and are never embedded in this source file.
"""
import copy
import io
import json
import struct

from PIL import Image

from fr3_models import BASE, DRAW, Reader, compress, decompress, model, serialize
from inspect_faces import buffers
from inspect_transformations import textures


# Tuft, lens, goggles, nose, body, eyes, teeth/helmet strap. The native body
# effect also contains the teeth and strap, so both use native body controls.
SOURCE_EFFECT = [3, 0, 0, 2, 3, 1, 3]
EXPECTED_TRIANGLES = [312, 40, 692, 64, 7050, 336, 1016]


def restore(raw, cinematic_raw, glb, *, name='daxter-lod0',
            donor_name='daxter-highres-lod0', expected_triangles=EXPECTED_TRIANGLES,
            omitted_primitive_indices=()):
    target = model(raw, name)
    donor = model(cinematic_raw, donor_name)
    assert len(target['effects']) == 2, 'Expected the original two importer groups'
    assert len(donor['effects']) == len(expected_triangles)
    for effect in target['effects']:
        reader = Reader(effect['mod'])
        assert not reader.draws() and not reader.draws()
        assert not reader.vector(64), 'Regroup Daxter before creating facial buffers'

    json_length = struct.unpack_from('<I', glb, 12)[0]
    doc = json.loads(glb[20:20 + json_length])
    binary = glb[28 + json_length:]
    primitives = doc['meshes'][0]['primitives']
    texture_list, _ = textures(raw)

    def reflection_texture(material):
        texture_index = material['extensions']['KHR_materials_specular']['specularColorTexture']['index']
        image = doc['images'][doc['textures'][texture_index]['source']]
        view = doc['bufferViews'][image['bufferView']]
        start = view.get('byteOffset', 0)
        decoded = Image.open(io.BytesIO(binary[start:start + view['byteLength']])).convert('RGBA')
        pixels = bytearray(decoded.tobytes())
        pixels[3::4] = bytes(alpha >> 1 for alpha in pixels[3::4])
        matches = [index for index, texture in enumerate(texture_list)
                   if texture['name'] == image['name'] and texture['page'] == 'custom-level'
                   and (texture['width'], texture['height']) == decoded.size
                   and texture['pixels'] == pixels]
        assert matches, ('Missing cinematic reflection texture', image['name'])
        return matches[0]

    effects = []
    cursors = [0, 0]
    primitive_index = 0
    donor_primitive_index = 0
    for effect_index, old in enumerate(donor['effects']):
        bucket_index = int(bool(old['tail'][8]))
        bucket = target['effects'][bucket_index]
        new = copy.deepcopy(bucket)
        new['draws'] = []
        reflections = []
        for old_draw in old['draws']:
            source_primitive_index = donor_primitive_index
            donor_primitive_index += 1
            if source_primitive_index in omitted_primitive_indices:
                continue
            primitive = primitives[primitive_index]
            assert primitive['material'] == source_primitive_index
            triangle_count = doc['accessors'][primitive['indices']]['count'] // 3
            assert triangle_count == DRAW.unpack(old_draw)[5]
            draw = bucket['draws'][cursors[bucket_index]]
            cursors[bucket_index] += 1
            assert DRAW.unpack(draw)[5] == triangle_count and DRAW.unpack(draw)[6] == 1
            new['draws'].append(draw)
            if bucket_index:
                reflections.append(reflection_texture(doc['materials'][primitive['material']]))
            primitive_index += 1
        assert sum(DRAW.unpack(draw)[5] for draw in new['draws']) == expected_triangles[effect_index]
        if reflections:
            assert len(set(reflections)) == 1
            reflection = reflections[0]
        else:
            reflection = 0
        # Preserve each authored reflection blend mode, not the importer's
        # merged reflection mode. Facial retargeting sets has_mod_draw later.
        new['tail'] = old['tail'][:4] + struct.pack('<I', reflection) + old['tail'][8:9] + b'\0'
        effects.append(new)

    assert primitive_index == len(primitives)
    assert cursors == [len(effect['draws']) for effect in target['effects']]
    target['effects'] = effects
    fixed = raw[:target['start']] + serialize(target) + raw[target['end']:]
    checked = model(fixed, target['name'])
    assert serialize(checked) == serialize(target)
    # This stage only replaces the model record; buffer and texture data stay
    # identical, including all native and imported neutral vertices.
    _, old_indices, _, old_vertices = buffers(raw, target['name'])
    _, new_indices, _, new_vertices = buffers(fixed, target['name'])
    assert old_indices.tobytes() == new_indices.tobytes() and old_vertices == new_vertices
    assert raw[:target['start']] == fixed[:target['start']]
    assert raw[target['end']:] == fixed[checked['end']:]
    return fixed


def main():
    path = BASE / 'prototype/GAME-armor.fr3'
    raw = decompress(path)
    cinematic = decompress(BASE / 'extraction/out/jak3/fr3/ARENACST.fr3')
    glb = (BASE / 'prototype/merc_replacements/daxter-lod0.glb').read_bytes()
    fixed = restore(raw, cinematic, glb)
    compress(path, fixed)
    assert decompress(path) == fixed
    (BASE / 'daxter-effect-map.json').write_text(json.dumps({
        'cinematic_to_gameplay_effect': SOURCE_EFFECT,
        'triangles_per_effect': EXPECTED_TRIANGLES,
        'authored_envmap_modes_preserved': True,
        'geometry_and_buffers_unchanged': True,
    }, indent=2))
    print('Restored seven Daxter effects; preserved all 9,510 triangles and neutral vertices.')


if __name__ == '__main__':
    main()
