"""Restore cinematic effect boundaries lost by the two-bucket GLB importer.

The source exporter emits one primitive per draw, in effect order. Validate that
provenance against the compact GLB and imported triangle counts before regrouping.
Only metadata in the isolated Jak MercModel is rewritten; geometry is untouched.
"""
import json,struct,copy,io
from PIL import Image
from fr3_models import BASE,DRAW,Reader,decompress,compress,model,serialize

# Each cinematic effect takes visibility/alpha/fade from this gameplay effect.
# Verified from both runtime seg-tables, source armor rules and geometry bounds.
SOURCE_EFFECT=[10,17,1,13,13,15,5,4,2,16,16,6,8,11,12,18,16,14]
def main():
    raw=decompress(BASE/'prototype/GAME.fr3')
    assert struct.unpack_from('<H',raw)[0]==43
    target=model(raw,'jakb-c-lod0');assert len(target['effects'])==2
    cinematic=model(decompress(BASE/'extraction/out/jak3/fr3/ARENACST.fr3'),'jakc-highres-lod0')
    glb=(BASE/'prototype/merc_replacements/jakb-c-lod0.glb').read_bytes()
    n=struct.unpack_from('<I',glb,12)[0];doc=json.loads(glb[20:20+n])
    binary=glb[28+n:]
    # The importer also collapses reflection textures. Recover each group's actual
    # texture by decoded pixels, so duplicate image names cannot misidentify it.
    reader=Reader(raw,2);reader.take(reader.num('Q'));textures=[]
    for _ in range(reader.num('Q')):
        w,h,combo=struct.unpack('<HHI',reader.take(8));pixels=reader.vector(4)
        name=reader.take(reader.num('Q')).decode();page=reader.take(reader.num('Q')).decode();reader.take(1)
        textures.append((name,page,w,h,pixels))
    def env_texture(material):
        ti=material['extensions']['KHR_materials_specular']['specularColorTexture']['index']
        tex=doc['textures'][ti];img=doc['images'][tex['source']];view=doc['bufferViews'][img['bufferView']]
        start=view.get('byteOffset',0);decoded=Image.open(io.BytesIO(binary[start:start+view['byteLength']])).convert('RGBA')
        pixels=bytearray(decoded.tobytes());pixels[3::4]=bytes(a>>1 for a in pixels[3::4])
        matches=[i for i,t in enumerate(textures) if t[0]==img['name'] and t[1]=='custom-level' and t[2:4]==decoded.size and t[4]==pixels]
        assert matches, img['name']
        return matches[0],doc['samplers'][tex['sampler']]
    prims=doc['meshes'][0]['primitives'];pi=0;cursors=[0,0];effects=[]
    for ei,old in enumerate(cinematic['effects']):
        kind=int(bool(old['tail'][8]));bucket=target['effects'][kind]
        new=copy.deepcopy(bucket);new['draws']=[]
        envs=[]
        for olddraw in old['draws']:
            primitive=prims[pi];assert primitive['material']==pi
            triangle_count=doc['accessors'][primitive['indices']]['count']//3
            assert triangle_count==DRAW.unpack(olddraw)[5]
            if kind:envs.append(env_texture(doc['materials'][primitive['material']]))
            draw=bucket['draws'][cursors[kind]];cursors[kind]+=1
            assert DRAW.unpack(draw)[5]==triangle_count and DRAW.unpack(draw)[-1]==1
            new['draws'].append(draw);pi+=1
        if kind:
            assert all(e==envs[0] for e in envs), ('Mixed envmaps in original group',ei)
            new['tail']=new['tail'][:4]+struct.pack('<I',envs[0][0])+new['tail'][8:]
        effects.append(new)
    assert pi==len(prims) and cursors==[len(e['draws']) for e in target['effects']]
    assert len(effects)==len(SOURCE_EFFECT)==18
    target['effects']=effects
    fixed=raw[:target['start']]+serialize(target)+raw[target['end']:]
    check=model(fixed,target['name']);assert serialize(check)==serialize(target)
    assert sum(DRAW.unpack(d)[5] for e in check['effects'] for d in e['draws'])==13574
    compress(BASE/'prototype/GAME-armor.fr3',fixed)
    compress(BASE/'extraction/out/jak3/fr3/GAME.fr3',fixed)
    (BASE/'armor-effect-map.json').write_text(json.dumps({'cinematic_to_gameplay_effect':SOURCE_EFFECT,'gameplay_seg_table':[0x1bb00,3,0x40000,0x20000,4,0x4000,0x400,0x40,0x80,0x38],'cinematic_seg_table':[0x17038,4,0x8000,0x600,2,0x100,0x20000,1,0x800,0xc0]},indent=2))
    print('Restored 18 cinematic effects; preserved all 13,574 triangles.')
if __name__=='__main__':main()
