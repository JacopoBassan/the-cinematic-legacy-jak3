"""Fit Dark and Light Jak cutscene geometry to the gameplay renderer."""
from collections import defaultdict
import struct

import numpy as np
from scipy.spatial import cKDTree

from build_npc_expansion import triangles, vec
from fix_faces import draw_list
from fr3_models import DRAW, Reader, compress, decompress, model, serialize
from inspect_faces import buffers
from inspect_transformations import textures

PLAYER = 'jakb-c-lod0'
REGULAR = 'jakc-highres-lod0'
DARK = 'darkjak-highres-lod0'
REPLACEMENT = 'jakb-dark-c-lod0'
FIRST_GAME_MODEL = 'collectables-bomb-blast-lod0'
GAME_WINGS = 'jakb-wings-wings-lod0'
CUTSCENE_WINGS = 'jakc-wings-lod0'

def material(draw, items):
    return items[draw[1]]['name'] if draw[1] >= 0 else draw[1]


def read_mesh(raw, name):
    record = model(raw, name)
    offset, indices, _, vertices = buffers(raw, name)
    items, _ = textures(raw)
    return record, offset, indices, np.frombuffer(vertices, 'u1').reshape(-1, 64), items


def surfaces(record, indices, items):
    grouped = defaultdict(list)
    for effect_index, effect in enumerate(record['effects']):
        for draw_index, packed in enumerate(effect['draws']):
            draw = DRAW.unpack(packed)
            run = indices[draw[3]:draw[3] + draw[4]]
            faces = run.reshape(-1, 3) if draw[6] else triangles(run)
            if len(faces) != draw[5]:
                raise ValueError('Unexpected Dark Jak triangle count')
            grouped[material(draw, items)].append(((effect_index, draw_index), draw, faces))
    return grouped


def vertex_columns(vertices):
    return vertices.view('<f4').reshape(-1, 16)


def partition_surfaces(regular, dark, normal_vertices, dark_vertices):
    """Restore gameplay armor boundaries where the cutscene combined draws."""
    if regular.keys() != dark.keys():
        raise ValueError('Dark and regular cutscene materials differ')
    normal = vertex_columns(normal_vertices)
    donor = vertex_columns(dark_vertices)
    selections = {}
    for name, regions in regular.items():
        sources = dark[name]
        pools = [[] for _ in regions]
        trees = [cKDTree(normal[faces, :3].mean(axis=1)) for _, _, faces in regions]
        for _, draw, faces in sources:
            centers = donor[faces, :3].mean(axis=1)
            distances = np.stack([tree.query(centers)[0] for tree in trees])
            which = distances.argmin(axis=0)
            for region, pool in enumerate(pools):
                chosen = faces[which == region]
                if len(chosen):
                    pool.append((draw, chosen))
        for (key, _, faces), pool in zip(regions, pools):
            if len(pool) != 1 or len(pool[0][1]) != len(faces):
                raise ValueError(f'Unexpected Dark Jak armor split: {name} {key}')
            selections[key] = pool[0]
    return selections


def nearest_vertices(reference, samples, *, eyes=False):
    # Include UVs and normals to distinguish vertices across a seam.
    columns = [0, 1, 2] if eyes else [0, 1, 2, 4, 5, 6, 12, 13]
    scale = np.array([1, 1, 1] if eyes else [1, 1, 1, 12, 12, 12, 36, 36])
    _, matches = cKDTree(reference[:, columns] * scale).query(samples[:, columns] * scale)
    distance = np.linalg.norm(reference[matches, :3] - samples[:, :3], axis=1).max()
    if distance > 24:
        raise ValueError(f'Dark Jak vertex match is {distance:.1f} units away')
    return matches


def morph_data(effect):
    reader = Reader(effect['mod'])
    moving, fixed = reader.draws(), reader.draws()
    vertices = np.frombuffer(reader.vector(64), 'u1').reshape(-1, 64)
    legacy_indices = reader.vector(2)
    legacy_groups = reader.vector(1)
    legacy_count = reader.num('I')
    floats = np.frombuffer(reader.vector(32), '<f4').reshape(-1, 8)
    words = np.frombuffer(reader.vector(4), '<u4')
    if legacy_indices or legacy_groups or legacy_count or reader.pos != len(effect['mod']):
        raise ValueError('Unexpected legacy Dark Jak morph data')
    records = {}
    start = current = 0
    ends = np.flatnonzero(words == 0xffffffff)
    for end in ends:
        channels = words[start:end]
        count = len(channels) + 1
        records[int(words[end + 1])] = (channels, floats[current + 1:current + count])
        current += count
        start = end + 2
    if start != len(words) or current != len(floats):
        raise ValueError('Invalid Dark Jak morph records')
    return moving, fixed, vertices, records


def fit_wings(raw, source, indices, vertices, index_chunks, vertex_chunks, next_index, next_vertex):
    gameplay = model(raw, GAME_WINGS)
    authored, _, donor_indices, donor_vertices, _ = read_mesh(decompress(source), CUTSCENE_WINGS)
    if len(gameplay['effects']) != len(authored['effects']):
        raise ValueError('Light Jak wing effect layout differs')
    count = 0
    for current, donor in zip(gameplay['effects'], authored['effects']):
        if len(current['draws']) != len(donor['draws']) or current['tail'][-1]:
            raise ValueError('Unexpected Light Jak wing draw layout')
        draws = []
        for game_draw, donor_draw in zip(current['draws'], donor['draws']):
            target = list(DRAW.unpack(game_draw))
            origin = DRAW.unpack(donor_draw)
            if (target[:3], target[5:]) != (list(origin[:3]), list(origin[5:])):
                raise ValueError('Unexpected Light Jak wing material or triangle count')
            current_run = indices[target[3]:target[3]+target[4]]
            donor_run = donor_indices[origin[3]:origin[3]+origin[4]]
            current_ids = np.unique(current_run[current_run != 0xffffffff])
            donor_ids, inverse = np.unique(donor_run[donor_run != 0xffffffff], return_inverse=True)
            selected = donor_vertices[donor_ids].copy()
            previous = vertices[current_ids]
            positions = vertex_columns(selected)
            distance, nearby = cKDTree(vertex_columns(previous)[:, :3]).query(positions[:, :3])
            matched = previous[nearby]
            rows = np.arange(len(matched))
            gameplay_joint = matched[rows, 60 + vertex_columns(matched)[:, 8:11].argmax(axis=1)]
            donor_joint = selected[rows, 60 + positions[:, 8:11].argmax(axis=1)]
            if not np.array_equal(gameplay_joint, donor_joint) or distance.max() > 200:
                raise ValueError('Light Jak wing joints do not match')
            selected[:, 32:44] = matched[:, 32:44]
            selected[:, 60:63] = matched[:, 60:63]
            selected[:, 63] = 0
            updated_run = donor_run.copy()
            updated_run[donor_run != 0xffffffff] = inverse + next_vertex
            target[3:5] = [next_index, len(updated_run)]
            index_chunks.append(updated_run.tobytes())
            vertex_chunks.append(selected.tobytes())
            next_index += len(updated_run)
            next_vertex += len(selected)
            count += target[5]
            draws.append(DRAW.pack(*target))
        current['draws'] = draws
    if count != 844:
        raise ValueError('Unexpected Light Jak wing triangle count')
    packed = serialize(gameplay)
    if len(packed) != gameplay['end'] - gameplay['start']:
        raise ValueError('Light Jak wing patch changed the gameplay model layout')
    return raw[:gameplay['start']] + packed + raw[gameplay['end']:], count


def add_models(source, regular, dark, wings, unfitted, output):
    raw = decompress(source)
    gameplay, offset, indices, vertices, items = read_mesh(raw, PLAYER)
    ordinary, _, regular_indices, regular_vertices, regular_items = read_mesh(decompress(regular), REGULAR)
    cinematic, _, dark_indices, dark_vertices, dark_items = read_mesh(decompress(dark), DARK)
    groups = surfaces(ordinary, regular_indices, regular_items)
    replacement = partition_surfaces(groups, surfaces(cinematic, dark_indices, dark_items),
                                     regular_vertices, dark_vertices)
    _, _, _, unfitted_vertices, _ = read_mesh(decompress(unfitted), PLAYER)
    if len(gameplay['effects']) != len(ordinary['effects']):
        raise ValueError('Gameplay armor effects have changed')
    by_regular = {key: (name, draw, faces) for name, values in groups.items()
                  for key, draw, faces in values}

    index_chunks, vertex_chunks = [], []
    next_index, next_vertex = len(indices), len(vertices)

    def append_indices(indices_to_add):
        nonlocal next_index
        start = next_index
        index_chunks.append(np.asarray(indices_to_add, dtype='<u4').tobytes())
        next_index += len(indices_to_add)
        return start

    new_effects = []
    palettes = {}
    authored_triangles = 0
    retained_triangles = 0
    for effect_id, effect in enumerate(gameplay['effects']):
        moving, fixed, morph_vertices, morph_records = morph_data(effect)
        move_key = {(DRAW.unpack(b)[1], DRAW.unpack(b)[2], DRAW.unpack(b)[5]): b for b in moving}
        if len(effect['draws']) != len(ordinary['effects'][effect_id]['draws']):
            raise ValueError('Gameplay Dark Jak effect layout has changed')
        draws, new_moving, new_fixed = [], [], []
        mod_vertices, channels, deltas = [], [], []
        mod_vertex_count = 0
        for draw_id, packed in enumerate(effect['draws']):
            key = (effect_id, draw_id)
            name, normal_draw, _ = by_regular[key]
            draw = list(DRAW.unpack(packed))
            if material(draw, items) != name or (draw[5] != normal_draw[5] and name != 'jakc-waistband2'):
                raise ValueError(f'Gameplay Dark Jak material layout changed: {key} {name}')
            old_move = move_key.pop((draw[1], draw[2], draw[5]), None)
            if name == 'jakc-waistband2':
                if old_move:
                    raise ValueError('Unexpected moving waistband draw')
                draws.append(packed)
                new_fixed.append(packed)
                retained_triangles += draw[5]
                continue
            source_draw, faces = replacement[key]
            if source_draw[1] >= 0 and draw[2] >= 2:
                if dark_items[source_draw[1]]['pixels'] != items[draw[1]]['pixels']:
                    raise ValueError(f'Dark Jak static texture differs: {name}')
            used, local = np.unique(faces, return_inverse=True)
            local = local.ravel()
            selected = dark_vertices[used].copy()
            selected[:, 63] = 0
            data = vertex_columns(selected)
            current_ids = np.unique(indices[draw[3]:draw[3] + draw[4]])
            current_ids = current_ids[current_ids != 0xffffffff]
            unfitted_rows = vertex_columns(unfitted_vertices[current_ids])
            nearest = nearest_vertices(unfitted_rows, data, eyes=draw[2] < 2)
            current = vertices[current_ids[nearest]]
            reference = vertex_columns(current)
            # The cutscene weights target different joint pivots. Keep the
            # verified gameplay weights and the existing waist and cloth fit.
            selected[:, 32:44] = current[:, 32:44]
            selected[:, 60:63] = current[:, 60:63]
            rest = unfitted_rows[nearest]
            data[:, :3] += reference[:, :3] - rest[:, :3]
            fitted = np.any(reference[:, :3] != rest[:, :3], axis=1)
            data[fitted, 4:7] = reference[fitted, 4:7]
            if draw[2] < 2:
                data[:, 12:14] = reference[:, 12:14]
            if name in (-13, -9):
                for row, reference_vertex in enumerate(current):
                    rgb = tuple(int(x) for x in reference_vertex[56:59])
                    if rgb not in palettes:
                        palettes[rgb] = len(palettes) + 1
                    selected[row, 63] = palettes[rgb]

            draw[3] = append_indices(local + next_vertex)
            draw[4] = len(local)
            draw[6] = 1
            next_vertex += len(selected)
            draws.append(DRAW.pack(*draw))
            vertex_chunks.append(selected.tobytes())
            authored_triangles += draw[5]
            if old_move is None:
                new_fixed.append(draws[-1])
                continue

            prior = DRAW.unpack(old_move)
            prior_indices = indices[prior[3]:prior[3] + prior[4]]
            main = DRAW.unpack(packed)
            main_indices = indices[main[3]:main[3] + main[4]]
            if len(main_indices) != len(prior_indices):
                raise ValueError('Dark Jak gameplay morph topology has changed')
            morphed_by_id = dict(zip(main_indices, prior_indices))
            matched_morph_ids = [morphed_by_id[v] for v in current_ids[nearest]]
            draw[3] = append_indices(local + mod_vertex_count)
            new_moving.append(DRAW.pack(*draw))
            for row, original in enumerate(matched_morph_ids):
                if original not in morph_records:
                    continue
                weights, changes = morph_records[original]
                channels.extend((*weights, 0xffffffff, mod_vertex_count + row))
                deltas.extend((data[row, :8], *changes))
            mod_vertices.append(selected.tobytes())
            mod_vertex_count += len(selected)
        if move_key or len(new_moving) != len(moving) or (moving and len(new_fixed) != len(fixed)):
            raise ValueError('Dark Jak lost a gameplay morph or fixed draw')
        mod = (draw_list(new_moving) + draw_list(new_fixed) + vec(b''.join(mod_vertices), 64)
               + vec(b'', 2) + vec(b'', 1) + struct.pack('<I', 0)
               + vec(np.asarray(deltas, dtype='<f4').tobytes(), 32)
               + vec(np.asarray(channels, dtype='<u4').tobytes(), 4))
        new_effects.append({'draws': draws, 'mod': mod if moving else effect['mod'], 'tail': effect['tail']})

    raw, wing_triangles = fit_wings(raw, wings, indices, vertices, index_chunks, vertex_chunks,
                                    next_index, next_vertex)
    first = model(raw, FIRST_GAME_MODEL)
    count_offset = first['start'] - 8
    count = struct.unpack_from('<Q', raw, count_offset)[0]
    cursor = first['start']
    for _ in range(count):
        reader = Reader(raw, cursor)
        name = reader.take(reader.num('Q')).decode('ascii')
        existing = model(raw, name)
        if existing['start'] != cursor:
            raise ValueError('Invalid GAME model table')
        cursor = existing['end']
    if cursor != offset:
        raise ValueError('Invalid GAME model count')

    new = {'name': REPLACEMENT, 'effects': new_effects, 'tail': gameplay['tail']}
    patched = (raw[:count_offset] + struct.pack('<Q', count + 1) + raw[count_offset + 8:offset]
               + serialize(new) + vec(indices.tobytes() + b''.join(index_chunks), 4)
               + vec(vertices.tobytes() + b''.join(vertex_chunks), 64) + raw[-2:])
    if serialize(model(patched, PLAYER)) != serialize(gameplay):
        raise ValueError('Dark Jak build changed the regular gameplay model')
    if len(palettes) > 255:
        raise ValueError('Dark Jak has too many original transition colors')
    compress(output, patched)
    return dict(authoredTriangles=authored_triangles, fittedClothTriangles=retained_triangles, cinematicWingTriangles=wing_triangles,
                transitionColors=list(palettes))
