import json,struct,hashlib,os,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parent/'stages'))
from fr3_models import BASE,Reader,DRAW,decompress,model,serialize
from inspect_faces import buffers,glb
from inspect_transformations import textures
from group_armor_effects import SOURCE_EFFECT
from dax_effects import SOURCE_EFFECT as DAX_GEOMETRY_EFFECTS
sys.path.insert(0,str(Path(__file__).parent))
from dax_foreground import SOURCE_EFFECT as DAX_RUNTIME_EFFECTS
assert DAX_GEOMETRY_EFFECTS==DAX_RUNTIME_EFFECTS
report={}
raw=decompress(BASE/'prototype/GAME-transformations.fr3');original=decompress(BASE/'prototype/GAME-baseline.fr3')
assert struct.unpack_from('<H',raw)[0]==43
tex,tex_end=textures(raw);_,ix,_,vb=buffers(raw,'jakb-c-lod0')
oldtex,oldtex_end=textures(original);_,oldix,_,oldvb=buffers(original,'jakb-c-lod0')
from collections import Counter
def texture_identity(t):
    return (t['name'],t['page'],t['width'],t['height'],t['combo'],t['flag'],hashlib.sha256(t['pixels']).hexdigest())
missing=Counter(map(texture_identity,oldtex))-Counter(map(texture_identity,tex))
assert not missing,('Original global textures missing or changed',list(missing)[:10])
assert any(t['name']=='security-env-dest' for t in tex)
report['all_original_global_textures_preserved']=len(oldtex)
v=np.frombuffer(vb,'<f4').reshape(-1,16);b=np.frombuffer(vb,'u1').reshape(-1,64)
models=[]
for name,nchannels,ntris in [('jakb-c-lod0',13,13574),('daxter-lod0',9,9510)]:
    m=model(raw,name);assert serialize(m)==raw[m['start']:m['end']]
    assert m['tail'][8:]==model(original,name)['tail'][8:], 'Native decode constants changed'
    maxdraw,maxbones,_,scale,_=struct.unpack('<IIIff',m['tail'])
    channels=set();eyes=[];slots=set();tris=0;animated=0
    for e in m['effects']:
        if e['tail'][8]:assert struct.unpack_from('<I',e['tail'],4)[0]<len(tex)
        for draw in e['draws']:
            d=DRAW.unpack(draw);ids=ix[d[3]:d[3]+d[4]];assert len(ids)==d[4]
            ids=ids[ids!=0xffffffff];assert len(ids) and ids.max()<len(v)
            assert np.isfinite(v[ids,:14]).all()
            assert np.allclose(v[ids,8:11].sum(1),1,atol=0.002)
            active=v[ids,8:11]>0
            assert b[ids,60:63][active].max()<=maxbones
            assert d[1]<len(tex)
            if d[1]<0:slots.add(d[1])
            if d[2]!=255:eyes.append(d[2])
            tris+=d[5]
        r=Reader(e['mod']);md=r.draws();fd=r.draws();mv=r.vector(64);r.vector(2);r.vector(1);r.take(4)
        floats=np.frombuffer(r.vector(32),'<f4').reshape(-1,8);ints=np.frombuffer(r.vector(4),'<u4')
        assert r.pos==len(e['mod']) and np.isfinite(floats).all()
        if md:
            assert e['tail'][9]
            assert sum(DRAW.unpack(d)[5] for d in md+fd)==sum(DRAW.unpack(d)[5] for d in e['draws'])
        for draw in md:
            d=DRAW.unpack(draw);ids=ix[d[3]:d[3]+d[4]]
            assert len(ids)==d[4] and ids.max()<len(mv)//64
        fi=ii=0
        while ii<len(ints):
            base=floats[fi];fi+=1
            while ints[ii]!=0xffffffff:
                ch=int(ints[ii]);assert 0<=ch<nchannels;channels.add(ch);fi+=1;ii+=1
            dest=int(ints[ii+1]);ii+=2;assert dest<len(mv)//64
            assert np.array_equal(base,np.frombuffer(mv[dest*64:dest*64+32],'<f4'))
            animated+=1
        assert fi==len(floats)
    assert tris==ntris and sorted(eyes)==[0,1] and channels==set(range(nchannels))
    if name=='jakb-c-lod0':assert len(m['effects'])==len(SOURCE_EFFECT)==18 and slots=={-13,-9,-12,-7,-11}
    else:assert len(m['effects'])==7
    models.append(dict(name=name,triangles=tris,effects=len(m['effects']),channels=sorted(channels),eyes=eyes,animated_vertices=animated,animated_texture_slots=sorted(slots),max_bones=maxbones))
report['models']=models
catalog=json.loads((Path(__file__).parent/'baseline-models.json').read_text())
model_start=min(model(raw,n)['start'] for n in catalog)
old_model_start=min(model(original,n)['start'] for n in catalog)
assert raw[tex_end:model_start]==original[oldtex_end:old_model_start], 'Non-Merc graphics data changed'
report['non_merc_graphics_data_preserved']=True
unchanged=[];other_changes=[]
for name,locations in catalog.items():
    if name in {'jakb-c-lod0','daxter-lod0'}:continue
    if any(x['file'].lower()=='game.fr3' for x in locations):
        ma,mb=model(original,name),model(raw,name)
        if serialize(ma)==serialize(mb):unchanged.append(name)
        else:
            assert ma['tail']==mb['tail'] and len(ma['effects'])==len(mb['effects'])
            def same_texture(a,z):
                if a<0 or z<0:assert a==z;return
                for k in ['width','height','pixels']:assert oldtex[a][k]==tex[z][k]
            def check_draws(ad,bd):
                assert len(ad)==len(bd)
                for aa,bb in zip(ad,bd):
                    a,z=DRAW.unpack(aa),DRAW.unpack(bb)
                    assert (a[0],a[2],a[4:])==(z[0],z[2],z[4:])
                    same_texture(a[1],z[1])
                    ai=oldix[a[3]:a[3]+a[4]];bi=ix[z[3]:z[3]+z[4]]
                    assert np.array_equal(ai==0xffffffff,bi==0xffffffff)
                    for av,bv in zip(ai,bi):
                        if av!=0xffffffff:assert oldvb[int(av)*64:(int(av)+1)*64]==vb[int(bv)*64:(int(bv)+1)*64]
            for ae,be in zip(ma['effects'],mb['effects']):
                assert ae['tail'][:4]+ae['tail'][8:]==be['tail'][:4]+be['tail'][8:]
                if ae['tail'][8]:same_texture(struct.unpack_from('<i',ae['tail'],4)[0],struct.unpack_from('<i',be['tail'],4)[0])
                check_draws(ae['draws'],be['draws'])
                ar,br=Reader(ae['mod']),Reader(be['mod'])
                # Non-replaced models retain local morph vertices and data.
                for kind in range(2):
                    ads,bds=ar.draws(),br.draws()
                    if kind==1:
                        check_draws(ads,bds);continue
                    assert len(ads)==len(bds)
                    for ad,bd in zip(ads,bds):
                        a,z=DRAW.unpack(ad),DRAW.unpack(bd)
                        assert (a[0],a[2],a[4:])==(z[0],z[2],z[4:]);same_texture(a[1],z[1])
                        assert np.array_equal(oldix[a[3]:a[3]+a[4]],ix[z[3]:z[3]+z[4]])
                assert ae['mod'][ar.pos:]==be['mod'][br.pos:]
            other_changes.append(name)
report['unchanged_other_GAME_models']=unchanged
report['other_models_verified_equivalent_after_buffer_texture_relocation']=other_changes


# Exact authored RGB/alpha verification per triangle vertex. The direct exporter
# preserves these values from local source data, including constant-color parts.
color_checks=[]
for low,high in [('jakb-c-lod0','jakc-highres-lod0'),('daxter-lod0','daxter-highres-lod0')]:
    doc,acc=glb(BASE/'prototype/merc_replacements'/f'{low}.glb')
    source_doc,source_acc=glb(BASE/'models'/f'{high}.glb')
    donor=decompress(BASE/'extraction/out/jak3/fr3/ARENACST.fr3')
    _,_,_,donor_vb=buffers(donor,high)
    donor_colors=np.frombuffer(donor_vb,'u1').reshape(-1,64)[:,56:60]
    draws=sorted([d for e in model(raw,low)['effects'] for d in e['draws']],key=lambda d:DRAW.unpack(d)[3])
    assert len(draws)==len(doc['meshes'][0]['primitives'])
    assert len(draws)==len(source_doc['meshes'][0]['primitives'])
    for draw,prim,source_prim in zip(draws,doc['meshes'][0]['primitives'],source_doc['meshes'][0]['primitives']):
        d=DRAW.unpack(draw); ids=ix[d[3]:d[3]+d[4]]; local=acc(prim['indices']).ravel()
        expected=acc(prim['attributes']['COLOR_0'])[local]
        assert np.array_equal(b[ids,56:60],expected),(low,'authored colors changed')
        native=donor_colors[source_acc(source_prim['indices']).ravel()]
        assert np.array_equal(b[ids,56:60],native),(low,'source FR3 authored colors changed')
    source_model=model(donor,high)
    target_model=model(raw,low)
    source_tex,_=textures(donor)
    for effect,native in zip(target_model['effects'],source_model['effects']):
        assert effect['tail'][:4]==native['tail'][:4]
        assert [d[:4] for d in effect['draws']]==[d[:4] for d in native['draws']]
        if native['tail'][8]:
            a=tex[struct.unpack_from('<I',effect['tail'],4)[0]]
            z=source_tex[struct.unpack_from('<I',native['tail'],4)[0]]
            assert (a['width'],a['height'],a['pixels'])==(z['width'],z['height'],z['pixels'])
    color_checks.append(low)
report['authored_vertex_colors_preserved']=color_checks
(BASE/'validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'validated_models':len(report['models']),'other_models_preserved':len(unchanged)+len(other_changes),'authored_colors':color_checks}))
