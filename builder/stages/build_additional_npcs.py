"""Build cinematic Errol and driving Kleiver from local assets."""
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import build_npc_expansion as builder

PAIRS = [('errol-lowres-lod0', 'errol-lod0'),
         ('kleever-rider-lod0', 'kleever-highres-lod0')]

original_map = builder.choose_map
original_effect = builder.donor_effect
original_keep = builder.native_draw_kept

def joint_map(name, supported, model):
    if name in supported:
        return name, name
    if model == 'errol-lowres-lod0' and name == 'LthumbA':
        # Native fragments do not submit thumb A. Keep the authored position
        # and use the adjacent submitted thumb segment for this candidate.
        return 'LthumbB', 'LthumbB'
    if model == 'kleever-rider-lod0':
        aliases = {'LmoustA': 'Lmoust', 'LmoustB': 'Lmoust',
                   'RmoustA': 'Rmoust', 'RmoustB': 'Rmoust',
                   'hairA': 'hairMain', 'hairB': 'hairMain',
                   'SDK_RKneePad': 'RKneePad', 'jawSDK': 'head'}
        if name in aliases:
            target = aliases[name]
            # Keep matching authored frames where possible. The moustache
            # and hair chains use the donor's own corresponding joint frame.
            return target, target if target in builder.CAT['kleever-highres-lod0']['joints'] else name
    return original_map(name, supported, model)

def effect(model, index, texture):
    if model == 'errol-lowres-lod0':
        return {0: 0, 1: 3, 2: 1, 3: 2, 4: 3, 5: 3, 6: 4}[index]
    if model == 'kleever-rider-lod0':
        # First three effects are detachable cinema hand props. Use
        # cinematic eye surfaces with the native eye texture slots.
        return {0: None, 1: None, 2: None, 3: 1, 4: 5, 5: 2,
                6: 3, 7: 0, 8: 4}[index]
    return original_effect(model, index, texture)

def keep(model, index, texture, draw):
    if model == 'errol-lowres-lod0':
        return False
    if model == 'kleever-rider-lod0':
        return False
    return original_keep(model, index, texture, draw)

def build(data_dir, output_dir, metadata_path=None, report_path=None):
    OUT = Path(output_dir)
    metadata_path = metadata_path or Path(__file__).with_name('additional-npc-catalog.json')
    builder.configure(data_dir, OUT, metadata_path)
    builder.choose_map = joint_map
    builder.donor_effect = effect
    builder.native_draw_kept = keep
    try:
        reports = []
        for low, high in PAIRS:
            # The driving actor has no merc-eye controller. Bind the authored
            # cinematic iris directly instead of requesting ungenerated slots.
            options = {'static_eye_texture': 'klever-eye', 'static_eye_pupil': True} if low == 'kleever-rider-lod0' else {}
            if low == 'errol-lowres-lod0':
                options = {'eye_slot_map': {120: 0, 121: 1}}
            if builder.CINEMATIC_EYES and low == 'kleever-rider-lod0':
                options.pop('static_eye_texture', None)
                options.pop('static_eye_pupil', None)
                options['eye_slot_map'] = {i:i%2 for i in range(255)}
            reports.extend(builder.build_one(low, high, submit_all_joints=True, **options))
            if low == 'errol-lowres-lod0':
                for filename in builder.CAT[low]['files']:
                    path = OUT / filename
                    raw = builder.decompress(path)
                    near = builder.model(raw, low)
                    far = builder.model(raw, 'errol-lowres-lod1')
                    replacement = copy.deepcopy(near)
                    replacement['name'] = far['name']
                    # Keep native LOD flags and effect metadata. Both variants
                    # use the same 67-joint rig and five material effects.
                    for a, b in zip(replacement['effects'], far['effects']):
                        a['tail'] = b['tail']
                    replacement['tail'] = near['tail'][:4] + far['tail'][4:]
                    istart, indices, _, vertices = builder.buffers(raw, low)
                    used = builder.used_indices(near, indices)
                    new_vertices = np.frombuffer(vertices, 'u1').reshape(-1, 64)[used].copy()
                    # The distant rig computes only joints through shinPlate_R
                    # (62). Ears, hair and brows are intentionally rigid there.
                    weights = new_vertices.view('<f4').reshape(-1, 16)[:, 8:11]
                    for slot in range(3):
                        missing = (new_vertices[:, 60+slot] >= 63) & (weights[:, slot] > 0)
                        new_vertices[missing, 60+slot] = 6
                    remap = np.full(len(vertices)//64, 0xffffffff, dtype='<u4')
                    remap[used] = np.arange(len(used)) + len(vertices)//64
                    appended = []; cursor = len(indices)
                    for e in replacement['effects']:
                        draws = []
                        for packed in e['draws']:
                            d = list(builder.DRAW.unpack(packed))
                            ix = indices[d[3]:d[3]+d[4]].copy()
                            valid = ix != 0xffffffff; ix[valid] = remap[ix[valid]]
                            d[3] = cursor; cursor += len(ix); appended.append(ix)
                            draws.append(builder.DRAW.pack(*d))
                        e['draws'] = draws
                    fixed = raw[:istart] + builder.vec(np.concatenate([indices]+appended).tobytes(), 4) + builder.vec(vertices+new_vertices.tobytes(), 64) + raw[-2:]
                    fixed = fixed[:far['start']] + builder.serialize(replacement) + fixed[far['end']:]
                    builder.compress(path, fixed)
                    assert builder.decompress(path) == fixed
                    reports.append(dict(file=filename, gameplay=far['name'],
                                        donor=high, distantFaceJointsRigid=True,
                                        requires_full_joint_submission=True,
                                        status='staged; runtime validation required'))
        for item in reports:
            path = OUT / item['file']
            item['output_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            item['output_bytes'] = path.stat().st_size
        if report_path:
            Path(report_path).write_text(json.dumps(reports, indent=2))
        return reports
    finally:
        builder.choose_map = original_map
        builder.donor_effect = original_effect
        builder.native_draw_kept = original_keep
