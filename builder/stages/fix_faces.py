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

def fit_daxter_grin(out, lp, la, hp, ha, sp, sa, report):
    """Restore the two native corner endpoints at the Dark Jak stance weight."""
    channel, weight = 0, 5568 / 8192
    source, cinematic = lp[6], sp[6]
    low = la(source['attributes']['POSITION'])
    high = sa(cinematic['attributes']['POSITION'])
    points = ha(hp[6]['attributes']['POSITION'])
    corners = high[[2494]].repeat(2, axis=0)
    corners[1, 0] *= -1
    native_ids = [3775, 3732]
    assert np.allclose(low[native_ids].T[0], [.11512, -.11512], atol=1e-5)
    high_ids = cKDTree(points).query(corners)[1]
    assert np.allclose(points[high_ids], corners, atol=1e-6)
    native = low[native_ids] + weight * la(source['targets'][channel]['POSITION'])[native_ids]
    fitted = points[high_ids] + weight * out[6][0][channel, high_ids]
    delta = (native - fitted) / weight
    # Match the left-right height difference while retaining the donor's mean
    # mouth height and depth. Use the same field at skin and interior seams.
    delta[:, 1] -= delta[:, 1].mean()
    delta[:, 2] = 0
    for part in (5, 6, 15):
        pos = ha(hp[part]['attributes']['POSITION'])
        distance = np.linalg.norm(pos[:, None] - corners, axis=2)
        influence = np.maximum(0, 1 - distance / .063) ** 2
        motion = out[part][0][channel]
        motion += influence @ delta
        # The donor's visible lip and teeth end before the corner landmarks.
        # Spread the front of the mouth and teeth together, tapering to the
        # corners and outside the muzzle.
        x, y, z = pos.T
        posed_x = x + motion[:, 0] * (9664 / 8192)
        mouth = np.exp(-((y - 1.043) / .044) ** 2)
        mouth *= np.clip((1.085 - y) / .015, 0, 1)
        mouth *= np.clip((z - .081) / .043, 0, 1)
        mouth *= np.exp(-(posed_x / .165) ** 4)
        motion[:, 0] += .24 * posed_x * mouth / weight
    report.append({'darkDaxterMouthCornerCorrectionM': (delta * weight).tolist()})
    mirror_daxter_grin(hp, ha, out)


def mirror_daxter_grin(primitives, accessor, deformations):
    """Use the screen-right corner for both sides of the Dark Jak grin."""
    reflect = np.array([-1, 1, 1])
    reflection = np.tile(reflect, 2)
    for part in (5, 6, 15):
        primitive = primitives[part]
        points = accessor(primitive['attributes']['POSITION'])
        normals = accessor(primitive['attributes']['NORMAL'])
        used = np.unique(accessor(primitive['indices']))
        x, y, z = points.T
        if part == 15:
            influence = np.ones(len(points))
        else:
            # Taper into the jaw, cheeks and nose. The same field covers copies
            # of the lip edges so the inside of the mouth moves with the fur.
            influence = np.clip((1.077 - y) / .019, 0, 1)
            influence *= np.clip((y - .965) / .040, 0, 1)
            influence *= np.clip((z - .076) / .035, 0, 1)
        affected = used[(x[used] < 0) & (influence[used] > 0)]
        reference = used[x[used] > 0]
        # Position identifies the mirrored vertex. A small normal term
        # distinguishes the surface sides where the neutral lips touch.
        features = np.c_[points, normals * .004]
        distance, match = cKDTree(features[reference]).query(features[affected] * reflection)
        assert np.all(distance < 1e-5), 'Daxter mouth has unmatched mirrored vertices'
        reference = reference[match]
        position_deltas, normal_deltas = deformations[part]
        motion, normal_motion = position_deltas[0], normal_deltas[0]
        alpha = influence[affected, None]
        motion[affected] += alpha * (motion[reference] * reflect - motion[affected])
        normal_motion[affected] += alpha * (normal_motion[reference] * reflect - normal_motion[affected])
        center = used[x[used] == 0]
        motion[center, 0] *= 1 - influence[center]
        # Ease the lower lip and tooth strip through the middle. Mirroring the
        # corner alone leaves a peak where the two sloping halves meet.
        lower_row = np.clip((-motion[:, 1] - .020) / .030, 0, 1)
        motion[:, 1] -= .012 * np.exp(-(x / .026) ** 2) * influence * lower_row


def close_daxter_eyelids(out, hp, ha):
    """Keep the textured caps behind the brow when the lids close."""
    peak = 9280 / 8192
    for part in (13, 14):
        pos = ha(hp[part]['attributes']['POSITION'])
        cap, _ = out[part]
        cap[0, :, 2] -= .020
        cap[6, :, 2] -= .038
        cap[6, :, 0] -= .004 * np.sign(pos[:, 0].mean())
        y = pos[:, 1] + peak * cap[6, :, 1]
        cap[6, :, 1] -= .34 * (y - y.mean()) / peak
    for part in (5, 6):
        pos = ha(hp[part]['attributes']['POSITION'])
        squeeze = out[part][0][6]
        x, y, z = (pos + peak * squeeze).T
        # Compress the remaining slit in posed space. Using the same field
        # on both sides of the material boundary keeps the fur seam together.
        eye_region = np.clip((abs(x) - .027) / .034, 0, 1)
        eye_region *= np.clip((.150 - abs(x)) / .027, 0, 1)
        eye_region *= np.clip((z - .050) / .070, 0, 1)
        squeeze[:, 1] -= .83 * (np.clip(y, 1.101, 1.118) - 1.101) * eye_region / peak


def fit_daxter_poses(lp, la, hp, ha, deformations, native):
    from daxter_expression_fit import compress_closed_caps, fit_mouth, fit_stretched_grimace, fit_tall_eyes, lower_tooth_row

    # The donor weights and target indices differ from those in Jak II.
    poses = {
        1: (7552/8192, ((4, .86),), (1.04, .75, 1)),
        2: (8064/8192, ((4, .58), (29, .78)), (1.06, .88, 1)),
        3: (1., ((10, 1.26),), (1.1, 1.25, 1)),
        4: (1., ((4, 1.),), (1, 1, 1)),
        5: (1., ((4, .35), (30, 1.05)), (1.06, .88, 1)),
        6: (9280/8192, ((19, .65),), (1, 1, 1)),
        7: (1., ((1, 1.27),), (1.26, 1.37, 1)),
        8: (9152/8192, ((4, .78),), (1.06, .70, 1)),
    }
    def donor_shape(part, target):
        positions, normals = native[part]
        return positions[target], normals[target]

    fit_mouth(hp, ha, deformations, donor_shape, poses, shout=7)
    tooth = ha(hp[15]['attributes']['POSITION'])
    lower = lower_tooth_row(hp[15], ha, tooth)
    upper = np.setdiff1d(ha(hp[15]['indices']), lower)
    for channel in (1, 2):
        weight = poses[channel][0]
        motion = deformations[15][0][channel]
        posed_x = tooth[:, 0] + weight*motion[:, 0]
        if channel == 1:
            # The native clench shows 25 mm of upper teeth at center, versus
            # 37 mm on the donor. Keep the outer dental edges fixed.
            edge = np.clip((.140-abs(posed_x))/.030, 0, 1)
        else:
            # Expose the lower band without following the raised lip into a
            # diagonal bite line. Ease the lift out under the narrow corner.
            lift = np.interp(posed_x, [-.125, -.105, -.085, -.060, -.035, 0, .035, .060, .085, .105, .125],
                             [0, .002, .007, .013, .013, .012, .012, .012, .012, .014, .015])
        for row, lower_arcade in ((lower, True), (upper, False)):
            y = tooth[row, 1]
            assert np.ptp(y) > .010
            contact = (y-y.min())/np.ptp(y) if lower_arcade else (y.max()-y)/np.ptp(y)
            correction = (.012/weight*contact*edge[row]**2*(3-2*edge[row]) if channel == 1
                          else lift[row]/weight*contact)
            motion[row, 1] += correction
        assert np.isfinite(motion).all()
    fit_tall_eyes((lp[1], lp[2]), la, hp, ha, deformations,
                  ((2, 8064/8192), (3, 1.), (7, 1.), (8, 9152/8192)))
    fit_stretched_grimace((lp[1], lp[2]), la, hp, ha, deformations, 9152/8192)
    compress_closed_caps(hp, ha, deformations, channel=6, weight=9280/8192, fraction=.26)
    fit_daxter_grin_profiles(lp[6], la, hp, ha, deformations, lower, upper)


def fit_daxter_grin_profiles(stock_head, read_stock, parts, read, shapes, lower, upper):
    """Retain the cinematic grin targets and match the stock lip and dental contours."""
    from daxter_expression_fit import smoothstep

    # The wide clench exposes 38 mm of the upper teeth at the center. The
    # donor exposes 57 mm. Lift their contact while keeping both roots.
    teeth = read(parts[15]['attributes']['POSITION'])
    for row, is_lower in ((lower, True), (upper, False)):
        y = teeth[row, 1]
        along = (y-y.min())/np.ptp(y)
        contact = along if is_lower else 1-along
        shapes[15][0][4, row, 1] += .019*contact

    # Stock teeth extend to 160 mm on each side and the band bends upward
    # near the corners. Use a shared field on skin, lining and enamel.
    for part in (4,5,6,15):
        rest = read(parts[part]['attributes']['POSITION'])
        motion = shapes[part][0][4]
        x,y,z = (rest+motion).T
        influence = (np.ones(len(rest)) if part == 15 else
                     smoothstep((1.105-rest[:,1])/.036)*smoothstep((rest[:,2]-.025)/.048))
        corner = smoothstep((abs(x)-.080)/.070)
        level = smoothstep((y-.974)/.085)
        motion[:,0] += influence*x*(160/149-1)
        motion[:,1] += influence*.018*corner*level
    # The native one-sided smile goes from a broad opening on the left to
    # a slit on the right. Carry the authored asymmetric pose farther
    # sideways, without widening the entire face or touching the eyelids.
    for part in (4,5,6,15):
        rest = read(parts[part]['attributes']['POSITION'])
        motion = shapes[part][0][5]
        x,y,z = (rest+motion).T
        influence = (np.ones(len(rest)) if part == 15 else
                     smoothstep((1.105-rest[:,1])/.036)*smoothstep((rest[:,2]-.025)/.048))
        radius = abs(x)
        widening = np.interp(radius,[0,.104,.175,.235],[0,.058,.020,0])
        motion[:,0] += influence*np.sign(x)*widening
        # Restore the upward slope of the lower lip on the closing side.
        # The underlying teeth and mouth lining share the same field.
        left = np.interp(x,[-.105,-.080,-.045,0], [.011,.007,0,0])
        right = np.interp(x,[0,.045,.080,.105],[0,0,.006,.011])
        low = np.clip((1.043-y)/.022,0,1)
        motion[:,1] += influence*(left+right*low)

    # Sample the outer yellow upper-cheek vertex on each actual neutral
    # mesh. The one-sided grin raises these corners by different amounts.
    stock = read_stock(stock_head['attributes']['POSITION'])
    donor = read(parts[6]['attributes']['POSITION'])
    def tips(points, ids):
        low = points[ids]
        band = (low[:,1]>.970)&(low[:,1]<1.110)&(low[:,2]>.070)
        ids = ids[band]
        return ids[[points[ids,0].argmin(),points[ids,0].argmax()]]
    native_tips = tips(stock, np.unique(read_stock(stock_head['indices'])))
    donor_tips = tips(donor, np.unique(read(parts[6]['indices'])))
    channel = 5
    native = read_stock(stock_head['targets'][channel]['POSITION'])[native_tips,1]
    correction = native-shapes[6][0][channel,donor_tips,1]
    for part in (5,6):
        rest = read(parts[part]['attributes']['POSITION'])
        x,y,z = rest.T
        cheek = smoothstep((abs(x)-.118)/.034)*smoothstep((y-1.030)/.030)
        cheek *= smoothstep((1.103-y)/.025)*smoothstep((z-.024)/.045)
        shapes[part][0][channel,:,1] += cheek*np.interp(x,donor[donor_tips,0],correction)

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
        if low == 'jakb-c-lod0':
            # retarget_glb packs each primitive in ascending source-index order.
            # Contacting lips share positions but can move in opposite directions.
            assert np.allclose(sa(orig['attributes']['POSITION'])[used],ha(p['attributes']['POSITION']),atol=1e-6)
            near=np.arange(len(used))
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
        landmarks = [(17443,20194),(17401,20170),(17370,20165),
                     (17308,20223),(17307,20207),(17309,20249),(17415,20196)]
        source_part, cinematic_part = lp[29], sp[26]
        assert np.allclose(la(source_part['attributes']['POSITION'])[17443],
                           [0,2.344,0.3178],atol=0.0001)
        def opposite(part, read, ids):
            used = np.unique(read(part['indices']))
            # On the closed mouth, position alone conflates touching surfaces.
            # The authored jaw-open motion disambiguates the two lips.
            features = np.c_[read(part['attributes']['POSITION']),
                             read(part['targets'][0]['POSITION'])[:,1]]
            distances, matches = cKDTree(features[used]).query(features[ids]*[-1,1,1,1])
            assert max(distances)<.010
            return used[matches]
        low_pts, high_pts = zip(*landmarks[3:])
        landmarks += list(zip(map(int,opposite(source_part,la,list(low_pts))),
                              map(int,opposite(cinematic_part,sa,list(high_pts)))))
        mouth_a = np.concatenate([np.array([sa(t['POSITION'])[hi]
                                  for t in cinematic_part['targets']]).T
                                  for lo,hi in landmarks])
        errors = {}
        for ch in (0,1,11,12):
            mouth_b = np.concatenate([la(source_part['targets'][ch]['POSITION'])[lo]
                                      for lo,hi in landmarks])
            coeff[ch] = lsq_linear(
                np.vstack([A,np.eye(A.shape[1])*regularizer,mouth_a*64.0]),
                np.r_[B[:,ch],np.zeros(A.shape[1]),mouth_b*64.0],
                bounds=(0,1.5),tol=1e-9).x
            errors[str(ch)] = float(np.abs(mouth_a@coeff[ch]-mouth_b).max())
        report.append({'snarl_mouth_landmarks':landmarks,'max_landmark_error_m':errors})
        # Both smiles open the center lips by 25 mm. Combining unrelated
        # mouth shapes to reach that opening folds the inner cinematic lip.
        # Keep the donor smile intact and fit the gameplay brow over it.
        smile=9
        brow_targets=np.flatnonzero(np.linalg.norm(native[27][0],axis=(1,2))>1e-5)
        residual=B[:,smile]-A[:,smile]
        coeff[smile]=0
        coeff[smile,smile]=1
        coeff[smile,brow_targets]=lsq_linear(
            np.vstack([A[:,brow_targets],np.eye(len(brow_targets))*regularizer]),
            np.r_[residual,np.zeros(len(brow_targets))],bounds=(0,1.5),tol=1e-9).x
    facechannels=([0,1,2,8,9,10,11,12] if low.startswith('jak') else list(range(count)))
    for pi in faceparts:
        dp,dn=out[pi];npd,nnd=native[pi]
        for ch in facechannels:
            dp[ch]=np.einsum('t,tnj->nj',coeff[ch],npd)
            dn[ch]=np.einsum('t,tnj->nj',coeff[ch],nnd)
    if low == 'daxter-lod0':
        fit_daxter_grin(out, lp, la, hp, ha, sp, sa, report)
        close_daxter_eyelids(out, hp, ha)
        fit_daxter_poses(lp, la, hp, ha, out, native)
    if low.startswith('jak'):
        fit_jak_expression_endpoints(lp, la, hp, ha, sp, sa, out, native, report)
        fit_jak_authored_eye_region(hp, ha, out, native)
        # The donor starts with higher goggles and has its own lowered pose.
        # Transfer its lens, frame and strap as a set, including their normals.
        for pi in (20,21,25):
            donor=sp[pi]
            used=np.unique(sa(donor['indices']))
            distance,near=cKDTree(sa(donor['attributes']['POSITION'])[used]).query(
                ha(hp[pi]['attributes']['POSITION']))
            assert distance.max()<1e-5
            lowered=donor['targets'][15]
            dp,dn=out[pi]
            dp[4]=sa(lowered['POSITION'])[used[near]]
            dn[4]=sa(lowered['NORMAL'])[used[near]]
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
                for ch in (0,1,8,9,10,11,12):
                    dp[ch,vi]=face_dp[ch,best]
                    dn[ch,vi]=face_dn[ch,best]
                seams+=1
        report.append({'snarlSeamVerticesMatched':seams})
        # The authored cinematic poses move the lids and brows around fixed
        # eye surfaces. The live compositor continues to drive gaze and blink.
        for pi in (1, 2):
            out.pop(pi)
    report.append({'native_cinematic_basis_fit':True,'coefficients':{str(ch):{str(t):round(float(w),6) for t,w in enumerate(coeff[ch]) if w>1e-4} for ch in facechannels},'regularizer':regularizer})
    return hd,ha,out,count,report


def fit_jak_authored_eye_region(primitives, accessor, out, authored):
    """Use the donor's coordinated eyelid, skin and brow shapes."""
    # The stock shout and clench have the same brow motion as the frown,
    # with more travel. Target 3 supplies that motion on the cinematic head.
    # The smile adds authored left/right lid motion to its original shape.
    poses = {
        0: ((3, .75),),
        1: ((3, .75),),
        8: ((3, .69),),
        9: ((9, 1.), (3, .46), (4, .21), (5, .21)),
    }
    for part in (26, 27):
        x, y, z = accessor(primitives[part]['attributes']['POSITION']).T
        face = np.clip((y-2.446)/.036, 0, 1) * np.clip((z-.160)/.070, 0, 1)
        face *= np.clip((.220-abs(x))/.040, 0, 1)
        for motion, donor in zip(out[part], authored[part]):
            for channel, shapes in poses.items():
                target = sum(weight * donor[index] for index, weight in shapes)
                motion[channel] += face[:, None] * (target - motion[channel])


def fit_jak_smile_mouth(primitives, accessor, out, lower_teeth):
    """Follow the stock smile's shallow lip line and single visible tooth band."""
    channel = 9
    for part in (24, 26):
        motion = out[part][0][channel]
        x, y, z = (accessor(primitives[part]['attributes']['POSITION']) + motion).T
        front = np.clip((z-.195)/.070, 0, 1) * np.exp(-(x/.105)**6)
        lower_lip = np.exp(-((y-2.344)/.025)**2) * (1 - np.exp(-(x/.028)**2))
        upper_lip = np.exp(-((y-2.373)/.024)**2)
        motion[:, 1] -= front * (.013*lower_lip + .008*upper_lip)

    motion = out[28][0][channel]
    posed = accessor(primitives[28]['attributes']['POSITION']) + motion
    x, y, _ = posed[lower_teeth].T
    # The original tooth band rises only 4 mm from center to corner.
    # Remove the donor's pronounced arch before extending its crowns.
    flatten = .31 * (np.hypot(x, .009) - .009)
    crown = np.clip((y - flatten - 2.323)/.035, 0, 1)
    motion[lower_teeth, 0] -= .15*x
    motion[lower_teeth, 1] += .013*crown - flatten
    motion[lower_teeth, 2] += .013 + .005*np.exp(-(x/.060)**2)
    motion[~lower_teeth, 1] += .014
    motion[~lower_teeth, 2] -= .013


def fit_jak_expression_endpoints(lp, la, hp, ha, sp, sa, out, native, report):
    """Fit the smirk's brow and the visible mouth endpoints on the human head."""
    brow_ids = [17150, 17154, 17158]
    donor_ids = [20368, 20362, 20314]
    def both_sides(part, read, picks):
        points = read(part['attributes']['POSITION'])
        used = np.unique(read(part['indices']))
        reflected = points[picks] * [-1, 1, 1]
        distances, matches = cKDTree(points[used]).query(reflected)
        assert max(distances) < .006
        return np.r_[used[matches][::-1], picks]
    brow_ids = both_sides(lp[28], la, brow_ids)
    donor_ids = both_sides(sp[27], sa, donor_ids)
    original_brow = la(lp[28]['attributes']['POSITION'])[brow_ids]
    donor_brow = sa(sp[27]['attributes']['POSITION'])[donor_ids]
    high_brow = ha(hp[27]['attributes']['POSITION'])
    distance, mapped = cKDTree(high_brow).query(donor_brow)
    assert max(distance) < 1e-6
    target = original_brow + la(lp[28]['targets'][10]['POSITION'])[brow_ids]
    correction = target - (donor_brow + out[27][0][10, mapped])
    for pi in (26, 27):
        x, y, z = ha(hp[pi]['attributes']['POSITION']).T
        influence = np.clip((y - 2.475) / .051, 0, 1)
        influence *= np.clip((2.696 - y) / .067, 0, 1)
        influence *= np.clip((z - .245) / .029, 0, 1)
        influence *= np.clip((.205 - abs(x)) / .042, 0, 1)
        field = np.column_stack([np.interp(x, donor_brow[:, 0], column) for column in correction.T])
        out[pi][0][10] += influence[:, None] * field
    # The native clench shows one continuous strip of teeth. Donor lip shapes
    # open both dental arches apart, leaving daylight at the center.
    out[28][0][1] = 0
    out[28][1][1] = 0
    # The native shout displays the upper edge of a single tooth strip.
    # Keep the lower donor teeth under the moving lower lip and give the upper
    # row its native endpoint, including the fully hidden teeth on channel 12.
    lower_teeth = native[28][0][0, :, 1] < -.005
    native_teeth = lp[31]
    donor_tooth_edge = sa(sp[28]['attributes']['POSITION'])[20437, 1]
    native_tooth_edge = la(native_teeth['attributes']['POSITION'])[17466, 1]
    # The stock clench presents a single upper plate down to the inside of
    # the lower lip. Extend the upper crowns to the measured lower edge so
    # the neutral lower incisors do not leave a dark gap below the clench.
    clench_peak = 9856 / 8192
    edge_travel = (native_tooth_edge - donor_tooth_edge) / clench_peak
    edge_travel += la(native_teeth['targets'][1]['POSITION'])[17466, 1]
    teeth_y = ha(hp[28]['attributes']['POSITION'])[:,1]
    root_y = sa(sp[28]['attributes']['POSITION'])[20449, 1]
    crown = np.clip((root_y - teeth_y) / (root_y - donor_tooth_edge), 0, 1)
    out[28][0][1, ~lower_teeth, 1] = edge_travel * crown[~lower_teeth]
    out[28][0][1, lower_teeth, 2] = -.010
    for ch, peak in ((0,8512/8192), (11,5248/8192), (12,8704/8192)):
        original_drop = la(lp[29]['targets'][ch]['POSITION'])[17443, 1]
        upper_edge = (native_tooth_edge - donor_tooth_edge) / peak
        upper_edge += la(native_teeth['targets'][ch]['POSITION'])[17466, 1]
        pos, norm = out[28]
        pos[ch] = 0
        norm[ch] = 0
        pos[ch, ~lower_teeth, 1] = upper_edge
        pos[ch, lower_teeth] = [0, original_drop - .040, -.035]
        if ch == 12:
            donor_drop = sa(sp[26]['targets'][0]['POSITION'])[20194, 1]
            jaw_scale = (original_drop - .018) / donor_drop
            pos[ch, lower_teeth] = native[28][0][0, lower_teeth] * jaw_scale
            norm[ch, lower_teeth] = native[28][1][0, lower_teeth] * jaw_scale
    # Donor target 9 preserves the center smile opening but underdrives both
    # corners. Apply the measured corner travel to all contacting oral surfaces.
    source_ids = both_sides(lp[29], la, [17308])
    donor_corner_ids = both_sides(sp[26], sa, [20223])
    donor_positions = sa(sp[26]['attributes']['POSITION'])[donor_corner_ids]
    source_displacement = la(lp[29]['targets'][9]['POSITION'])[source_ids]
    donor_displacement = sa(sp[26]['targets'][9]['POSITION'])[donor_corner_ids]
    offset = la(lp[29]['attributes']['POSITION'])[source_ids] + source_displacement
    offset -= donor_positions + donor_displacement
    offset[:, 2] = 0
    for pi in (24, 26, 28):
        pos = ha(hp[pi]['attributes']['POSITION']) + out[pi][0][9]
        x, y, z = pos.T
        oral = np.clip((z - .195) / .046, 0, 1)
        oral *= np.clip((.345 - z) / .030, 0, 1)
        oral *= np.clip((2.447 - y) / .057, 0, 1) * np.clip((y - 2.255) / .067, 0, 1)
        for side, center in enumerate(donor_positions + donor_displacement):
            side_x = x * np.sign(center[0])
            influence = np.clip(side_x / abs(center[0]), 0, 1)
            influence *= np.clip((.166 - side_x) / .078, 0, 1) * oral
            out[pi][0][9] += influence[:,None] * offset[side]
    fit_jak_smile_mouth(hp, ha, out, lower_teeth)
    # Native closed lips form a short, angular frown on 8 and a longer,
    # one-sided crease on 10. Align the actual lip line, including the
    # different neutral heights at its corners, across the entire muzzle.
    native_line = both_sides(lp[29], la, [17309,17308,17436])
    donor_line = both_sides(sp[26], sa, [20249,20223,20212])
    face_used = np.unique(sa(sp[26]['indices']))
    compact = np.searchsorted(face_used, donor_line)
    for ch in (8, 10):
        low_line = la(lp[29]['attributes']['POSITION'])[native_line] + la(lp[29]['targets'][ch]['POSITION'])[native_line]
        high_line = ha(hp[26]['attributes']['POSITION'])[compact] + out[26][0][ch, compact]
        centers = np.r_[high_line[:3,0], 0., high_line[3:,0]]
        correction = np.insert(low_line[:,:2] - high_line[:,:2], 3, [0.,0.], axis=0)
        pos = ha(hp[26]['attributes']['POSITION']) + out[26][0][ch]
        x, y, z = pos.T
        influence = np.clip((z - .20) / .045, 0, 1) * np.clip((.346-z) / .035, 0, 1)
        influence *= np.clip((2.43-y) / .034, 0, 1) * np.clip((y-2.275)/.05, 0, 1)
        influence *= np.clip((.16-abs(x))/.046,0,1)
        field = np.column_stack([np.interp(x,centers,values) for values in correction.T])
        out[26][0][ch,:,:2] += influence[:,None] * field
    fit_raised_scarf(lp[25], la, sp[39], sa, hp[39], ha, out[39])
    report.append({'humanBrowLandmarks': list(zip(map(int,brow_ids), map(int,donor_ids))),
                   'raisedScarfGameplayChannel': 7})


def fit_raised_scarf(stock, read_stock, donor, read_donor, scarf, read, output):
    """Transfer the original collar, front fold and free edge as distinct cloth layers."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    channel = 7
    points = read(scarf['attributes']['POSITION'])
    indices = read(scarf['indices']).reshape(-1, 3)
    used, inverse = np.unique(indices, return_inverse=True)
    welded, weld_ids = np.unique(points[used].round(6), axis=0, return_inverse=True)
    tris = weld_ids[inverse.reshape(-1, 3)]
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    graph = coo_matrix((np.ones(len(edges)), edges.T), shape=(len(welded),)*2)
    _, component = connected_components(graph, directed=False)
    # This material also contains two disconnected side strips. Only the
    # neck wrap corresponds to the stock cloth. Leave the strips on their
    # own authored motion, including their stationary upper attachments.
    src = read_donor(donor['attributes']['POSITION'])
    donor_ids = np.unique(read_donor(donor['indices']))
    dist, near = cKDTree(src[donor_ids]).query(points[used])
    assert dist.max() < 1e-6
    for field, key in zip(output, ('POSITION', 'NORMAL')):
        field[channel, used] = read_donor(donor['targets'][23][key])[donor_ids[near]]
    wrap = used[component[weld_ids] == component[np.argmin(welded[:, 1])]]

    native = read_stock(stock['attributes']['POSITION'])
    stock_tri = read_stock(stock['indices']).reshape(-1, 3)
    travel = read_stock(stock['targets'][channel]['POSITION'])
    # Register the two neutral surfaces at the hem, intermediate front
    # crease, three free-edge points, and inner neck seam. All remaining
    # native landmarks retain their own rest coordinates.
    native_ids = [16751, 16789, 16778, 16779, 16780, 16762]
    donor_ids = [23843, 23859, 23831, 23837, 23944, 23838]
    reference = native[stock_tri].copy()
    endpoint = (native + travel)[stock_tri].copy()
    for src_id, dst_id in zip(native_ids, donor_ids):
        matches = (native[stock_tri] == native[src_id]).all(axis=2)
        reference[matches] = src[dst_id]
        if not travel[src_id].any():
            endpoint[matches] = src[dst_id]
    triangles, bary, _ = closest_surface(points[wrap], reference)
    dp = np.einsum('ni,nij->nj', bary, (endpoint-reference)[triangles])
    stock_uv = read_stock(stock['attributes']['TEXCOORD_0'])
    cloth_uv = read(scarf['attributes']['TEXCOORD_0']).copy()
    cloth_uv[wrap] = np.einsum('ni,nij->nj', bary, stock_uv[stock_tri[triangles]])
    # The front of the donor wrap is a strip of seven sewn cross-sections.
    # Nearest triangles alone can send adjacent rim vertices to opposing
    # layers of the folded stock cloth. Sample each whole strip in order
    # from the free edge through the native crease to the anchored hem.
    columns = (
        (23827,23826,23825,23824,23823,23850),
        (23831,23833,23858,23835,23853,23851),
        (23832,23834,23859,23860,23882,23843),
        (23837,23836,23861,23857,23903,23862),
        (23946,23974,23982,23929,23928,23846),
        (23944,23983,23907,23935,23933,23934),
        (23943,23973,23936,23924,23925,23926),
    )
    # Corresponding stock section curves from free edge to chest. The
    # central section has a single intermediate crease where the two side
    # creases meet. The inserted donor sections sample the adjacent native
    # curves at their relative lateral distance on the rest rim.
    sections = np.array([
        [16777,16791,16794,16816], [16778,16788,16793,16750],
        [16779,16789,16789,16751], [16780,16774,16767,16756],
        [16770,16781,16755,16754],
    ])
    anchor_columns = [0,1,3,5,6]
    donor_rim_x = src[[c[0] for c in columns], 0]
    along = np.interp(donor_rim_x, donor_rim_x[anchor_columns], np.arange(len(sections)))
    stock_normals = read_stock(stock['attributes']['NORMAL']) + read_stock(stock['targets'][channel]['NORMAL'])
    panel_normals = {}
    for column, lateral in zip(columns, along):
        left = min(int(lateral), len(sections)-2)
        fraction = lateral-left
        native_curve = (1-fraction)*native[sections[left]] + fraction*native[sections[left+1]]
        posed_curve = ((1-fraction)*(native+travel)[sections[left]]
                       + fraction*(native+travel)[sections[left+1]])
        donor_column = src[list(column)]
        # Parameterize both pieces of fabric by distance along the resting
        # material. This preserves sample order even at the doubled crease.
        stock_s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(native_curve, axis=0), axis=1))]
        donor_s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(donor_column, axis=0), axis=1))]
        progress = donor_s/donor_s[-1]
        goal = np.column_stack([np.interp(progress*stock_s[-1], stock_s, axis)
                                for axis in posed_curve.T])
        # Keep the cinematic hem sewn into its own shirt at all weights.
        goal += progress[:, None]**2 * (donor_column[-1]-posed_curve[-1])
        curve_normals = (1-fraction)*stock_normals[sections[left]] + fraction*stock_normals[sections[left+1]]
        goal_normals = np.column_stack([np.interp(progress*stock_s[-1], stock_s, axis)
                                       for axis in curve_normals.T])
        # The cinematic lower eye surfaces sit below the stock ones.
        # Matched front and angled frames both require the crest to clear
        # 12/9 additional pixels across the two eyes at full raise. The
        # front view projects 188 mm between cap centers onto 78.6 pixels.
        # Distribute that eye-relative correction into the free fabric,
        # preserving the lower hem and tapering into the side attachment.
        edge_x = posed_curve[0, 0]
        clearance = np.interp(edge_x, [-.205, -.155, -.094, .094, .155, .205],
                              np.array([0, 12, 12, 9, 9, 0]) * (.188 / 78.6))
        goal[:, 1] -= clearance * (1-progress)
        uv_curve = (1-fraction)*stock_uv[sections[left]] + fraction*stock_uv[sections[left+1]]
        goal_uv = np.column_stack([np.interp(progress*stock_s[-1], stock_s, axis) for axis in uv_curve.T])
        for point, target, normal, uv in zip(donor_column, goal, goal_normals, goal_uv):
            seam = np.linalg.norm(points[wrap]-point, axis=1) < 1e-6
            assert seam.any()
            dp[seam] = target-point
            cloth_uv[wrap[seam]] = uv
            for vertex in np.flatnonzero(seam):
                panel_normals[vertex] = normal/np.linalg.norm(normal)
    # Keep the donor's original texture chart behind the neck. The two
    # wraps put their repeat seam on different sides. Register the native
    # chart only on the forward panel and feather across the shoulders.
    original_uv = read(scarf['attributes']['TEXCOORD_0'])
    front = np.clip((points[wrap, 2]+.010)/.080, 0, 1)
    front *= front*(3-2*front)
    cloth_uv[wrap] = original_uv[wrap] + front[:, None]*(cloth_uv[wrap]-original_uv[wrap])
    scarf['_cloth_uv'] = cloth_uv
    output[0][channel, wrap] = dp

    # Transport the donor's shading normals by the change in its own face
    # directions, retaining the smoothing on either side of texture seams.
    posed = points.copy()
    posed[wrap] += dp
    def face_normals(vertices):
        corners = vertices[indices]
        n = np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0])
        return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    before, after = face_normals(points), face_normals(posed)
    normals = read(scarf['attributes']['NORMAL'])
    # The shortest rotation from each original face normal to its posed
    # normal rotates every authored corner normal of that face.
    axis = np.cross(before, after)
    cosine = np.einsum('ij,ij->i', before, after)
    initial = normals[indices]
    cross = np.cross(axis[:, None], initial)
    rotated = initial + cross + np.cross(axis[:, None], cross) / np.maximum(1+cosine[:, None, None], 1e-8)
    sums = np.zeros_like(points)
    np.add.at(sums, indices.ravel(), rotated.reshape(-1, 3))
    fitted = sums[wrap] / np.maximum(np.linalg.norm(sums[wrap], axis=1, keepdims=True), 1e-8)
    # The stock front has authored shading across the crest and crease.
    for vertex, normal in panel_normals.items():
        fitted[vertex] = normal
    output[1][channel, wrap] = fitted-normals[wrap]

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
    for pi, primitive in enumerate(prims):
        if '_cloth_uv' in primitive:
            values = DRAW.unpack(draws[pi])
            ids = indices[values[3]:values[3]+values[4]]
            local = a(primitive['indices']).ravel()
            offset = int(ids[0])-int(local[0])
            assert np.array_equal(ids, local+offset)
            for vertex in np.unique(local):
                struct.pack_into('<2f', vertices, (offset+int(vertex))*64+48, *primitive['_cloth_uv'][vertex])
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
