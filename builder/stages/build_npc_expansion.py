"""Build cinematic NPC replacements against the pinned local game data.

Outputs are staged, never installed. Preserve gameplay effect layout, eye routing,
material response, skeleton capacity and prop visibility. Cinema-only facial
controllers are not copied to actors without matching gameplay channels.
"""
import copy, hashlib, json, re, struct
from pathlib import Path
import numpy as np
from fr3_models import BASE, Reader, DRAW, decompress, compress, model, serialize
from inspect_faces import buffers
from inspect_transformations import textures
import npc_rigs
from npc_rigs import skeleton

ROOT = None
OUT = BASE/'prototype/expansion-npcs'
CAT = {}
FULLCAT = {}
CINEMATIC_EYES = False
REPORT_PATH = BASE/'npc-expansion-build.json'
EMPTY_MOD = struct.pack('<5QI2Q', 0,0,0,0,0,0,0,0)

def configure(data_dir=None, output_dir=None, metadata_path=None, report_path=None):
    """Configure an extracted local data root and staging output, never install."""
    global ROOT, OUT, CAT, FULLCAT, REPORT_PATH
    if data_dir is None:
        raise ValueError('Pass the original extracted game data directory explicitly')
    data=Path(data_dir)
    ROOT=data/'out/jak3/fr3';npc_rigs.RAW=data/'decompiler_out/jak3/raw_obj'
    OUT=Path(output_dir) if output_dir is not None else BASE/'prototype/expansion-npcs'
    meta=Path(metadata_path) if metadata_path is not None else Path(__file__).with_name('npc-build-catalog.json')
    catalog=json.loads(meta.read_text());assert catalog['schema']==1
    CAT={x['name']:x for x in catalog['models']};FULLCAT={}
    for filename,names in catalog['file_models'].items():
        for name in names:FULLCAT.setdefault(name,[]).append({'file':filename})
    REPORT_PATH=Path(report_path) if report_path is not None else BASE/'npc-expansion-build.json'

def vec(b, size): return struct.pack('<Q',len(b)//size)+b
def texture_bytes(t):
    def s(x): b=x.encode(); return struct.pack('<Q',len(b))+b
    return struct.pack('<HHI',t['width'],t['height'],t['combo'])+vec(t['pixels'],4)+s(t['name'])+s(t['page'])+t['flag']

def used_indices(m, ix):
    lists=[ix[d[3]:d[3]+d[4]] for e in m['effects'] for d in map(DRAW.unpack,e['draws'])]
    ids=np.unique(np.concatenate(lists));return ids[ids!=0xffffffff]

def active_joints(m, ix, vb):
    used=used_indices(m,ix); b=np.frombuffer(vb,'u1').reshape(-1,64)[used]; f=b.copy().view('<f4').reshape(-1,16)
    return set(int(x) for k in range(3) for x in b[f[:,8+k]>0,60+k])

def choose_map(name, supported, low_name):
    if name in supported: return name, name
    if re.match('[LR](index|ring|pinky)[ABC]$',name):
        side=name[0]; part=name[-1]; candidate=side+'middle'+part
        if candidate not in supported: candidate=side+'middleB'
        if candidate in supported: return candidate, candidate
    if re.match('[LR](middle|thumb)C$',name):
        candidate=name[:-1]+'B'; return candidate,candidate
    if name in {'HairBase','HairMid','LearBase','LearMid','RearBase','RearMid','learA','learB','rearA','rearB','Ponytail','jaw','browL','browR','Learbase','Learmid','Rearbase','Rearmid','neck'}:
        return 'head','head'
    if name in {'rearflap1','rearflap2','rearflap3'}: return 'rearflapbase','rearflapbase'
    if name=='holster':return 'hips','hips'
    if name=='chest' and 'realChest' in supported:return 'realChest','chest'
    if name in {'Lball','Rball'} and name[0]+'ankle' in supported:return name[0]+'ankle',name[0]+'ankle'
    if name=='hose':return 'realChest','chest'
    if name in {'Ropea','Ropeb'}:return 'hips','hips'
    if name in {'Knife','Cigar'}:return ('Lhand','Lhand') if name=='Knife' else ('Rhand','Rhand')
    raise AssertionError(('unmapped weighted joint',low_name,name))

def donor_effect(low_name, ei, texture):
    if CINEMATIC_EYES and ei==4 and low_name in {'sig-rider-lod0','bt-sig-lod0'}:
        return 1 if low_name=='sig-rider-lod0' else 2
    if low_name=='sig-rider-lod0':return {0:2,1:0,2:0,3:0,4:None,5:2,6:2,7:2,8:4,9:3}[ei]
    if low_name=='bt-sig-lod0':
        if ei==2:return 0 if texture=='sig-horn' else 1
        return {0:5,1:0,3:1,4:None,5:4,6:3,7:5,8:7,9:6}[ei]
    if low_name=='king-rider-lod0':return 0
    if low_name=='ashelin-lod0':return {0:0,1:1,2:3,3:3,4:2,5:2}[ei]
    if low_name=='jinx-lod0':
        if ei==1:return 5
        if ei==2 or texture in {'jinx-handle','jinx-cigar','jinx-cigarflame','jinx-rope-01','jinx-singlerope'}:return None
        if texture=='jinx-glove':return 2
        if texture in {'jinx-glovepalm','jinx-finger'}:return 3
        if texture in {'jinx-wraps'}:return 4
        return 5
    if low_name=='crimson-guard-lod0':
        if ei in (4,5,6):return None
        # Whole authored material regions must switch together. Nearest-surface
        # triangle labels split the shared body at dark-armor boundaries and
        # leave scattered holes when regular armor effects are disabled.
        if texture in {'cguard1-chestplate','cguard1-backmetal'}:return 4
        if texture in {'cguard1-armshield','cguard1-shouldershield'}:return 5
        if texture in {'cguard1-headshield','cguard-shoemetal','cguard1-scarf'}:return 6
        return 7
    raise AssertionError(low_name)

def native_draw_kept(low_name, ei, texture, draw):
    if CINEMATIC_EYES and low_name in {'sig-rider-lod0','bt-sig-lod0'} and 'eye' in texture:
        return False
    if low_name=='sig-rider-lod0':return ei==1
    if low_name=='bt-sig-lod0':return ei==2
    if low_name=='king-rider-lod0':return False
    if low_name=='ashelin-lod0':return False
    if low_name=='jinx-lod0':return ei in (0,1)
    if low_name=='crimson-guard-lod0':return ei in (0,1,2,3)
    raise AssertionError(low_name)

def filter_strip(indices, excluded):
    """Keep OpenGL strip winding, with restart after each retained triangle."""
    out=[]; kept=removed=0;window=[];toggle=False
    for value in indices:
        v=int(value)
        if v==0xffffffff:window=[];toggle=False;continue
        window.append(v)
        if len(window)<3:continue
        a,b,c=window[-3:]
        tri=(b,a,c) if toggle else (a,b,c);toggle=not toggle
        if len(set(tri))<3:continue
        if any(excluded[t] for t in tri):removed+=1;continue
        out.extend((*tri,0xffffffff));kept+=1
    return np.array(out,dtype='<u4'),kept,removed

def triangles(indices):
    result=[];window=[];toggle=False
    for value in indices:
        v=int(value)
        if v==0xffffffff:window=[];toggle=False;continue
        window.append(v)
        if len(window)<3:continue
        a,b,c=window[-3:];tri=(b,a,c) if toggle else (a,b,c);toggle=not toggle
        if len(set(tri))==3:result.append(tri)
    return np.array(result,dtype='<u4').reshape(-1,3)

def jinx_anatomical_transform(target, anchor, ls, hs, torso=False):
    """Align anatomical segment directions, avoiding incompatible joint roll.

    Jinx's native main/chest local frames are rolled 180 degrees relative to
    cinema, despite both torsos standing upright. Direct frame multiplication
    inverts the torso. Limb direction alignment uses actual joint centers.
    """
    lp={n:np.linalg.inv(j['inv_bind'])[:3,3] for n,j in ls.items()}
    hp={n:np.linalg.inv(j['inv_bind'])[:3,3] for n,j in hs.items()}
    next_name={}
    if torso:
        next_name.update(chest='neck',neck='head')
        if target=='head':
            return np.linalg.inv(ls[target]['inv_bind'])@hs[anchor]['inv_bind']
    for side in 'LR':
        next_name.update({side+'shoulder':side+'elbow',side+'elbow':side+'hand',side+'hand':side+'middleA',side+'middleA':side+'middleB',side+'thumbA':side+'thumbB',side+'thigh':side+'knee',side+'knee':side+'ankle',side+'ankle':side+'ball'})
    rotation=np.eye(3)
    if target in next_name:
        end=next_name[target];a=hp[end]-hp[anchor];b=lp[end]-lp[target]
    elif target.endswith('middleB') or target.endswith('thumbB'):
        parent=target[:-1]+'A';a=hp[anchor]-hp[parent];b=lp[target]-lp[parent]
    else:a=b=None
    if a is not None:
        a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);cross=np.cross(a,b);c=float(a@b)
        assert c>-0.9999,(target,'opposed anatomical direction')
        k=np.array([[0,-cross[2],cross[1]],[cross[2],0,-cross[0]],[-cross[1],cross[0],0]])
        rotation=np.eye(3)+k+k@k/(1+c)
    out=np.eye(4);out[:3,:3]=rotation;out[:3,3]=lp[target]-rotation@hp[anchor]
    return out


def torn_hand_transform(target, anchor, ls, hs):
    """Fit palm roll and phalange length before applying the gameplay rig."""
    side=target[0]
    def centers(s):return {n:np.linalg.inv(j['inv_bind'])[:3,3] for n,j in s.items()}
    lp,hp=centers(ls),centers(hs)
    def palm(p):
        along=p[side+'middleA']-p[side+'hand'];along/=np.linalg.norm(along)
        across=p[side+'thumbA']-p[side+'hand'];across-=along*(along@across);across/=np.linalg.norm(across)
        return np.column_stack((along,across,np.cross(along,across)))
    rotation=palm(lp)@palm(hp).T
    if target.endswith('hand'):start,end=side+'hand',side+'middleA'
    elif 'middle' in target:start,end=side+'middleA',side+'middleB'
    else:start,end=side+'thumbA',side+'thumbB'
    a=hp[end]-hp[start];b=lp[end]-lp[start]
    scale=np.linalg.norm(b)/np.linalg.norm(a);a/=np.linalg.norm(a);b/=np.linalg.norm(b)
    if target.endswith('middleB'):scale=1.0
    aligned=rotation@a;cross=np.cross(aligned,b);c=float(aligned@b)
    if c < -0.9999:raise ValueError('Opposed Torn hand segment')
    k=np.array([[0,-cross[2],cross[1]],[cross[2],0,-cross[0]],[-cross[1],cross[0],0]])
    rotation=(np.eye(3)+k+k@k/(1+c))@rotation
    linear=rotation@(np.eye(3)+(scale-1)*np.outer(a,a))
    out=np.eye(4);out[:3,:3]=linear;out[:3,3]=lp[target]-linear@hp[anchor]
    return out


def fit_torn_hand_weights(source, original_joints, hs, ls, lm, li, lv):
    """Transfer the native knuckle bend field onto the fitted cinematic hands."""
    from scipy.spatial import cKDTree
    dst=source.view('<f4').reshape(-1,16)
    native=np.frombuffer(lv,'<f4').reshape(-1,16)
    nb=np.frombuffer(lv,'u1').reshape(-1,64)
    faces=np.concatenate([triangles(li[d[3]:d[3]+d[4]]) for e in lm['effects'] for d in map(DRAW.unpack,e['draws'])])
    native_used=np.unique(faces)
    report={}
    for side in 'LR':
        donor_ids=[j['id'] for n,j in hs.items() if re.fullmatch(side+'(hand|thumb[ABC]|index[ABC]|middle[ABC]|ring[ABC]|pinky[ABC])',n)]
        native_ids=[j['id'] for n,j in ls.items() if re.fullmatch(side+'(hand|thumb[AB]|middle[AB])',n)]
        influence=np.clip(np.sum(dst[:,8:11]*np.isin(original_joints,donor_ids),axis=1),0,1)
        rows=np.flatnonzero(influence>0)
        # The gameplay hand is already curled in its bind mesh. The cinematic
        # hand is straight there, so joint animation alone cannot supply this
        # rest curvature. Fit an arc to the native distal phalange surface.
        centers={n:np.linalg.inv(j['inv_bind'])[:3,3] for n,j in ls.items()}
        origin=centers[side+'middleB'];along=origin-centers[side+'middleA'];along/=np.linalg.norm(along)
        across=centers[side+'thumbA']-centers[side+'hand'];across-=along*(across@along);across/=np.linalg.norm(across)
        inward=np.cross(along,across)
        if side=='R':inward=-inward
        field=native[native_used,:3]-origin;s=field@along;t=field@inward
        distal=np.sum(native[native_used,8:11]*(nb[native_used,60:63]==ls[side+'middleB']['id']),axis=1)>.2
        fit=distal&(s>100)&(t>100)
        if np.count_nonzero(fit)<6:raise ValueError('Missing native Torn finger curvature')
        radius=float(np.sum(t[fit]*(s[fit]**2+t[fit]**2))/(2*np.sum(t[fit]**2)))
        if not 200<radius<1500:raise ValueError('Unexpected Torn finger curvature')
        field=dst[rows,:3]-origin;s=field@along;t=field@inward
        bend=s>0;rr=rows[bend];angle=s[bend]/radius
        if np.max(angle)>2.5:raise ValueError('Excessive Torn finger curl')
        if np.any(radius-t[bend]<=1):raise ValueError('Singular Torn finger fit')
        new_s=(radius-t[bend])*np.sin(angle);new_t=radius-(radius-t[bend])*np.cos(angle)
        dst[rr,:3]+=(new_s-s[bend])[:,None]*along+(new_t-t[bend])[:,None]*inward
        normals=dst[rr,4:7].copy();ns=(normals@along)/(1-t[bend]/radius);nt=normals@inward
        dst[rr,4:7]=normals-(normals@along)[:,None]*along-nt[:,None]*inward+(ns*np.cos(angle)-nt*np.sin(angle))[:,None]*along+(ns*np.sin(angle)+nt*np.cos(angle))[:,None]*inward
        dst[rr,4:7]/=np.linalg.norm(dst[rr,4:7],axis=1)[:,None]
        nw=np.sum(native[:,8:11]*np.isin(nb[:,60:63],native_ids),axis=1)
        selected=faces[np.all(nw[faces]>0.01,axis=1)]
        tri=native[selected,:3].astype(float)
        _,near=cKDTree(tri.mean(1)).query(dst[rows,:3],k=min(32,len(tri)))
        t=tri[near];p=dst[rows,None,:3]
        a,b,c=t[:,:,0],t[:,:,1],t[:,:,2];ab=b-a;ac=c-a;ap=p-a
        dot=lambda x,y:np.sum(x*y,axis=-1)
        aa,bb,cc=dot(ab,ab),dot(ab,ac),dot(ac,ac);det=aa*cc-bb*bb
        u=np.divide(cc*dot(ap,ab)-bb*dot(ap,ac),det,out=np.zeros_like(det),where=abs(det)>1e-12)
        v=np.divide(aa*dot(ap,ac)-bb*dot(ap,ab),det,out=np.zeros_like(det),where=abs(det)>1e-12)
        bary=np.stack([1-u-v,u,v],axis=-1);q=a+u[:,:,None]*ab+v[:,:,None]*ac
        dist=np.where((bary>=0).all(-1)&(abs(det)>1e-12),dot(q-p,q-p),np.inf)
        for x,y,ix,iy in [(a,b,0,1),(a,c,0,2),(b,c,1,2)]:
            edge=y-x;den=dot(edge,edge);f=np.clip(np.divide(dot(p-x,edge),den,out=np.zeros_like(den),where=den>1e-12),0,1)
            q=x+f[:,:,None]*edge;d=dot(q-p,q-p);better=d<dist
            edge_bary=np.zeros_like(bary);edge_bary[:,:,ix]=1-f;edge_bary[:,:,iy]=f
            bary=np.where(better[:,:,None],edge_bary,bary);dist=np.minimum(dist,d)
        choice=dist.argmin(1);verts=selected[near[np.arange(len(rows)),choice]];bw=bary[np.arange(len(rows)),choice]
        for index,row in enumerate(rows):
            combined={}
            for j,w in zip(source[row,60:63],dst[row,8:11]):combined[int(j)]=combined.get(int(j),0)+float(w)*(1-influence[row])
            for vi,factor in zip(verts[index],bw[index]):
                for j,w in zip(nb[vi,60:63],native[vi,8:11]):combined[int(j)]=combined.get(int(j),0)+float(w)*factor*influence[row]
            chosen=sorted(combined.items(),key=lambda x:x[1],reverse=True)[:3]
            total=sum(w for j,w in chosen);source[row,60:63]=0;dst[row,8:11]=0
            for k,(j,w) in enumerate(chosen):source[row,60+k]=j;dst[row,8+k]=w/total
        report[side]=dict(vertices=len(rows),curvedVertices=len(rr),nativeRestRadius=radius,
                          maxCurlRadians=float(np.max(angle)),weightSource='native hand surface')
    if not np.allclose(dst[:,8:11].sum(1),1,atol=1e-5):raise ValueError('Invalid Torn hand weights')
    return report

def fit_jinx_eyes(raw, native):
    """Sample the native iris using the cinematic eyes' authored UV projection.

    Native eyeballs are farther apart and shallower. Nearest-surface transfer
    from those eyeballs shifts both pupils outward and clamps their lower UVs.
    Preserve each cinematic eye's own projection and fit the static iris
    within the cinematic sclera. Clamp to the white texture border
    so repeating UVs cannot introduce a second iris.
    """
    name='jinx-lod0'
    _,indices,vertex_start,vb=buffers(raw,name)
    tx,_=textures(raw)
    native_tx,_=textures(native)
    eye_texture=next(t for t in tx if t['name']=='citn-allleye')
    assert texture_bytes(eye_texture)==texture_bytes(next(t for t in native_tx if t['name']=='citn-allleye'))
    current=np.frombuffer(vb,'<f4').reshape(-1,16).copy()
    ids=np.unique(np.concatenate([indices[d[3]:d[3]+d[4]]
        for e in model(raw,name)['effects'] for d in map(DRAW.unpack,e['draws'])
        if tx[d[1]]['name']=='citn-allleye']))
    ids=ids[ids!=0xffffffff]
    assert len(ids)==94, 'Unexpected cinematic eye geometry'
    assert np.count_nonzero(current[ids,0]>0)==47
    assert np.count_nonzero(current[ids,0]<0)==47
    assert np.isfinite(current[ids,12:14]).all()
    current[ids,12:14]=np.clip((current[ids,12:14]-0.5)*1.5+0.5,0.015625,0.984375)
    before=np.frombuffer(vb,'u1').reshape(-1,64)
    after=current.view('u1').reshape(-1,64)
    assert np.array_equal(before[:,:48],after[:,:48])
    assert np.array_equal(before[:,56:],after[:,56:])
    unchanged=np.ones(len(current),dtype=bool);unchanged[ids]=False
    assert np.array_equal(before[unchanged],after[unchanged])
    vertex_start+=8
    result=raw[:vertex_start]+current.tobytes()+raw[vertex_start+len(vb):]
    _,checked,_,checked_vb=buffers(result,name)
    assert np.array_equal(checked,indices) and checked_vb==current.tobytes()
    return result,dict(vertices=len(ids),projection="cinematic-authored",irisUVScale=1.5,
                       nativeTextureUnchanged=True,onlyEyeUVsChanged=True)


def build_one(low_name, high_name, *, source_dir=None, submit_all_joints=False,
              excluded_joint_names=(), native_prop_joint_names=(), anatomical=False,
              static_eye_texture=None, eye_slot_map=None, static_eye_uv_scale=1.0,
              static_eye_pupil=False):
    lc,hc=CAT[low_name],CAT[high_name]
    hr=decompress(ROOT/hc['files'][0]);hm=model(hr,high_name)
    _,hi,_,hv=buffers(hr,high_name);ht,_=textures(hr)
    hs=skeleton(high_name.removesuffix('-lod0'),hc['joints'])
    ls=skeleton(low_name.removesuffix('-lod0'),lc['joints'])
    reports=[]
    for filename in lc['files']:
        original=(Path(source_dir) if source_dir is not None else ROOT)/filename;source_digest=hashlib.sha256(original.read_bytes()).hexdigest();lr=decompress(original);lm=model(lr,low_name)
        istart,li,_,lv=buffers(lr,low_name);lt,tend=textures(lr)
        supported_ids=active_joints(lm,li,lv)
        if submit_all_joints:
            supported_ids={item['id'] for item in ls.values()}
        supported={name for name,item in ls.items() if item['id'] in supported_ids}
        hb=np.frombuffer(hv,'u1').reshape(-1,64);hf=np.frombuffer(hv,'<f4').reshape(-1,16)
        # Staff is a cinematic prop. Removing every staff-weighted triangle
        # prevents a guessed attachment from entering the passenger model.
        staff_ids={hs[n]['id'] for n in hs if n.startswith('staff')}
        excluded=np.zeros(len(hb),bool)
        extra_excluded={hs[n]['id'] for n in excluded_joint_names}
        for k in range(3):excluded|=np.isin(hb[:,60+k],list(extra_excluded))&(hf[:,8+k]>1e-6)
        if low_name=='king-rider-lod0':
            for k in range(3):excluded|=np.isin(hb[:,60+k],list(staff_ids))&(hf[:,8+k]>1e-6)
        selections=[];removed_tris=0
        for ei,e in enumerate(hm['effects']):
            for packed in e['draws']:
                d=list(DRAW.unpack(packed));te=donor_effect(low_name,ei,ht[d[1]]['name'])
                if te is None:continue
                ix=hi[d[3]:d[3]+d[4]]
                if excluded.any():
                    ix,n,removed=filter_strip(ix,excluded);removed_tris+=removed
                    if not n:continue
                    d[4]=len(ix);d[5]=n
                selections.append((te,d,ix))
        used=np.unique(np.concatenate([ix[ix!=0xffffffff] for _,_,ix in selections]))
        source=hb[used].copy();sf=source.view('<f4').reshape(-1,16)
        weights=sf[:,8:11].copy();beforepos=sf[:,:3].copy();beforenorm=sf[:,4:7].copy();original_joints=source[:,60:63].copy()
        assert np.allclose(weights.sum(1),1,atol=1e-5)
        spos=np.c_[beforepos,np.ones(len(source))];nresult=np.zeros_like(beforenorm,dtype=float);presult=np.zeros_like(beforepos,dtype=float)
        byid={v['id']:k for k,v in hs.items()};mapping={};transforms={}
        for k in range(3):
            for joint in np.unique(original_joints[:,k]):
                active=(original_joints[:,k]==joint)&(weights[:,k]>0)
                if not active.any():continue
                name=byid[int(joint)];target,anchor=choose_map(name,supported,low_name)
                assert target in supported and anchor in hs
                matrix=np.linalg.inv(ls[target]['inv_bind'])@hs[anchor]['inv_bind']
                if low_name=='jinx-lod0' or anatomical:matrix=jinx_anatomical_transform(target,anchor,ls,hs,torso=low_name=='bt-torn-lod0')
                if low_name=='bt-torn-lod0' and re.fullmatch('[LR](hand|middle[AB]|thumb[AB])',target):
                    matrix=torn_hand_transform(target,anchor,ls,hs)
                normal_matrix=np.linalg.inv(matrix[:3,:3]).T
                presult[active]+=(spos[active]@matrix.T)[:,:3]*weights[active,k,None]
                nresult[active]+=(beforenorm[active]@normal_matrix.T)*weights[active,k,None]
                source[active,60+k]=ls[target]['id']
                mapping[name]=target;transforms[name]=matrix.tolist()
        # Preserve source normals under exact identity transforms; changed bind
        # frames use normalized inverse-transpose directions.
        lengths=np.linalg.norm(nresult,axis=1);assert np.all(lengths>1e-7)
        sf[:,:3]=presult;sf[:,4:7]=nresult/lengths[:,None]
        torso_fit=None
        if low_name=='bt-torn-lod0':
            # The cinematic upper body is taller above the same joint pivots.
            # Fit one continuous vertical field to matching garment/face
            # landmarks, blended by torso skin weights to preserve limb fit.
            def material_rows(merc, indices, tex, names):
                lists=[indices[d[3]:d[3]+d[4]] for e in merc['effects']
                       for d in map(DRAW.unpack,e['draws']) if tex[d[1]]['name'] in names]
                ids=np.unique(np.concatenate(lists));return ids[ids!=0xffffffff]
            native=np.frombuffer(lv,'<f4').reshape(-1,16)
            face_names={'torn-face','torn-face-right'}
            face_h=np.searchsorted(used,material_rows(hm,hi,ht,face_names))
            vest_h=np.searchsorted(used,material_rows(hm,hi,ht,{'torn-vest'}))
            face_l=material_rows(lm,li,lt,face_names)
            vest_l=material_rows(lm,li,lt,{'torn-vest'})
            base=float(np.linalg.inv(ls['main']['inv_bind'])[1,3])
            x=np.array([0,base,sf[face_h,1].min(),sf[vest_h,1].max(),sf[face_h,1].max()])
            y=np.array([0,base,native[face_l,1].min(),native[vest_l,1].max(),native[face_l,1].max()])
            assert (np.diff(x)>0).all() and (np.diff(y)>0).all()
            slopes=np.diff(y)/np.diff(x)
            assert ((slopes>0.75)&(slopes<1.25)).all(), 'Unexpected Torn torso proportions'
            bands=np.clip(np.searchsorted(x,sf[:,1],side='right')-1,0,len(slopes)-1)
            sf[:,1]=y[bands]+(sf[:,1]-x[bands])*slopes[bands]
            sf[:,5]/=slopes[bands]
            sf[:,4:7]/=np.linalg.norm(sf[:,4:7],axis=1)[:,None]
            # A torso height fit must not move the hand/finger bind positions
            # away from the gameplay control grips. Blend by torso influence
            # so the shoulder seams remain continuous.
            central={hs[n]['id'] for n in ('main','chest','neck','head')}
            fit_weight=np.sum(weights*np.isin(original_joints,list(central)),axis=1)
            sf[:,:3]=presult+(sf[:,:3]-presult)*fit_weight[:,None]
            unfit_norm=nresult/lengths[:,None]
            sf[:,4:7]=unfit_norm+(sf[:,4:7]-unfit_norm)*fit_weight[:,None]
            sf[:,4:7]/=np.linalg.norm(sf[:,4:7],axis=1)[:,None]
            torso_fit=dict(sourceY=x.tolist(),targetY=y.tolist(),slopes=slopes.tolist(),
                           landmarks=['ground','main joint','face bottom','vest top','face top'],
                           continuous=True,normalsInverseTranspose=True)
            # The cinematic sword has its own animated joint. Its bind pose
            # lies across the shoulders, so merely attaching it to chest is
            # wrong. Fit its handle to the native chest-mounted handle using
            # locally extracted geometry, then keep the whole sword rigid.
            native_bytes=np.frombuffer(lv,'u1').reshape(-1,64)
            lh=material_rows(lm,li,lt,{'torn-handle-01'})
            lh=lh[np.any((native_bytes[lh,60:63]==ls['chest']['id'])&(native[lh,8:11]>0),axis=1)]
            hh=material_rows(hm,hi,ht,{'torn-handle-01'})
            hh=hh[np.any((hb[hh,60:63]==hs['sword']['id'])&(hf[hh,8:11]>0),axis=1)]
            def handle_axis(points):
                center=(points.min(0)+points.max(0))/2
                axis=np.linalg.svd(points-points.mean(0),full_matrices=False)[2][0]
                if axis[1]<0:axis=-axis
                return center,axis
            hc0,a=handle_axis(hf[hh,:3]);lc0,z=handle_axis(native[lh,:3])
            cross=np.cross(a,z);c=float(a@z);assert c>-0.99
            k=np.array([[0,-cross[2],cross[1]],[cross[2],0,-cross[0]],[-cross[1],cross[0],0]])
            rotation=np.eye(3)+k+k@k/(1+c)
            sword_rows=np.any((original_joints==hs['sword']['id'])&(weights>0),axis=1)
            assert np.allclose(weights[sword_rows].max(1),1)
            sf[sword_rows,:3]=(beforepos[sword_rows]-hc0)@rotation.T+lc0
            sf[sword_rows,4:7]=beforenorm[sword_rows]@rotation.T
            torso_fit['swordAttachment']='rigid fit to native chest-mounted handle'
        hand_fit=fit_torn_hand_weights(source,original_joints,hs,ls,lm,li,lv) if low_name=='bt-torn-lod0' else None
        assert np.all(np.isfinite(sf[:,:3])) and np.all(np.isfinite(sf[:,4:7]))
        # Catch accidental matrix-ID remapping collisions or unit errors before
        # a staged asset can be accepted for a runtime test.
        native_used=used_indices(lm,li);native_pos=np.frombuffer(lv,'<f4').reshape(-1,16)[native_used,:3]
        assert np.ptp(sf[:,1])<np.ptp(native_pos[:,1])*1.2,(low_name,'unexpected height')
        assert np.max(np.abs(sf[:,:3]))<np.max(np.abs(native_pos))*1.5,(low_name,'unexpected bound')
        assert all(int(j) in supported_ids for k in range(3) for j in source[sf[:,8+k]>0,60+k])
        region_report=None
        if low_name=='crimson-guard-lod0':
            region_report={}
            for te,d,_ in selections:
                texture=ht[d[1]]['name']
                assert texture not in region_report or region_report[texture]==te
                region_report[texture]=te
        new=copy.deepcopy(lm)
        for e in new['effects']:
            assert e['mod']==EMPTY_MOD
            e['draws']=[]
        native_kept=0;native_app=[];native_cursor=len(li)
        lb=np.frombuffer(lv,'u1').reshape(-1,64)
        lf=np.frombuffer(lv,'<f4').reshape(-1,16)
        prop_vertices=np.zeros(len(lb),bool)
        prop_ids={ls[n]['id'] for n in native_prop_joint_names}
        for k in range(3):prop_vertices|=np.isin(lb[:,60+k],list(prop_ids))&(lf[:,8+k]>1e-6)
        for ei,e in enumerate(lm['effects']):
            for packed in e['draws']:
                d=DRAW.unpack(packed)
                if native_draw_kept(low_name,ei,lt[d[1]]['name'],d):
                    new['effects'][ei]['draws'].append(packed);native_kept+=d[5]
                elif prop_ids:
                    ix=li[d[3]:d[3]+d[4]]
                    tris=ix.reshape(-1,3) if d[6] else triangles(ix)
                    tris=tris[np.any(prop_vertices[tris],axis=1)]
                    if len(tris):
                        kept=tris.astype('<u4').ravel();nd=list(d)
                        nd[3]=native_cursor;nd[4]=len(kept);nd[5]=len(tris);nd[6]=1
                        native_cursor+=len(kept);native_app.append(kept)
                        new['effects'][ei]['draws'].append(DRAW.pack(*nd));native_kept+=len(tris)
        txmap={};added=[];existing={texture_bytes(t):i for i,t in enumerate(lt)}
        for i in sorted({d[1] for _,d,_ in selections}):
            if static_eye_texture and ht[i]['name'].startswith('programmer_eye_'):
                native_index=next((j for j,t in enumerate(lt) if t['name']==static_eye_texture),None)
                if native_index is not None:
                    txmap[i]=native_index
                else:
                    eye=next(t for t in ht if t['name']==static_eye_texture)
                    if static_eye_pupil:
                        from static_eyes import compose
                        global_textures,_=textures(decompress(ROOT/'GAME.fr3'))
                        # Torn's Secrets idle uses these iris/pupil scales.
                        # The generic no-animation defaults obscure his blue iris.
                        args = dict(name='cinematic-torn-neutral-eye', combo=0xfffe0003,
                                    iris_scale=0.890625, pupil_scale=0.390625) if low_name=='bt-torn-lod0' else {}
                        eye=compose(eye,next(t for t in global_textures if t['name']=='autoeye-pupil'),**args)
                        assert not any(t['combo']==eye['combo'] for t in lt+ht+global_textures)
                    key=texture_bytes(eye);index=existing.get(key)
                    if index is None:
                        index=len(lt)+len(added);added.append(eye);existing[key]=index
                    txmap[i]=index
                continue
            if not CINEMATIC_EYES and low_name=='jinx-lod0' and ht[i]['name'].startswith('programmer_eye_'):
                txmap[i]=next(j for j,t in enumerate(lt) if t['name']=='citn-allleye')
                continue
            key=texture_bytes(ht[i]);index=existing.get(key)
            if index is None:index=len(lt)+len(added);added.append(ht[i]);existing[key]=index
            txmap[i]=index
        vmap=np.full(len(hb),0xffffffff,dtype='<u4');vmap[used]=np.arange(len(used),dtype='<u4')+len(lv)//64
        if static_eye_texture and static_eye_uv_scale != 1.0:
            eye_ids=np.unique(np.concatenate([ix[ix!=0xffffffff] for _,d,ix in selections
                if ht[d[1]]['name'].startswith('programmer_eye_')]))
            eye_rows=vmap[eye_ids]-len(lv)//64
            tex=(lt+added)[txmap[next(d[1] for _,d,_ in selections
                if ht[d[1]]['name'].startswith('programmer_eye_'))]]
            border=np.array([0.5/tex['width'],0.5/tex['height']])
            sf[eye_rows,12:14]=np.clip((sf[eye_rows,12:14]-0.5)*static_eye_uv_scale+0.5,border,1-border)
        app=list(native_app);nextidx=native_cursor
        for ei,d,ix in selections:
            mapped=ix.copy();valid=ix!=0xffffffff;mapped[valid]=vmap[ix[valid]]
            assert np.all(mapped[valid]!=0xffffffff)
            app.append(mapped);d[1]=txmap[d[1]];d[2]=(eye_slot_map or {}).get(d[2],255);d[3]=nextidx;nextidx+=len(mapped)
            new['effects'][ei]['draws'].append(DRAW.pack(*d))
        assert all(e['draws'] for e in new['effects'])
        tail=bytearray(new['tail']);struct.pack_into('<I',tail,0,sum(len(e['draws']) for e in new['effects']));new['tail']=bytes(tail)
        allix=np.concatenate([li]+app);allvb=lv+source.tobytes()
        fixed=lr[:istart]+vec(allix.tobytes(),4)+vec(allvb,64)+lr[-2:]
        fixed=fixed[:lm['start']]+serialize(new)+fixed[lm['end']:]
        r=Reader(lr,2);r.take(r.num('Q'));tstart=r.pos
        fixed=fixed[:tstart]+struct.pack('<Q',len(lt)+len(added))+b''.join(texture_bytes(t) for t in lt+added)+fixed[tend:]
        checked=model(fixed,low_name);_,ci,_,cv=buffers(fixed,low_name);ct,_=textures(fixed)
        assert serialize(checked)==serialize(new) and cv==allvb and np.array_equal(ci,allix)
        assert cv[:len(lv)]==lv and np.array_equal(ci[:len(li)],li)
        assert [texture_bytes(t) for t in ct[:len(lt)]]==[texture_bytes(t) for t in lt]
        preserved=0
        for name,entries in FULLCAT.items():
            if name!=low_name and any(x['file']==filename for x in entries):
                assert serialize(model(lr,name))==serialize(model(fixed,name)),name;preserved+=1
        eye_report=None
        if low_name=='jinx-lod0' and not CINEMATIC_EYES:
            fixed,eye_report=fit_jinx_eyes(fixed,lr)
        OUT.mkdir(parents=True,exist_ok=True);out=OUT/filename;compress(out,fixed);assert decompress(out)==fixed
        report=dict(file=filename,gameplay=low_name,donor=high_name,triangles=sum(DRAW.unpack(d)[5] for e in new['effects'] for d in e['draws']),native_preserved_triangles=native_kept,removed_staff_triangles=removed_tris,effects=len(new['effects']),native_effect_tails_preserved=all(a['tail']==b['tail'] for a,b in zip(lm['effects'],new['effects'])),matrix_ids_within_original_submission=True,joint_mapping=mapping,bind_transforms=transforms,body_extent_m=(np.ptp(sf[:,:3],axis=0)/4096).tolist(),source_sha256=source_digest,output_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),output_bytes=out.stat().st_size,other_models_preserved=preserved,guard_material_effect_assignment=region_report,status='staged; requires runtime animation, attachment, eye and material checks')
        report['eye_surface_transfer']=eye_report
        report['torso_fit']=torso_fit
        report['hand_fit']=hand_fit
        report['static_eye_layers']=['iris','pupil'] if static_eye_pupil else None
        report['requires_full_joint_submission']=submit_all_joints
        report['matrix_ids_within_original_submission']=not submit_all_joints
        reports.append(report)
        print(filename,low_name,report['triangles'],'triangles',native_kept,'native preserved triangles',removed_tris,'staff triangles removed')
    return reports

def build(data_dir=None,output_dir=None,metadata_path=None,report_path=None,animated_eyes=False):
    global CINEMATIC_EYES
    CINEMATIC_EYES = animated_eyes
    if not CAT or any(x is not None for x in [data_dir,output_dir,metadata_path,report_path]):
        configure(data_dir,output_dir,metadata_path,report_path)
    report=[]
    for low,high in [('king-rider-lod0','king-highres-lod0'),('ashelin-lod0','ashelin-highres-lod0'),('sig-rider-lod0','sig-highres-lod0'),('bt-sig-lod0','sig-highres-lod0'),('jinx-lod0','jinx-highres-lod0'),('crimson-guard-lod0','crimson-guard-highres-lod0')]:
        options={}
        if low=='king-rider-lod0':options['eye_slot_map']={120:0,121:1}
        if low=='ashelin-lod0':options.update(static_eye_texture='ashelin-lo-eye',static_eye_uv_scale=1.5)
        if CINEMATIC_EYES and low in {'ashelin-lod0','jinx-lod0','sig-rider-lod0','bt-sig-lod0'}:
            options={'eye_slot_map':{i:i%2 for i in range(255)}}
        report.extend(build_one(low,high,**options))
    REPORT_PATH.parent.mkdir(parents=True,exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':build()
