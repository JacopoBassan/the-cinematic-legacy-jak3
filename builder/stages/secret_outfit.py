"""Fit the Arena's Jak II costume to the secret outfit's native gameplay controls."""
import struct

import numpy as np
from scipy.optimize import lsq_linear

from build_npc_expansion import texture_bytes, vec
from fix_faces import closest_surface, draw_list, fix_model
from fr3_models import DRAW, Reader, compress, decompress, model, serialize
from inspect_faces import buffers, glb
from inspect_transformations import textures

PLAYER = 'jakb-normal-lod0'
DONOR = 'jak-highres-lod0'
DARK_PLAYER = 'jakb-normal-dark-lod0'
EYES = {0: 0, 1: 1}
NATIVE_EYES = {0: 2, 1: 1}
FACE_PAIRS = {8: (9,), 9: (12,), 10: (8,), 28: (3,)}
EQUIPMENT_PAIRS = {2: (5,), 3: (4,), 4: (6,), 6: (7,),
                   12: (10,), 17: (11,), 24: (9,), 25: (7,), 16: (22,)}


def transfer(source, target, read, read_target, used_parts, channels):
    triangles, deltas, normals = [], [], []
    for index in used_parts:
        primitive = source[index]
        faces = read(primitive['indices']).reshape(-1, 3)
        triangles.append(read(primitive['attributes']['POSITION'])[faces])
        deltas.append(np.array([read(t['POSITION'])[faces] for t in primitive['targets']]))
        normals.append(np.array([read(t['NORMAL'])[faces] for t in primitive['targets']]))
    points = read_target(target['attributes']['POSITION'])
    match, bary, distance = closest_surface(points, np.concatenate(triangles))
    delta, normal = (np.einsum('tnij,ni->tnj', np.concatenate(v, axis=1)[:, match], bary)
                     for v in (deltas, normals))
    excluded = np.setdiff1d(np.arange(len(delta)), channels)
    delta[excluded] = 0
    normal[excluded] = 0
    return delta, normal, distance


def fit_outfit(stock_path, replacement_path):
    stock, read = glb(stock_path)
    donor, read_donor = glb(replacement_path)
    low = stock['meshes'][0]['primitives']
    high = donor['meshes'][0]['primitives']
    assert {len(p['targets']) for p in low} == {13}
    assert {len(p['targets']) for p in high} == {32}
    channels = (0, 1, 2, 8, 9, 10, 11, 12)
    sampled, authored, coefficients = {}, {}, {}
    basis_rows, stock_rows = [], []
    for part, source in FACE_PAIRS.items():
        primitive = high[part]
        local = np.unique(read_donor(primitive['indices']))
        positions = np.array([read_donor(t['POSITION']) for t in primitive['targets']])
        normals = np.array([read_donor(t['NORMAL']) for t in primitive['targets']])
        authored[part] = positions, normals
        delta, _, distance = transfer(low, primitive, read, read_donor, source, channels)
        active = local[(np.linalg.norm(positions[:, local], axis=2).max(0) > 1e-5)
                       & (distance[local] < .070)]
        assert len(active) > 0
        # Equalize the skin, brow, lower jaw and tooth constraints.
        scale = 1 / np.sqrt(len(active))
        basis_rows.append(scale * positions[:, active].reshape(32, -1).T)
        stock_rows.append(scale * delta[:, active].reshape(13, -1).T)
    basis = np.concatenate(basis_rows)
    wanted = np.concatenate(stock_rows)
    penalty = np.eye(32) * .0015
    matrix = np.vstack([basis, penalty])
    for ch in channels:
        coefficients[ch] = lsq_linear(matrix, np.r_[wanted[:, ch], np.zeros(32)],
                                      bounds=(0, 1.5), tol=1e-9).x
    for part, shape in authored.items():
        entries = []
        for value in shape:
            field = np.zeros((13, *value.shape[1:]), dtype=np.float32)
            for ch in channels:
                field[ch] = np.einsum('s,sij->ij', coefficients[ch], value)
            entries.append(field)
        sampled[part] = tuple(entries)
    for part, source in EQUIPMENT_PAIRS.items():
        delta, normal, _ = transfer(low, high[part], read, read_donor, source, range(3, 8))
        sampled[part] = delta, normal
    fit_held_frown(high, read_donor, sampled, authored)
    active = sorted({int(ch) for delta, _ in sampled.values()
                     for ch in np.flatnonzero(np.max(abs(delta), axis=(1, 2)) > 1e-7)})
    assert active == list(range(13)), active
    return donor, read_donor, sampled, 13, [{
        'nativeWeightChannels': active,
        'authoredFaceCoefficients': {str(ch): {str(i): round(float(value), 5)
                                              for i, value in enumerate(fit) if value > 1e-4}
                                     for ch, fit in coefficients.items()}
    }]


def fit_held_frown(primitives, read, field, authored):
    """Use the Arena frown's eyelids and a continuous closed-lip surface."""
    channel = 8
    for part in (8, 10):
        position = read(primitives[part]['attributes']['POSITION'])
        x, y, z = position.T
        eye_region = (np.clip((y-2.437)/.041,0,1)*np.clip((z-.165)/.076,0,1)
                      *np.clip((.213-abs(x))/.039,0,1))
        for motion, targets in zip(field[part],authored[part]):
            motion[channel] += eye_region[:,None]*(.85*targets[3]-motion[channel])
    # Jak II's outfit uses the same donor lips. For the held frown, both
    # touching lip edges must follow one displacement rather than a mix of
    # open-mouth shapes. Keep this field below the facial socket.
    for part in (8, 9, 28):
        primitive = primitives[part]
        x,y,z = read(primitive['attributes']['POSITION']).T
        corner = np.clip((abs(x)-.021)/.034,0,1)
        taper = corner**2*(3-2*corner)
        lift = .024 + .0045*np.exp(-((abs(x)-.032)/.014)**2) - .0075*taper
        mouth = (np.clip((.100-abs(x))/.032,0,1)*np.clip((y-2.275)/.057,0,1)
                 *np.clip((2.406-y)/.046,0,1)*np.clip((z-.200)/.070,0,1))
        if part == 9:
            mouth = np.ones_like(y)
        closed = np.column_stack((.20*x,lift,.002*(1-taper)))
        field[part][0][channel] += mouth[:,None]*(closed-field[part][0][channel])
        field[part][1][channel] *= 1-mouth[:,None]


def fit_archive(imported, original, donor_archive, stock_glb, replacement_glb, output):
    """Restore the native alternate's constants and fit its own 13 shape channels."""
    raw, stock, authored = (decompress(p) for p in (imported, original, donor_archive))
    candidate, before, arena = model(raw, PLAYER), model(stock, PLAYER), model(authored, DONOR)
    index_start, indices, _, vertices = buffers(raw, PLAYER)
    vertices = bytearray(vertices)
    draws = sorted([(DRAW.unpack(draw)[3], draw, effect)
                    for effect in candidate['effects'] for draw in effect['draws']], key=lambda row: row[0])
    originals = [(effect, draw) for effect in arena['effects'] for draw in effect['draws']]
    assert len(draws) == len(originals) == 29
    native_clear = next(effect['mod'] for effect in before['effects'] if not effect['tail'][9])
    groups = []
    cursor = 0
    for effect in arena['effects']:
        grouped = []
        tail = draws[cursor][2]['tail']
        for original_draw in effect['draws']:
            _, imported_draw, imported_effect = draws[cursor]
            assert DRAW.unpack(imported_draw)[5] == DRAW.unpack(original_draw)[5]
            assert imported_effect['tail'][:9] == tail[:9]
            grouped.append(original_draw[:4] + imported_draw[4:])
            cursor += 1
        groups.append({'draws':grouped,'mod':native_clear,'tail':effect['tail'][:4]+tail[4:9]+b'\0'})
    candidate['effects'] = groups
    candidate['tail'] = candidate['tail'][:4]+before['tail'][4:]
    raw = raw[:candidate['start']] + serialize(candidate) + raw[candidate['end']:]
    stock_doc, stock_read = glb(stock_glb)
    result, more, report = fix_model(
        raw, PLAYER, DONOR, None, indices, vertices,
        retarget_fn=lambda *_: fit_outfit(stock_glb,replacement_glb),
        eye_fn=lambda _: EYES, native_eye_data=(stock_doc,stock_read,NATIVE_EYES),
    )
    index_start, _, _, _ = buffers(raw,PLAYER)
    packed = raw[:index_start]+vec(np.concatenate([indices,*more]).astype('<u4').tobytes(),4)+vec(vertices,64)+raw[-2:]
    packed = packed[:result['start']]+serialize(result)+packed[result['end']:]
    assert sum(DRAW.unpack(d)[5] for e in result['effects'] for d in e['draws'])==10886
    assert result['tail'][4:]==before['tail'][4:]
    compress(output, packed)
    return report


def merge_archive(base, outfit, output):
    """Replace just the alternate Merc model, preserving the released GAME assets."""
    raw, source = decompress(base), decompress(outfit)
    target, patch = model(raw, PLAYER), model(source, PLAYER)
    index_start, indices, _, vertices = buffers(raw, PLAYER)
    _, add_indices, _, add_vertices = buffers(source, PLAYER)
    current, texture_end = textures(raw)
    incoming, _ = textures(source)
    known = {texture_bytes(t):i for i,t in enumerate(current)}
    names = {t['name'] for t in current}
    relocated = {}
    old_count = len(current)
    appended_runs = []
    appended_vertices = []
    next_index, next_vertex = len(indices), len(vertices)//64
    source_vertices = np.frombuffer(add_vertices,'u1').reshape(-1,64)

    def texture(i):
        if i < 0:
            return i
        if i in relocated:
            return relocated[i]
        entry = incoming[i]
        packed = texture_bytes(entry)
        index = known.get(packed)
        if index is None:
            if entry['name'] in names:
                entry = dict(entry, name=f'secret-outfit-{i}-{entry["name"]}')
            names.add(entry['name'])
            index = len(current)
            known[packed] = index
            current.append(entry)
        relocated[i] = index
        return index

    def remap(packed, glb_vertices):
        nonlocal next_index, next_vertex
        draw = list(DRAW.unpack(packed))
        old = add_indices[draw[3]:draw[3]+draw[4]]
        draw[1] = texture(draw[1])
        draw[3] = next_index
        next_index += len(old)
        if glb_vertices:
            assert np.all(old != 0xffffffff)
            ids, local = np.unique(old, return_inverse=True)
            appended_vertices.append(source_vertices[ids].tobytes())
            old = local.astype('<u4')+next_vertex
            next_vertex += len(ids)
        appended_runs.append(old.astype('<u4').tobytes())
        return DRAW.pack(*draw)

    result = dict(patch, effects=[])
    for effect in patch['effects']:
        parser = Reader(effect['mod'])
        moving = parser.draws()
        fixed = parser.draws()
        draws = [remap(d,True) for d in effect['draws']]
        fixed_map = {old:new for old,new in zip(effect['draws'],draws)}
        mod = (draw_list([remap(d,False) for d in moving])+draw_list([fixed_map[d] for d in fixed])
               + effect['mod'][parser.pos:])
        tail = bytearray(effect['tail'])
        if tail[8]:
            struct.pack_into('<I',tail,4,texture(struct.unpack_from('<I',tail,4)[0]))
        result['effects'].append(dict(draws=draws,mod=mod,tail=bytes(tail)))
    merged = raw[:index_start]+vec(indices.tobytes()+b''.join(appended_runs),4)+vec(vertices+b''.join(appended_vertices),64)+raw[-2:]
    first = model(raw,'collectables-bomb-blast-lod0')
    count_offset = first['start']-8
    count = struct.unpack_from('<Q', raw, count_offset)[0]
    dark = dict(target,name=DARK_PLAYER)
    merged = (merged[:count_offset]+struct.pack('<Q',count+1)
              +merged[count_offset+8:target['start']]+serialize(result)+serialize(dark)
              +merged[target['end']:])
    header = Reader(raw,2)
    header.take(header.num('Q'))
    merged = (merged[:header.pos]+struct.pack('<Q',len(current))+b''.join(map(texture_bytes,current))
              +merged[texture_end:])
    assert serialize(model(merged,'jakb-c-lod0'))==serialize(model(raw,'jakb-c-lod0'))
    assert serialize(model(merged,'daxter-lod0'))==serialize(model(raw,'daxter-lod0'))
    assert serialize(model(merged,PLAYER))==serialize(result)
    assert serialize(model(merged,DARK_PLAYER))==serialize(dark)
    compress(output,merged)
    return dict(addedTextures=len(current)-old_count, triangles=10886,
                drawEffects=len(result['effects']), staticIds=list(EYES.values()), gameplayChannels=13)


def restore_dark_variant(source, path, process):
    """Keep vanilla alternate Dark Jak's animated palette and claw geometry."""
    from dark_vertex_runtime import render_name, replace_once
    original = render_name(path, process, 'jakb-dark-c-lod0')
    fallback = f'''(if (and *cinematic-dark-eye-mesh?* (= {process} *target*)
                      (string= {path} "{PLAYER}"))
                 "{DARK_PLAYER}"
                 {path})'''
    assert original.endswith(f'{path})')
    return replace_once(source, original, original[:-len(path)-1]+fallback+')')
