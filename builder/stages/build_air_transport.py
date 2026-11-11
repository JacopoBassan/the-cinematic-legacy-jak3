"""Build staged air-train assets from the user's pinned extracted game data.
Does not install or modify game files. Keep original effects, render controls,
collision and animations; transplant donor geometry, textures and named joints.
"""
import copy,hashlib,json,struct
from pathlib import Path
import numpy as np
from fr3_models import BASE,Reader,DRAW,decompress,compress,model,serialize
from inspect_faces import buffers
from inspect_transformations import textures
ROOT=None
OUT=BASE/'prototype/expansion-air-train'
LOW='air-train-lod0'; HIGH='air-train-fma-lod0'

def vec(b,size):return struct.pack('<Q',len(b)//size)+b

def texture_bytes(t):
 def s(x):b=x.encode();return struct.pack('<Q',len(b))+b
 return struct.pack('<HHI',t['width'],t['height'],t['combo'])+vec(t['pixels'],4)+s(t['name'])+s(t['page'])+t['flag']

def used_indices(m,ix):
 ids=np.unique(np.concatenate([ix[d[3]:d[3]+d[4]] for e in m['effects'] for d in map(DRAW.unpack,e['draws'])]))
 return ids[ids!=0xffffffff]

def build(data_dir, output_dir, report_path):
 global ROOT,OUT
 ROOT=Path(data_dir)/'out/jak3/fr3';OUT=Path(output_dir)
 metadata=json.loads(Path(__file__).with_name('air-build-catalog.json').read_text())
 catalog=metadata['models']
 donor_raw=decompress(ROOT/catalog[HIGH]['files'][0]);donor=model(donor_raw,HIGH)
 _,si,_,sv=buffers(donor_raw,HIGH);st,_=textures(donor_raw)
 sj={int(v):k for k,v in catalog[HIGH]['joints'].items()};lj={k:int(v) for k,v in catalog[LOW]['joints'].items()}
 # FR3 matrix IDs and GOAL joint node indices are both one based.
 mapping={j:lj[n] for j,n in sj.items()};mapping[0]=0
 used=used_indices(donor,si);source=np.frombuffer(sv,'u1').reshape(-1,64)[used].copy()
 sf=source.view('<f4').reshape(-1,16);weights=sf[:,8:11]
 assert np.all(np.isfinite(sf[:,:3])) and np.allclose(weights.sum(1),1,atol=1e-5)
 for k in range(3):
  for j in np.unique(source[:,60+k]):
   active=(source[:,60+k]==j)&(weights[:,k]>0)
   if active.any():assert int(j) in mapping;source[active,60+k]=mapping[int(j)]
 # Preserve donor scale and authored detail; do not normalize by bounding box.
 # Gameplay dimensions differ by under 3%, measured and retained for review.
 OUT.mkdir(exist_ok=True,parents=True);report=[]
 for file in catalog[LOW]['files']:
  raw=decompress(ROOT/file);target=model(raw,LOW);ts,tend=textures(raw)
  istart,ix,_,vb=buffers(raw,LOW);lowused=used_indices(target,ix)
  lowpos=np.frombuffer(vb,'<f4').reshape(-1,16)[lowused,:3]
  assert len(donor['effects'])==len(target['effects'])==4
  txids={d[1] for e in donor['effects'] for d in map(DRAW.unpack,e['draws'])}
  txids.update(struct.unpack_from('<I',e['tail'],4)[0] for e in donor['effects'] if e['tail'][8])
  assert min(txids)>=0
  txmap={};added=[]
  for i in sorted(txids):
   key=texture_bytes(st[i]);matches=[j for j,t in enumerate(ts) if texture_bytes(t)==key]
   txmap[i]=matches[0] if matches else len(ts)+len(added)
   if not matches:added.append(st[i])
  new=copy.deepcopy(donor);new['name']=LOW
  vmap=np.full(len(sv)//64,0xffffffff,dtype='<u4');vmap[used]=np.arange(len(used),dtype='<u4')+len(vb)//64
  app=[];nextidx=len(ix)
  for k,e in enumerate(new['effects']):
   assert e['mod']==target['effects'][k]['mod'],'Air train must not have deformation buffers'
   ds=[]
   for packed in e['draws']:
    d=list(DRAW.unpack(packed));original=si[d[3]:d[3]+d[4]];mapped=original.copy();valid=original!=0xffffffff;mapped[valid]=vmap[original[valid]]
    assert np.all(mapped[valid]!=0xffffffff)
    app.append(mapped);d[1]=txmap[d[1]];d[3]=nextidx;nextidx+=len(mapped);ds.append(DRAW.pack(*d))
   e['draws']=ds
   tail=bytearray(e['tail'])
   if tail[8]:struct.pack_into('<I',tail,4,txmap[struct.unpack_from('<I',tail,4)[0]])
   e['tail']=bytes(tail)
  # Keep original six-joint capacity while retaining the donor UV decode metadata.
  tail=bytearray(new['tail']);struct.pack_into('<I',tail,4,struct.unpack_from('<I',target['tail'],4)[0]);new['tail']=bytes(tail)
  allix=np.concatenate([ix]+app);allvb=vb+source.tobytes()
  fixed=raw[:istart]+vec(allix.tobytes(),4)+vec(allvb,64)+raw[-2:]
  fixed=fixed[:target['start']]+serialize(new)+fixed[target['end']:]
  r=Reader(raw,2);r.take(r.num('Q'));tstart=r.pos
  fixed=fixed[:tstart]+struct.pack('<Q',len(ts)+len(added))+b''.join(texture_bytes(t) for t in ts+added)+fixed[tend:]
  # Roundtrip and conservation checks, including all other Merc model records.
  checked=model(fixed,LOW);_,ci,_,cv=buffers(fixed,LOW);ct,_=textures(fixed)
  assert np.array_equal(ci,allix) and cv==allvb
  assert cv[:len(vb)]==vb and np.array_equal(ci[:len(ix)],ix)
  assert [texture_bytes(t) for t in ct[:len(ts)]]==[texture_bytes(t) for t in ts]
  assert serialize(checked)==serialize(new)
  fullcat={n:[{'file':f}] for f,names in metadata['file_models'].items() for n in names}
  preserved=0
  for name,entries in fullcat.items():
   if name!=LOW and any(x['file']==file for x in entries):
    assert serialize(model(raw,name))==serialize(model(fixed,name)),name;preserved+=1
  out=OUT/file;compress(out,fixed);assert decompress(out)==fixed
  extent=np.ptp(sf[:,:3],axis=0);lowextent=np.ptp(lowpos,axis=0)
  report.append({'file':file,'triangles':sum(DRAW.unpack(d)[5] for e in checked['effects'] for d in e['draws']), 'source_sha256':hashlib.sha256((ROOT/file).read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'original_bytes':(ROOT/file).stat().st_size,'output_bytes':out.stat().st_size,'other_models_preserved':preserved,'added_textures':len(added),'added_vertices':len(source),'joint_mapping':mapping,'gameplay_extent_m':(lowextent/4096).tolist(),'cinematic_extent_m':(extent/4096).tolist(),'extent_ratio':(extent/lowextent).tolist(),'status':'staged, requires rig/pivot and in-game rendering tests; not installed'})
 Path(report_path).write_text(json.dumps(report,indent=2));return report

