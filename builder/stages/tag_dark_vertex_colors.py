"""Private prototype: tag cinematic hair/eyebrows for native Dark color blending.

Only unused MercVertex.pad3 bytes and Jak's max_bones header change. The shader
and GOAL packet metadata must be paired with this file. Never installs anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile

import numpy as np

from fr3_models import DRAW, Reader, compress, decompress, model, serialize
from inspect_faces import buffers

MODEL = 'jakb-c-lod0'
FIRST_MODEL = 'collectables-bomb-blast-lod0'
SLOTS = frozenset((-13, -9))
MARKER = 0xD3


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _all_models(raw):
    first = model(raw, FIRST_MODEL)['start']
    count = struct.unpack_from('<Q', raw, first - 8)[0]
    _require(0 < count < 1000, 'Unexpected Merc model count')
    cursor = first
    result = []
    for _ in range(count):
        size = struct.unpack_from('<Q', raw, cursor)[0]
        _require(0 < size < 256, 'Unexpected Merc model name length')
        name = raw[cursor + 8:cursor + 8 + size].decode('ascii')
        item = model(raw, name)
        _require(item['start'] == cursor, 'Merc model sequence mismatch')
        result.append(item)
        cursor = item['end']
    return result, cursor


def _draw_ids(draws, indices, vertex_count, selected, slots=SLOTS):
    result = set()
    for draw in draws:
        values = DRAW.unpack(draw)
        if (values[1] in slots) != selected:
            continue
        start, length = values[3:5]
        _require(start + length <= len(indices), 'Draw index range out of bounds')
        for value in indices[start:start + length]:
            index = int(value)
            if index == 0xffffffff:
                continue
            _require(0 <= index < vertex_count, 'Draw vertex index out of bounds')
            result.add(index)
    return result


def native_hair_palette(native):
    """Derive chromaticity from owned gameplay hair, including its warm tips."""
    from scipy.spatial import cKDTree
    raw=decompress(native);m=model(raw,MODEL)
    _,indices,_,vb=buffers(raw,MODEL)
    vertices=np.frombuffer(vb,'u1').reshape(-1,64)
    ids=sorted(_draw_ids([d for e in m['effects'] for d in e['draws']],indices,len(vertices),True,{-6}))
    _require(bool(ids),'Native gameplay hair missing')
    rgb=vertices[ids,56:59].astype(float)
    ratios=rgb/np.maximum(rgb.max(1,keepdims=True),1)
    colors=[(1.0,1.0,1.0)]
    for row in ratios:
        key=tuple(row)
        if key not in colors:colors.append(key)
    _require(len(colors)<=256-MARKER,'Native hair palette exceeds reserved padding codes')
    codes=np.array([MARKER+colors.index(tuple(row)) for row in ratios],dtype='u1')
    tree=cKDTree(vertices.view('<f4').reshape(-1,16)[ids,:3])
    return tree,codes,colors


def patch_fr3(source, output, meta_index=68, native=None, replace_existing=False):
    """Write a private staged file and return a path-free validation report."""
    source, output = Path(source).resolve(), Path(output).resolve()
    _require(source != output, 'Source must never be overwritten')
    _require(0 < meta_index < 128, 'Metadata index is outside the shader matrix table')
    original_packed = source.read_bytes()
    source_sha = hashlib.sha256(original_packed).hexdigest()
    raw = decompress(source)
    models, model_end = _all_models(raw)
    target = next(item for item in models if item['name'] == MODEL)
    index_start, indices, vertex_start, vertex_bytes = buffers(raw, MODEL)
    _require(index_start == model_end, 'Global Merc buffers do not follow the model section')
    vertices = np.frombuffer(vertex_bytes, dtype='u1').reshape(-1, 64)
    allowed_source=(0, *range(MARKER,256)) if replace_existing else (0,)
    _require(np.isin(vertices[:,63],allowed_source).all(), 'Global pad3 is already in use')
    palette=[(1.0,1.0,1.0)]
    if native is not None:
        tree,codes,palette=native_hair_palette(native)
        # Reserve the first code for eyebrows/goatee, whose texture does not
        # receive the gameplay hair brightness adaptation.
        _require(len(palette)<256-MARKER,'Hair palette needs a separate facial-hair code')
        palette=[(1.0,1.0,1.0)]+palette
        codes=codes+np.uint8(1)
    def marker_for(verts, index, hair):
        if native is None or index not in hair:return MARKER
        position=np.frombuffer(verts[index].tobytes(),'<f4')[:3]
        distance,nearest=tree.query(position)
        _require(distance<4096.0,'Cinematic hair outside native head region')
        return int(codes[nearest])

    selected = set()
    unselected = set()
    allowed_offsets = set()
    mod_reports = []
    max_joint = 0
    all_mod_vertices = 0
    for item in models:
        effect_cursor = item['start'] + 8 + len(item['name'].encode()) + 8
        for effect_index, effect in enumerate(item['effects']):
            mod_start = effect_cursor + 8 + len(effect['draws']) * DRAW.size
            effect_cursor = mod_start + len(effect['mod']) + 10
            reader = Reader(effect['mod'])
            mod_draws, fixed_draws = reader.draws(), reader.draws()
            mod_vertex_offset = reader.pos + 8
            mod_bytes = reader.vector(64)
            mod_vertices = np.frombuffer(mod_bytes, dtype='u1').reshape(-1, 64)
            all_mod_vertices += len(mod_vertices)
            _require(np.isin(mod_vertices[:,63],allowed_source).all(), 'An effect pad3 is already in use')
            global_draws = effect['draws'] + fixed_draws
            is_target = item['name'] == MODEL
            if is_target:
                selected.update(_draw_ids(global_draws, indices, len(vertices), True))
                unselected.update(_draw_ids(global_draws, indices, len(vertices), False))
            else:
                for flag in (False, True):
                    unselected.update(_draw_ids(global_draws, indices, len(vertices), flag))
            if not is_target:
                _require((mod_vertices[:,63]==0).all(), 'Another model uses the reserved palette padding')
                continue
            if len(mod_vertices):
                max_joint = max(max_joint, int(mod_vertices[:, 60:63].max()))
            mod_selected = _draw_ids(mod_draws, indices, len(mod_vertices), True)
            mod_hair = _draw_ids(mod_draws, indices, len(mod_vertices), True, {-13})
            mod_unselected = _draw_ids(mod_draws, indices, len(mod_vertices), False)
            _require(not mod_selected & mod_unselected, 'Hair/eyebrow mod vertices share another material')
            _require((mod_vertices[sorted(mod_unselected),63]==0).all(), 'Unselected mod vertices carry reserved markers')
            if not mod_selected:
                continue
            patched_mod = bytearray(effect['mod'])
            for index in mod_selected:
                offset = mod_vertex_offset + index * 64 + 63
                patched_mod[offset] = marker_for(mod_vertices,index,mod_hair)
                allowed_offsets.add(mod_start + offset)
            effect['mod'] = bytes(patched_mod)
            mod_reports.append({'effect': effect_index, 'taggedVertices': len(mod_selected)})
        _require(effect_cursor + 20 == item['end'], 'Effect byte offsets do not match model bounds')
    _require(selected, 'No cinematic hair or eyebrow draws found')
    _require(not selected & unselected, 'Hair/eyebrow global vertices share another material or model')
    _require((vertices[sorted(unselected),63]==0).all(), 'Unselected vertices carry reserved markers')
    target_references = set()
    for effect in target['effects']:
        for flag in (False, True):
            target_references.update(_draw_ids(effect['draws'], indices, len(vertices), flag))
    max_joint = max(max_joint, int(vertices[sorted(target_references), 60:63].max()))
    _require(meta_index > max_joint, 'Metadata overlaps an actual skeletal matrix')
    old_max_bones = struct.unpack_from('<I', target['tail'], 4)[0]
    _require(meta_index > old_max_bones or (replace_existing and meta_index==old_max_bones), 'Metadata must follow the current shader matrix range')
    target['tail'] = target['tail'][:4] + struct.pack('<I', meta_index) + target['tail'][8:]
    allowed_offsets.update(range(target['end'] - 16, target['end'] - 12))

    staged = bytearray(raw)
    hair=set()
    for effect in target['effects']:
        r=Reader(effect['mod']);r.draws();fixed=r.draws()
        hair.update(_draw_ids(effect['draws']+fixed,indices,len(vertices),True,{-13}))
    for index in selected:
        offset = vertex_start + 8 + index * 64 + 63
        staged[offset] = marker_for(vertices,index,hair)
        allowed_offsets.add(offset)
    replacement = serialize(target)
    _require(len(replacement) == target['end'] - target['start'], 'Prototype unexpectedly resized model data')
    staged[target['start']:target['end']] = replacement
    changed = set(np.flatnonzero(np.frombuffer(raw, dtype='u1') != np.frombuffer(staged, dtype='u1')).tolist())
    _require(changed <= allowed_offsets, 'A non-padding or non-header byte changed')
    tagged_count = len(selected) + sum(entry['taggedVertices'] for entry in mod_reports)
    if not replace_existing:_require(len(changed) >= tagged_count, 'Not every selected vertex was tagged')
    _require(source.read_bytes() == original_packed, 'Source changed during the prototype build')

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + '.', suffix='.tmp', dir=output.parent)
    os.close(descriptor)
    try:
        compress(temporary, bytes(staged))
        _require(decompress(temporary) == staged, 'Compressed output failed round-trip validation')
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        'status': 'private-prototype-only',
        'sourceSha256': source_sha,
        'outputSha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'model': MODEL,
        'animatedTextureSlots': sorted(SLOTS),
        'marker': MARKER,
        'palette': palette,
        'paletteSource': 'owned gameplay hair chromaticity' if native else 'gray',
        'facialHairMarker': MARKER,
        'scalpHairMarkerStart': MARKER+1 if native else MARKER,
        'globalTaggedVertices': len(selected),
        'modTaggedVertices': mod_reports,
        'totalTaggedVertices': tagged_count,
        'sourceMaxBones': old_max_bones,
        'maximumActualJointIndex': max_joint,
        'metadataMatrixIndex': meta_index,
        'outputMaxBones': meta_index,
        'changedBytes': len(changed),
        'sharedUnselectedVertices': 0,
        'modelsAudited': len(models),
        'globalVerticesWithZeroSourcePadding': len(vertices),
        'modVerticesWithZeroSourcePadding': all_mod_vertices,
        'allUnselectedPaddingPreserved': True,
        'allOtherRawBytesPreserved': True,
        'sourceUnchanged': True,
        'runtimeValidation': 'pending',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--meta-index', type=int, default=68)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = patch_fr3(args.source, args.output, args.meta_index)
    rendered = json.dumps(report, indent=2) + '\n'
    if args.report:
        destination = args.report.resolve()
        destination.write_text(rendered, encoding='utf-8')
    print(rendered, end='')


if __name__ == '__main__':
    main()
