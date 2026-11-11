"""Restore exact source image bytes after the GLB importer's alpha conversion."""
import hashlib,json,struct
import numpy as np
from pathlib import Path
from fr3_models import BASE,Reader,decompress,compress,model
from inspect_transformations import textures

def main():
    path=BASE/'prototype/GAME-transformations.fr3'
    raw=decompress(path)
    target,end=textures(raw)
    baseline_raw=decompress(BASE/'prototype/GAME-baseline.fr3')
    baseline,baseline_end=textures(baseline_raw)
    donors,_=textures(decompress(BASE/'extraction/out/jak3/fr3/ARENACST.fr3'))
    fixed=[]
    identity=lambda t:(t['name'],t['page'],t['width'],t['height'],t['combo'],t['flag'],hashlib.sha256(t['pixels']).digest())
    existing={identity(t) for t in target if t['page']!='custom-level'}
    for i,tex in enumerate(target):
        if tex['page']!='custom-level':
            continue
        def matches(pool):
            return [t for t in pool if (t['name'],t['width'],t['height'])==(tex['name'],tex['width'],tex['height'])
                    and np.array_equal(np.frombuffer(t['pixels'],'u1').reshape(-1,4)[:,:3],np.frombuffer(tex['pixels'],'u1').reshape(-1,4)[:,:3])]
        baseline_options=matches(baseline)
        options=baseline_options+matches(donors)
        if not options:
            if tex['name']!='white':
                raise ValueError(f'No authoritative source image for {tex["name"]}')
            continue
        if len({hashlib.sha256(t['pixels']).digest() for t in options})!=1:
            raise ValueError(f'Ambiguous source image: {tex["name"]}')
        native=options[0]
        tex['pixels']=native['pixels']
        missing=[t for t in baseline_options if identity(t) not in existing]
        if missing:
            # Only these entries already belong to GAME's global texture table.
            tex.update({key:missing[0][key] for key in ('page','combo','flag')})
            existing.add(identity(missing[0]))
        fixed.append(i)
    reader=Reader(raw,2)
    reader.take(reader.num('Q'))
    output=bytearray(raw[:reader.pos])+struct.pack('<Q',len(target))
    for tex in target:
        output+=struct.pack('<HHI',tex['width'],tex['height'],tex['combo'])
        output+=struct.pack('<Q',len(tex['pixels'])//4)+tex['pixels']
        for key in ('name','page'):
            encoded=tex[key].encode()
            output+=struct.pack('<Q',len(encoded))+encoded
        output+=tex['flag']
    names=json.loads((Path(__file__).parents[1]/'baseline-models.json').read_text())
    first_model=min(model(raw,name)['start'] for name in names)
    first_original=min(model(baseline_raw,name)['start'] for name in names)
    # Preserve every non-Merc block byte for byte, including global indexed
    # textures and the unused collision-header bytes emitted uninitialized by
    # the official extractor. This also makes repeated builds deterministic.
    output+=baseline_raw[baseline_end:first_original]+raw[first_model:]
    compress(path,output)
    (BASE/'authored-textures.json').write_text(json.dumps({'textures_restored':len(fixed),'exact_source_rgba':True},indent=2))
    print(f'Restored exact source RGBA for {len(fixed)} imported textures')

if __name__=='__main__':
    main()
