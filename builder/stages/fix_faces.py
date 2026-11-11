"""Retarget native gameplay deformations onto cinematic surfaces and restore live eyes.

Version-specific FR3 adapter: preserves the imported neutral vertices, skeleton,
armor effects, UVs and textures. Uses native gameplay weight channels, not guessed
cinematic expression indices. Surface correspondence is restricted by material part.
"""
import copy,json,struct
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import lsq_linear
from fr3_models import BASE,Reader,DRAW,decompress,compress,model,serialize
from inspect_faces import glb,buffers

JAK_PARTS={1:[46],2:[45],6:[26],20:[22],21:[23],24:[47],25:[27],26:[29,32],27:[28],28:[31],39:[25]}
DAX_PARTS={4:[5,6],5:[4,5],6:[4,5,6,7],13:[1],14:[2],15:[8,9]}

def closest_surface(points,triangles):
    """Exact closest point amongst 48 nearest triangle centroids, all three edges included."""
    k=min(48,len(triangles));_,cand=cKDTree(triangles.mean(1)).query(points,k=k)
    if k==1:cand=cand[:,None]
    t=triangles[cand];p=points[:,None,:];a=t[:,:,0];u=t[:,:,1]-a;v=t[:,:,2]-a;q=p-a
    dot=lambda x,y:np.sum(x*y,axis=-1)
    uu=dot(u,u);uv=dot(u,v);vv=dot(v,v);qu=dot(q,u);qv=dot(q,v)
    den=uu*vv-uv*uv;safe=np.where(abs(den)>1e-20,den,1)
    b=(qu*vv-qv*uv)/safe;c=(qv*uu-qu*uv)/safe
    bary=np.stack([1-b-c,b,c],-1)
    valid=(np.min(bary,axis=-1)>=0)&(abs(den)>1e-20)
    projected=np.einsum('nki,nkij->nkj',bary,t)
    best=np.where(valid,dot(projected-p,projected-p),np.inf)
    for i,j in [(0,1),(1,2),(2,0)]:
        edge=t[:,:,j]-t[:,:,i];length=dot(edge,edge)
        alpha=np.clip(dot(p-t[:,:,i],edge)/np.maximum(length,1e-20),0,1)
        edgepoint=t[:,:,i]+alpha[:,:,None]*edge;dist=dot(edgepoint-p,edgepoint-p)
        eb=np.zeros_like(bary);eb[:,:,i]=1-alpha;eb[:,:,j]=alpha
        improve=dist<best;bary=np.where(improve[:,:,None],eb,bary);best=np.minimum(best,dist)
    pick=best.argmin(1);row=np.arange(len(points))
    return cand[row,pick],bary[row,pick],np.sqrt(best[row,pick])

def vec(b,size):return struct.pack('<Q',len(b)//size)+b
def draw_list(draws):return struct.pack('<Q',len(draws))+b''.join(draws)

def original_eyes(high):
    # The compact source GLB preserves primitive order; image names preserve eye side.
    d,_=glb(BASE/'models'/f'{high}.glb');result={}
    for i,p in enumerate(d['meshes'][0]['primitives']):
        mat=d['materials'][p['material']];ti=mat.get('pbrMetallicRoughness',{}).get('baseColorTexture',{}).get('index')
        if ti is not None:
            name=d['images'][d['textures'][ti]['source']]['name']
            if name in ('programmer_eye_left','programmer_eye_right'):result[i]=int(name.endswith('right'))
    assert len(result)==2
    return result

def retarget(low,high,parts):
    ld,la=glb(BASE/'models'/f'{low}.glb');hd,ha=glb(BASE/'prototype/merc_replacements'/f'{low}.glb')
    lp=ld['meshes'][0]['primitives'];hp=hd['meshes'][0]['primitives'];count=len(lp[0]['targets']);out={};report=[]
    for pi,source_parts in parts.items():
        pos=[];deltas=[];normals=[]
        for si in source_parts:
            p=lp[si];ids=la(p['indices']).reshape(-1,3)
            pos.append(la(p['attributes']['POSITION'])[ids])
            deltas.append(np.stack([la(t['POSITION'])[ids] for t in p['targets']]))
            normals.append(np.stack([la(t['NORMAL'])[ids] for t in p['targets']]))
        triangles=np.concatenate(pos);ds=np.concatenate(deltas,axis=1);ns=np.concatenate(normals,axis=1)
        p=hp[pi];positions=ha(p['attributes']['POSITION']);nrms=ha(p['attributes']['NORMAL'])
        ti,bary,dist=closest_surface(positions,triangles)
        dp=np.einsum('tnij,ni->tnj',ds[:,ti],bary)
        dn=np.einsum('tnij,ni->tnj',ns[:,ti],bary)
        dn-=np.einsum('tnj,nj->tn',dn,nrms)[:,:,None]*nrms[None,:,:]
        out[pi]=(dp,dn)
        used=np.unique(ha(p['indices']))
        report.append({'primitive':pi,'source_primitives':source_parts,'vertices':len(used),'max_surface_distance_m':float(dist[used].max()),'active_channels':np.flatnonzero(np.linalg.norm(dp[:,used],axis=2).max(1)>1e-6).tolist()})
    # Fit gameplay expressions in the native cinematic shape basis. Direct
    # surface transfer is unsuitable inside the higher-detail mouth: upper and
    # lower lips can select different nearby surfaces and tear. A shared linear
    # combination of authored shapes preserves cinematic topology and seams.
    source,sa=glb(BASE/'models'/f'{high}.glb')
    sp=source['meshes'][0]['primitives'];native={}
    faceparts=([24,26,27,28] if low.startswith('jak') else [4,5,6,13,14,15])
    fitparts=([26,27] if low.startswith('jak') else [5,6])
    amat=[];bmat=[]
    for pi in faceparts:
        p=hp[pi];orig=sp[pi];used=np.unique(sa(orig['indices']))
        distance,near=cKDTree(sa(orig['attributes']['POSITION'])[used]).query(ha(p['attributes']['POSITION']))
        assert distance.max()<1e-5
        native[pi]=(np.array([sa(t['POSITION'])[used[near]] for t in orig['targets']]),np.array([sa(t['NORMAL'])[used[near]] for t in orig['targets']]))
        if pi in fitparts:
            # Include only authored facial vertices; fingers share Jak's material.
            moving=np.linalg.norm(native[pi][0],axis=2).max(0)>1e-6
            basis=native[pi][0][:,moving].reshape(len(orig['targets']),-1).T
            desired=out[pi][0][:,moving].reshape(count,-1).T
            amat.append(basis);bmat.append(desired)
    A=np.concatenate(amat);B=np.concatenate(bmat)
    regularizer=0.025
    coeff=np.stack([lsq_linear(np.vstack([A,np.eye(A.shape[1])*regularizer]),np.r_[B[:,i],np.zeros(A.shape[1])],bounds=(0,1.5),tol=1e-9).x for i in range(count)])
    if low == 'daxter-lod0':
        # The closed cinematic mouth has overlapping upper/lower lip surfaces.
        # Nearest-surface transfer confuses these and suppresses the angry jaw.
        # Constrain the authored cinematic basis with corresponding lip landmarks.
        # These are source accessor indices for the supported Jak 3 extraction.
        # One shared basis still moves the lips, mouth interior and teeth together.
        landmarks = [(5, 3697, 2583), (6, 3765, 1715), (5, 3694, 2581),
                     (6, 3775, 2494)]
        hp6 = sp[6]
        assert count == 9 and len(hp6['targets']) == 37
        assert np.allclose(sa(hp6['attributes']['POSITION'])[2583],
                           [0.0, 1.0376226, 0.16812868], atol=1e-6), 'Unsupported Daxter mouth layout'
        assert np.allclose(la(lp[5]['attributes']['POSITION'])[3697],
                           [0.0, 1.0416071, 0.16994642], atol=1e-6), 'Unsupported native Daxter mouth layout'
        rows, targets = [], []
        for part, native_id, cinematic_id in landmarks:
            source_part = lp[part]
            rows.append(np.array([sa(t['POSITION'])[cinematic_id] for t in hp6['targets']]).T)
            target = la(source_part['targets'][0]['POSITION'])[native_id].copy()
            if cinematic_id == 2494:
                # The cinematic closed mouth is narrower. Match the native
                # corner endpoint when angry, without changing the neutral mesh.
                target[0] += (la(source_part['attributes']['POSITION'])[native_id,0]
                              - sa(hp6['attributes']['POSITION'])[cinematic_id,0])
            targets.append(target)
            if cinematic_id == 2494:
                # Match the opposite mouth corner too; use its actual source data.
                source_pos = la(source_part['attributes']['POSITION'])[native_id].copy()
                source_pos[0] *= -1
                native_left = int(np.linalg.norm(la(source_part['attributes']['POSITION'])-source_pos,axis=1).argmin())
                cinematic_pos = sa(hp6['attributes']['POSITION'])[cinematic_id].copy()
                cinematic_pos[0] *= -1
                cinematic_left = int(np.linalg.norm(sa(hp6['attributes']['POSITION'])-cinematic_pos,axis=1).argmin())
                rows.append(np.array([sa(t['POSITION'])[cinematic_left] for t in hp6['targets']]).T)
                target_left = la(source_part['targets'][0]['POSITION'])[native_left].copy()
                target_left[0] += (la(source_part['attributes']['POSITION'])[native_left,0]
                                   - sa(hp6['attributes']['POSITION'])[cinematic_left,0])
                targets.append(target_left)
        mouth_a, mouth_b = np.concatenate(rows), np.concatenate(targets)
        landmark_weight = 64.0
        coeff[0] = lsq_linear(
            np.vstack([A, np.eye(A.shape[1])*regularizer, mouth_a*landmark_weight]),
            np.r_[B[:,0], np.zeros(A.shape[1]), mouth_b*landmark_weight],
            bounds=(0,1.5), tol=1e-9).x
        report.append({'angry_mouth_landmarks': landmarks,
                       'max_landmark_error_m': float(np.abs(mouth_a@coeff[0]-mouth_b).max()),
                       'vertical_landmark_error_m': float(np.abs((mouth_a@coeff[0]-mouth_b).reshape(-1,3)[:,1]).max()),
                       'landmark_weight': landmark_weight})
    if low == 'jakb-c-lod0':
        # Corresponding lower mouth, lower lip and upper lip center points.
        # Surface proximity alone maps the cinematic lip to the wrong vertical
        # position on the gameplay face, attenuating the snarl in channels 0/1.
        landmarks = [(17443,20194),(17401,20170),(17370,20165)]
        source_part, cinematic_part = lp[29], sp[26]
        assert np.allclose(la(source_part['attributes']['POSITION'])[17443],
                           [0,2.344,0.3178],atol=0.0001)
        mouth_a = np.concatenate([np.array([sa(t['POSITION'])[hi]
                                  for t in cinematic_part['targets']]).T
                                  for lo,hi in landmarks])
        errors = {}
        for ch in (0,1):
            mouth_b = np.concatenate([la(source_part['targets'][ch]['POSITION'])[lo]
                                      for lo,hi in landmarks])
            coeff[ch] = lsq_linear(
                np.vstack([A,np.eye(A.shape[1])*regularizer,mouth_a*64.0]),
                np.r_[B[:,ch],np.zeros(A.shape[1]),mouth_b*64.0],
                bounds=(0,1.5),tol=1e-9).x
            errors[str(ch)] = float(np.abs(mouth_a@coeff[ch]-mouth_b).max())
        report.append({'snarl_mouth_landmarks':landmarks,'max_landmark_error_m':errors})
    facechannels=([0,1,2,8,9,10,11,12] if low.startswith('jak') else list(range(count)))
    for pi in faceparts:
        dp,dn=out[pi];npd,nnd=native[pi]
        for ch in facechannels:
            dp[ch]=np.einsum('t,tnj->nj',coeff[ch],npd)
            dn[ch]=np.einsum('t,tnj->nj',coeff[ch],nnd)
    if low.startswith('jak'):
        # Facial seam vertices also occur in separate cinematic material parts.
        # Apply the same snarl displacement on both copies of each seam, as in
        # the reviewed in-game expression repair. Without this, a clean build
        # leaves the adjoining hair/forehead material on its older surface fit.
        points=np.concatenate([ha(hp[pi]['attributes']['POSITION']) for pi in faceparts])
        normals=np.concatenate([ha(hp[pi]['attributes']['NORMAL']) for pi in faceparts])
        face_dp=np.concatenate([out[pi][0] for pi in faceparts],axis=1)
        face_dn=np.concatenate([out[pi][1] for pi in faceparts],axis=1)
        tree=cKDTree(points)
        seams=0
        for pi,(dp,dn) in out.items():
            if pi in faceparts or pi in (1,2):continue
            positions=ha(hp[pi]['attributes']['POSITION'])
            nrms=ha(hp[pi]['attributes']['NORMAL'])
            distances,near=tree.query(positions,k=8)
            for vi in np.flatnonzero(distances[:,0]<1e-5):
                candidates=near[vi][distances[vi]<1e-5]
                best=candidates[np.argmax(normals[candidates]@nrms[vi])]
                for ch in (0,1):
                    dp[ch,vi]=face_dp[ch,best]
                    dn[ch,vi]=face_dn[ch,best]
                seams+=1
        report.append({'snarlSeamVerticesMatched':seams})
        # Jak's cinematic eyeballs are fixed; his authored eyelids surround them.
        # The eye compositor supplies gaze and blink, so low-poly eye deformation
        # must not stretch the cinematic sclera along with gameplay eyebrows.
        for pi in [1,2]:out.pop(pi)
    report.append({'native_cinematic_basis_fit':True,'coefficients':{str(ch):{str(t):round(float(w),6) for t,w in enumerate(coeff[ch]) if w>1e-4} for ch in facechannels},'regularizer':regularizer})
    return hd,ha,out,count,report

def fix_model(raw,name,high,parts,indices,vertices,*,retarget_fn=None,eye_fn=None,native_eye_data=None,eye_slot=0):
    target=model(raw,name);d,a,deformations,count,report=(retarget_fn or retarget)(name,high,parts)
    prims=d['meshes'][0]['primitives'];eyes=(eye_fn or original_eyes)(high)
    # Imported draws are grouped by envmap. Recover their original primitive IDs
    # from first_index order (the importer appends primitives in source order).
    draws=sorted([x for e in target['effects'] for x in e['draws']],key=lambda x:DRAW.unpack(x)[3])
    assert len(draws)==len(prims)
    byfirst={DRAW.unpack(x)[3]:i for i,x in enumerate(draws)}
    if name=='jakb-c-lod0' or native_eye_data is not None:
        # Cinematic sclera are taller and use a different UV projection. Preserve
        # gameplay iris size/location in model space when sampling its live eyes.
        if native_eye_data is None:
            ld,la=glb(BASE/'models'/'jakb-c-lod0.glb');eye_parts={1:45,0:46}
        else:
            ld,la,eye_parts=native_eye_data
        for pi,side in eyes.items():
            oldp=ld['meshes'][0]['primitives'][eye_parts[side]]
            oldids=np.unique(la(oldp['indices']));oldpos=la(oldp['attributes']['POSITION'])[oldids]
            olduv=la(oldp['attributes']['TEXCOORD_0'])[oldids]
            projection=np.linalg.lstsq(np.c_[oldpos[:,:2],np.ones(len(oldpos))],olduv,rcond=None)[0]
            vals=DRAW.unpack(draws[pi]);ids=indices[vals[3]:vals[3]+vals[4]];local=a(prims[pi]['indices']).ravel()
            offset=int(ids[0])-int(local[0]);positions=a(prims[pi]['attributes']['POSITION'])
            uv=np.clip(np.c_[positions[:,:2],np.ones(len(positions))]@projection,0,1)
            for idx in np.unique(ids):struct.pack_into('<2f',vertices,int(idx)*64+48,*uv[int(idx)-offset])
    append=[];next_index=len(indices);modified_total=0
    for effect in target['effects']:
        moddraw=[];fixdraw=[];modvertices=[];floatdata=[];intdata=[];newdraws=[]
        for draw in effect['draws']:
            values=list(DRAW.unpack(draw));pi=byfirst[values[3]];p=prims[pi]
            if pi in eyes:values[2]=eye_slot*2+eyes[pi]
            draw=DRAW.pack(*values);newdraws.append(draw)
            if pi not in deformations:
                fixdraw.append(draw);continue
            ids=indices[values[3]:values[3]+values[4]]
            localids=a(p['indices']).ravel();assert len(ids)==len(localids)
            offset=int(ids[0])-int(localids[0]);assert np.array_equal(ids,localids+offset)
            localused=np.unique(localids);dp,dn=deformations[pi]
            if np.max(np.abs(dp[:,localused]))<1e-7:
                fixdraw.append(draw);continue
            used,mapping=np.unique(ids,return_inverse=True)
            modbase=len(modvertices);append.append((mapping+modbase).astype('<u4'))
            values[3]=next_index;next_index+=len(mapping);moddraw.append(DRAW.pack(*values))
            for j,globalidx in enumerate(used):
                vb=vertices[int(globalidx)*64:(int(globalidx)+1)*64];assert len(vb)==64
                modvertices.append(vb);li=int(globalidx)-offset
                # Positions in FR3 use 4096 units per metre, weights use 8192.
                base=np.frombuffer(vb[:32],dtype='<f4').copy()
                exported=a(p['attributes']['POSITION'])[li]*4096
                assert np.max(abs(base[:3]-exported))<0.015,(name,pi,base[:3],exported)
                active=np.flatnonzero((np.linalg.norm(dp[:,li],axis=1)>1e-7)|(np.linalg.norm(dn[:,li],axis=1)>1e-7))
                if not len(active):continue
                floatdata.append(base)
                for ch in active:
                    fd=np.zeros(8,dtype='<f4');fd[:3]=dp[ch,li]*0.5;fd[4:7]=dn[ch,li]/8192
                    floatdata.append(fd);intdata.append(int(ch))
                intdata.extend([0xffffffff,modbase+j]);modified_total+=1
        effect['draws']=newdraws
        if moddraw:
            effect['mod']=draw_list(moddraw)+draw_list(fixdraw)+vec(b''.join(modvertices),64)+vec(b'',2)+vec(b'',1)+struct.pack('<I',0)+vec(np.asarray(floatdata,dtype='<f4').tobytes(),32)+vec(np.asarray(intdata,dtype='<u4').tobytes(),4)
            effect['tail']=effect['tail'][:9]+b'\1'
            assert len(modvertices)<65535
    maxdraw=sum(max(len(e['draws']),len(Reader(e['mod']).draws())) for e in target['effects'])*2
    target['tail']=struct.pack('<I',maxdraw)+target['tail'][4:]
    return target,append,{'model':name,'native_weight_channels':count,'retargeted_vertices':modified_total,'eye_primitive_sides':eyes,'surface_correspondence':report}

def main():
    raw=decompress(BASE/'prototype/GAME-armor.fr3');indexstart,indices,vstart,original_vertices=buffers(raw,'jakb-c-lod0');vertices=bytearray(original_vertices)
    updates=[];reports=[];new_indices=indices
    for name,high,parts in [('jakb-c-lod0','jakc-highres-lod0',JAK_PARTS),('daxter-lod0','daxter-highres-lod0',DAX_PARTS)]:
        target,added,report=fix_model(raw,name,high,parts,new_indices,vertices)
        new_indices=np.concatenate([new_indices]+added);updates.append(target);reports.append(report)
    fixed=raw[:indexstart]+vec(new_indices.astype('<u4').tobytes(),4)+vec(vertices,64)+raw[-2:]
    for target in sorted(updates,key=lambda x:x['start'],reverse=True):fixed=fixed[:target['start']]+serialize(target)+fixed[target['end']:]
    _,check_indices,_,check_vertices=buffers(fixed,'jakb-c-lod0')
    assert check_vertices==vertices and np.array_equal(check_indices[:len(indices)],indices)
    # Only UVs may change in the neutral global vertex buffer.
    before=np.frombuffer(original_vertices,dtype='u1').reshape(-1,64);after=np.frombuffer(check_vertices,dtype='u1').reshape(-1,64)
    assert np.array_equal(before[:,:48],after[:,:48]) and np.array_equal(before[:,56:],after[:,56:])
    for target in updates:assert serialize(model(fixed,target['name']))==serialize(target)
    compress(BASE/'prototype/GAME-faces.fr3',fixed)
    (BASE/'face-retarget-report.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps(reports,indent=2))

if __name__=='__main__':main()
