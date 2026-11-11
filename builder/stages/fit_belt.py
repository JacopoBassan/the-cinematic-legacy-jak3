"""Fit cinematic waist clothing; cinematic_skirt.py adapts the separate cloth."""
import struct, hashlib, copy
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from fr3_models import DRAW, Reader, model, serialize, decompress, compress
from inspect_faces import buffers
from inspect_transformations import textures
from build_npc_expansion import triangles
from fix_faces import closest_surface

MATERIALS={'jakchires-brownstrap','jakchires-brwnleather','jakchires-clips'}

def native_skirt_join(raw,base):
    """Keep the authored skirt support band under the cinematic leather belt."""
    from build_npc_expansion import texture_bytes
    name='jakb-c-lod0';m=model(raw,name);original=model(base,name)
    assert len(m['effects'])==18
    start,ix,_,vb=buffers(raw,name);_,bi,_,bv=buffers(base,name)
    tx,_=textures(raw);bt,_=textures(base)
    lookup={texture_bytes(t):i for i,t in enumerate(tx)}
    selected=[list(DRAW.unpack(d)) for d in original['effects'][3]['draws'] if bt[DRAW.unpack(d)[1]]['name']=='jakc-waistband2']
    assert len(selected)==1
    d=selected[0];old=bi[d[3]:d[3]+d[4]];ids=np.unique(old[old!=0xffffffff])
    mapping={int(v):i+len(vb)//64 for i,v in enumerate(ids)}
    added=np.array([mapping[int(i)] if i!=0xffffffff else i for i in old],dtype='<u4')
    d[1]=lookup[texture_bytes(bt[d[1]])];d[3]=len(ix);replacement=DRAW.pack(*d)
    def replace(ds):
        return [replacement if DRAW.unpack(x)[1]>=0 and tx[DRAW.unpack(x)[1]]['name']=='jakc-waistband2' else x for x in ds]
    new=copy.deepcopy(m)
    assert sum(tx[DRAW.unpack(x)[1]]['name']=='jakc-waistband2' for e in m['effects'] for x in e['draws'] if DRAW.unpack(x)[1]>=0)==1
    for e in new['effects']:
        e['draws']=replace(e['draws'])
        r=Reader(e['mod']);md=r.draws();fd=r.draws()
        assert not any(DRAW.unpack(x)[1]>=0 and tx[DRAW.unpack(x)[1]]['name']=='jakc-waistband2' for x in md)
        fd=replace(fd)
        e['mod']=struct.pack('<Q',len(md))+b''.join(md)+struct.pack('<Q',len(fd))+b''.join(fd)+e['mod'][r.pos:]
    vertices=np.frombuffer(bv,'u1').reshape(-1,64)[ids].tobytes()
    allix=ix.tobytes()+added.tobytes();allvb=vb+vertices
    fixed=raw[:start]+struct.pack('<Q',len(allix)//4)+allix+struct.pack('<Q',len(allvb)//64)+allvb+raw[-2:]
    sm=serialize(new);assert len(sm)==m['end']-m['start']
    fixed=fixed[:m['start']]+sm+fixed[m['end']:]
    _,check_ix,_,check_vb=buffers(fixed,name)
    assert check_vb[:len(vb)]==vb and check_vb[len(vb):]==vertices
    assert np.array_equal(check_ix[:len(ix)],ix)
    return fixed,{'nativeEffect':3,'vertices':len(ids),'triangles':d[5],'clothModified':False,'cinematicLeatherBeltRetained':True}

def hem_vertices(draws,indices,vb,tx):
    positions=np.frombuffer(vb,'<f4').reshape(-1,16)[:,:3]/4096
    result={}
    for packed in draws:
        d=DRAW.unpack(packed)
        if d[1]<0 or tx[d[1]]['name'] not in {'jakchires-jacket','jakchires-brownstrap'}:continue
        ids=indices[d[3]:d[3]+d[4]];ids=np.unique(ids[ids!=0xffffffff])
        for i in ids:
            y=positions[i,1]
            if 1.65 <= y < 1.8 or (tx[d[1]]['name']=='jakchires-jacket' and y<1.8):
                result[int(i)]=float(np.clip((1.8-y)/(1.8-1.5927197),0,1))
    return result

def pants_vertices(draws,indices,vb,tx):
    positions=np.frombuffer(vb,'<f4').reshape(-1,16)[:,:3]/4096
    result={}
    for packed in draws:
        d=DRAW.unpack(packed)
        if d[1]<0 or tx[d[1]]['name']!='jakchires-pants':continue
        ids=indices[d[3]:d[3]+d[4]];ids=np.unique(ids[ids!=0xffffffff])
        for i in ids:
            y=positions[i,1]
            if y>1.3:result[int(i)]=float(np.clip((y-1.3)/(1.5001562-1.3),0,1))
    return result

def selected(draws,indices,vb,tx,materials=MATERIALS):
    positions=np.frombuffer(vb,'<f4').reshape(-1,16)[:,:3]
    result=set()
    for packed in draws:
        d=DRAW.unpack(packed)
        if d[1]<0 or tx[d[1]]['name'] not in materials:continue
        draw_indices=indices[d[3]:d[3]+d[4]]
        tris=draw_indices.reshape(-1,3) if d[6] else triangles(draw_indices)
        for tri in tris:
            inside=[1.4*4096 < positions[i,1] < 1.65*4096 for i in tri]
            if any(inside) and not all(inside):
                raise ValueError('Selection cuts a connected surface')
            if all(inside):result.update(int(i) for i in tri)
    return result

def fit_belt(source, native, output):
    source,native,output=map(Path,(source,native,output))
    if output.resolve() in (source.resolve(),native.resolve()):
        raise ValueError('Belt fitting requires a separate output')
    raw=decompress(source);base=decompress(native);name='jakb-c-lod0'
    m=model(raw,name);tx,_=textures(raw);bt,_=textures(base)
    _,indices,vstart,vb=buffers(raw,name);_,bi,_,bv=buffers(base,name)
    p=np.frombuffer(vb,'<f4').reshape(-1,16)
    np_=np.frombuffer(bv,'<f4').reshape(-1,16)
    def band(draws,ix,tex):
        parts=[]
        for packed in draws:
            d=DRAW.unpack(packed)
            if d[1]>=0 and tex[d[1]]['name'] in {'jakc-beltbuckles','jakc-waistband','jakc-brownstrap','jakc-leatherstrap'}:
                q=ix[d[3]:d[3]+d[4]];parts.extend(q[q!=0xffffffff])
        return np.unique(parts)
    native_ids=band(model(base,name)['effects'][3]['draws'],bi,bt)
    donor_ids=sorted(selected([d for e in m['effects'] for d in e['draws']],indices,vb,tx,MATERIALS-{'jakc-waistband2'}))
    dy=float((np_[native_ids,1].min()+np_[native_ids,1].max()-p[donor_ids,1].min()-p[donor_ids,1].max())/2)
    belt_scale=np.ptp(np_[native_ids,:3],axis=0)/np.ptp(p[donor_ids,:3],axis=0)
    belt_offset=np_[native_ids,:3].min(0)-p[donor_ids,:3].min(0)*belt_scale
    assert -512 < dy < -64
    # Transfer the native waist's actual three-influence skinning. This keeps
    # cinematic clothing attached like the gameplay cloth without inventing
    # a fourth influence or replacing large chest weights with a parent.
    native_model=model(base,name)
    pants_tris=[]
    for packed in native_model['effects'][16]['draws']:
        d=DRAW.unpack(packed)
        if d[1]>=0 and bt[d[1]]['name']=='jakc-pants':
            values=bi[d[3]:d[3]+d[4]]
            pants_tris.extend(values.reshape(-1,3) if d[6] else triangles(values))
    pants_tris=np.asarray(pants_tris)
    assert len(pants_tris)>0
    references={}
    for group,effects,materials in (
        ('belt',(3,),{'jakc-beltbuckles','jakc-waistband','jakc-waistband2','jakc-brownstrap','jakc-leatherstrap'}),
        ('hem',(7,),{'jakc-jacketbody'}),
        ('pants',(16,),{'jakc-pants'})):
        refs=[]
        for ei in effects:
            for packed in native_model['effects'][ei]['draws']:
                d=DRAW.unpack(packed)
                if d[1]>=0 and bt[d[1]]['name'] in materials:
                    values=bi[d[3]:d[3]+d[4]];refs.extend(values[values!=0xffffffff])
        refs=np.unique(refs)
        assert len(refs)>0
        references[group]=(cKDTree(np_[refs,:3]),refs)
    maximum_transfer_distance=0.0
    def patch(vb,ids,hem,pants):
        nonlocal maximum_transfer_distance
        out=bytearray(vb)
        factors=dict(hem);factors.update(pants);factors.update({i:1.0 for i in ids})
        for i,factor in factors.items():
            shift=dy-40.96 if i in hem else dy
            # One centimetre of hidden hem overlap prevents a slit during turns.
            struct.pack_into('<f',out,i*64+4,struct.unpack_from('<f',vb,i*64+4)[0]+shift*factor)
            if i in ids:
                point=np.array(struct.unpack_from('<3f',vb,i*64))
                struct.pack_into('<3f',out,i*64,*(point*belt_scale+belt_offset))
                normal=np.array(struct.unpack_from('<3f',vb,i*64+16))/belt_scale
                normal/=np.linalg.norm(normal)
                struct.pack_into('<3f',out,i*64+16,*normal)
            group='belt' if i in ids else ('pants' if i in pants else 'hem')
            tree,refs=references[group]
            distance,nearest=tree.query(struct.unpack_from('<3f',out,i*64))
            if distance>1024:raise ValueError(f'Waist skin reference too distant: {group}, {distance}')
            maximum_transfer_distance=max(maximum_transfer_distance,float(distance))
            ref=int(refs[nearest])
            out[i*64+32:i*64+44]=bv[ref*64+32:ref*64+44]
            out[i*64+60:i*64+63]=bv[ref*64+60:ref*64+63]
            if i in hem or i in pants:
                normal=np.array(struct.unpack_from('<3f',vb,i*64+16))
                stretch=(1+dy/(4096*(1.5001562-1.3)) if i in pants else 1-shift/(4096*(1.8-1.5927197)))
                assert stretch>0
                normal[1]/=stretch
                normal/=np.linalg.norm(normal)
                struct.pack_into('<3f',out,i*64+16,*normal)
            if i in pants:
                point=np.array(struct.unpack_from('<3f',out,i*64))
                nearest,bary,distance=closest_surface(point[None,:],np_[pants_tris,:3])
                assert distance[0]<1024
                target=bary[0]@np_[pants_tris[nearest[0]],:3]
                struct.pack_into('<3f',out,i*64,*(point+(target-point)*factor))
                target_normal=bary[0]@np_[pants_tris[nearest[0]],4:7]
                normal=np.array(struct.unpack_from('<3f',out,i*64+16))
                normal=normal*(1-factor)+target_normal*factor
                normal/=np.linalg.norm(normal)
                struct.pack_into('<3f',out,i*64+16,*normal)
        before=np.frombuffer(vb,'u1').reshape(-1,64);after=np.frombuffer(out,'u1').reshape(-1,64)
        allowed=np.zeros(before.shape,bool)
        for i in factors:allowed[i,4:8]=True;allowed[i,32:44]=True;allowed[i,60:63]=True
        for i in set(hem)|set(pants)|set(ids):allowed[i,16:28]=True
        for i in ids:allowed[i,:12]=True
        for i in pants:allowed[i,:12]=True
        assert not np.any((before!=after)&~allowed)
        active=np.array(sorted(factors))
        if len(active):
            values=np.frombuffer(out,'<f4').reshape(-1,16)[active]
            assert np.isfinite(values[:,:11]).all()
            assert np.allclose(values[:,8:11].sum(1),1,atol=0.01)
        return bytes(out)
    ids=set();hem={};pants={};mods=[]
    for ei,e in enumerate(m['effects']):
        r=Reader(e['mod']);md=r.draws();fd=r.draws();off=r.pos+8;mv=r.vector(64)
        ids.update(selected(e['draws']+fd,indices,vb,tx))
        hem.update(hem_vertices(e['draws']+fd,indices,vb,tx))
        pants.update(pants_vertices(e['draws']+fd,indices,vb,tx))
        mids=selected(md,indices,mv,tx)
        mhem=hem_vertices(md,indices,mv,tx)
        mpants=pants_vertices(md,indices,mv,tx)
        if mids or mhem or mpants:
            e['mod']=e['mod'][:off]+patch(mv,mids,mhem,mpants)+e['mod'][off+len(mv):]
            mods.append({'effect':ei,'vertices':len(mids),'hemVertices':len(mhem),'pantsVertices':len(mpants)})
    assert ids
    patched=bytearray(raw);patched[vstart+8:vstart+8+len(vb)]=patch(vb,ids,hem,pants)
    sm=serialize(m);assert len(sm)==m['end']-m['start']
    patched[m['start']:m['end']]=sm
    patched,join_report=native_skirt_join(bytes(patched),base)
    compress(output,patched)
    assert decompress(output)==patched
    report={'status':'static validation passed; runtime validation separate','verticalShiftMetres':dy/4096,'globalVertices':len(ids),'hemVertices':len(hem),'pantsVertices':len(pants),'modVertices':mods,'duplicateBeltAdded':False,'characterScaleChanged':False,'skinning':'native gameplay belt, jacket and pants weights from corresponding local surfaces'}
    report['sourceSha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    report['maximumSkinTransferDistanceMetres']=maximum_transfer_distance/4096
    report['waistbandFit']=join_report
    report['leatherBeltFitScale']=belt_scale.tolist()
    report['outputSha256']=hashlib.sha256(output.read_bytes()).hexdigest()
    return report
