"""Build replacement GLBs from locally extracted originals, without Blender.

Only joint assignments and material layout are changed. All surfaces and image
bytes are read from the user's extraction at build time.
"""
import base64
import copy
import json
import struct
import sys
from pathlib import Path
import numpy as np


def load(path):
    raw = Path(path).read_bytes()
    n = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20+n])
    binary = raw[28+n:]
    def accessor(i):
        a = doc['accessors'][i]
        v = doc['bufferViews'][a['bufferView']]
        dt = np.dtype({5126:'<f4',5125:'<u4',5123:'<u2',5121:'u1'}[a['componentType']])
        width = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
        return np.ndarray((a['count'],width),dt,buffer=binary,
                          offset=v.get('byteOffset',0)+a.get('byteOffset',0),
                          strides=(v.get('byteStride',width*dt.itemsize),dt.itemsize)).copy()
    return doc, binary, accessor


def retarget(low_path, high_path, output, *, donor_path=None, joint_aliases=None,
             preserve_morph_targets=False, use_default_aliases=True):
    low, _, low_acc = load(low_path)
    high, high_binary, acc = load(high_path)
    sys.path.insert(0,str(Path(__file__).parent/'stages'))
    from fr3_models import decompress
    from inspect_faces import buffers
    donor = decompress(donor_path if donor_path is not None else
                       high_path.parent.parent/'extraction/out/jak3/fr3/ARENACST.fr3')
    _,_,_,source_vertices = buffers(donor,high_path.stem)
    source_colors = np.frombuffer(source_vertices,'u1').reshape(-1,64)[:,56:60]
    source_positions = np.frombuffer(source_vertices,'<f4').reshape(-1,16)[:,:3]
    low_names = [low['nodes'][i]['name'] for i in low['skins'][0]['joints']]
    high_names = [high['nodes'][i]['name'] for i in high['skins'][0]['joints']]
    mapping = {name:name for name in high_names if name in low_names}
    if use_default_aliases and output.stem.startswith('jak'):
        mapping['RshoulderPad'] = 'Rshould'
    elif use_default_aliases:
        mapping.update({n:'head' for n in high_names if n.startswith('tongue') or n == 'uvula'})
        mapping['tailFirst'] = 'tailBase'
        for side in ['L','R']:
            for finger in ['pinky','ring']:
                for part in ['A','B']:
                    mapping[side+finger+part] = side+'middle'+part
    if joint_aliases:
        mapping.update(joint_aliases)
    for source, target in mapping.items():
        if source in high_names and target not in low_names:
            raise ValueError(f'Alias {source} targets missing gameplay joint {target}')
    out = {'asset':{'version':'2.0','generator':'Cinematic Jak and Daxter local builder'},
           'nodes':copy.deepcopy(low['nodes']), 'scenes':copy.deepcopy(low['scenes']),
           'skins':copy.deepcopy(low['skins']), 'meshes':[{'name':output.stem,'primitives':[]}],
           'accessors':[], 'bufferViews':[], 'buffers':[],
           'materials':[], 'textures':copy.deepcopy(high['textures']),
           'images':copy.deepcopy(high['images']), 'samplers':copy.deepcopy(high['samplers']),
           'extensionsUsed':copy.deepcopy(high.get('extensionsUsed',[]))}
    binary = bytearray()
    def view(data):
        binary.extend(b'\0'*(-len(binary)%4))
        vi = len(out['bufferViews'])
        out['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(data)})
        binary.extend(data)
        return vi
    def add(a, component, kind):
        ai = len(out['accessors'])
        entry = {'bufferView':view(a.tobytes()),'componentType':component,'count':len(a),'type':kind}
        if kind == 'VEC3':
            entry.update(min=a.min(0).tolist(),max=a.max(0).tolist())
        out['accessors'].append(entry)
        return ai
    # The renderer consumes joint indices; retain the complete native skeleton.
    for node in out['nodes']:
        if 'mesh' in node:
            node['mesh'] = 0
            node.setdefault('extras',{})['enable_custom_weights'] = 1
            node.pop('weights',None)
    out['skins'][0]['inverseBindMatrices'] = add(low_acc(low['skins'][0]['inverseBindMatrices']),5126,'MAT4')
    for image in out['images']:
        if 'uri' in image:
            prefix, encoded = image.pop('uri').split(',',1)
            if not prefix.startswith('data:image/png;base64'):
                raise ValueError('Unsupported embedded source image')
            image['bufferView'] = view(base64.b64decode(encoded))
            image['mimeType'] = 'image/png'
        else:
            source = high['bufferViews'][image['bufferView']]
            start = source.get('byteOffset',0)
            image['bufferView'] = view(high_binary[start:start+source['byteLength']])
    for p in high['meshes'][0]['primitives']:
        indices = acc(p['indices']).ravel()
        used, inverse = np.unique(indices,return_inverse=True)
        attrs = {}
        for key, ai in p['attributes'].items():
            if key in ('JOINTS_0','WEIGHTS_0'):
                continue
            source_a = high['accessors'][ai]
            values = acc(ai)[used]
            if key == 'COLOR_0':
                attrs[key] = add(source_colors[used].copy(),5121,'VEC4')
                out['accessors'][attrs[key]]['normalized'] = True
                continue
            if key == 'POSITION' and not np.array_equal(values*4096,source_positions[used]):
                raise ValueError('Extracted model vertex provenance changed')
            if key == 'NORMAL':
                values /= np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-20)
            attrs[key] = add(values,source_a['componentType'],source_a['type'])
        old_joints = acc(p['attributes']['JOINTS_0'])[used]
        old_weights = acc(p['attributes']['WEIGHTS_0'])[used]
        joints = np.zeros_like(old_joints,dtype='u1')
        weights = np.zeros_like(old_weights,dtype='<f4')
        for row in range(len(used)):
            totals = {}
            for joint, weight in zip(old_joints[row],old_weights[row]):
                if weight <= 0:
                    continue
                name = high_names[int(joint)]
                if name not in mapping:
                    raise ValueError(f'Unmapped active joint {name}')
                target = low_names.index(mapping[name])
                totals[target] = totals.get(target,0.0)+float(weight)
            ordered = sorted(totals.items(),key=lambda pair:-pair[1])
            if len(ordered)>3:
                raise ValueError('OpenGOAL supports at most three active joint weights')
            total = sum(weight for _,weight in ordered)
            if not np.isfinite(total) or total <= 0 or not np.isfinite(old_weights[row]).all():
                raise ValueError('Invalid source joint weights')
            for column,(joint,weight) in enumerate(ordered):
                joints[row,column] = joint
                weights[row,column] = weight/total
        attrs['JOINTS_0'] = add(joints,5121,'VEC4')
        attrs['WEIGHTS_0'] = add(weights,5126,'VEC4')
        # Each draw needs a distinct material for the effect reconstruction stage.
        material = copy.deepcopy(high['materials'][p['material']])
        mi = len(out['materials'])
        out['materials'].append(material)
        primitive = {'attributes':attrs,'indices':add(inverse.astype('<u4').reshape(-1,1),5125,'SCALAR'),'material':mi,'mode':4}
        if preserve_morph_targets:
            primitive['targets'] = [
                {key:add(acc(ai)[used],high['accessors'][ai]['componentType'],
                         high['accessors'][ai]['type']) for key,ai in target.items()}
                for target in p.get('targets',[])]
        out['meshes'][0]['primitives'].append(primitive)
    if preserve_morph_targets:
        for key in ('weights','extras'):
            if key in high['meshes'][0]:
                out['meshes'][0][key] = copy.deepcopy(high['meshes'][0][key])
    out['buffers'] = [{'byteLength':len(binary)}]
    encoded = json.dumps(out,separators=(',',':')).encode()
    encoded += b' '*(-len(encoded)%4)
    binary += b'\0'*(-len(binary)%4)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(struct.pack('<III',0x46546c67,2,28+len(encoded)+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary)


def main(work):
    work = Path(work)
    for low,high in [('jakb-c-lod0','jakc-highres-lod0'),('daxter-lod0','daxter-highres-lod0')]:
        retarget(work/'models'/f'{low}.glb',work/'models'/f'{high}.glb',work/'prototype'/'merc_replacements'/f'{low}.glb')


if __name__ == '__main__':
    import sys
    main(sys.argv[1])
