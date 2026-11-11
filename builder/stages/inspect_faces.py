import json,struct
import numpy as np
from scipy.spatial import cKDTree
from fr3_models import BASE,Reader,model,decompress,DRAW

def glb(path):
    b=path.read_bytes();n=struct.unpack_from('<I',b,12)[0]
    d=json.loads(b[20:20+n]);bin=b[28+n:]
    def acc(i):
        a=d['accessors'][i];v=d['bufferViews'][a['bufferView']]
        dt=np.dtype({5126:'<f4',5125:'<u4',5123:'<u2',5121:'u1'}[a['componentType']])
        k={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
        return np.ndarray((a['count'],k),dt,buffer=bin,offset=v.get('byteOffset',0)+a.get('byteOffset',0),strides=(v.get('byteStride',k*dt.itemsize),dt.itemsize)).copy()
    return d,acc

def shape_data(name):
    d,a=glb(BASE/'models'/f'{name}.glb');p=d['meshes'][0]['primitives'][0]
    used=np.unique(np.concatenate([a(x['indices']).ravel() for x in d['meshes'][0]['primitives']]))
    pos=a(p['attributes']['POSITION'])[used]
    delta=np.array([a(t['POSITION'])[used] for t in p['targets']])
    return pos,delta

def buffers(raw,name):
    end=model(raw,name)['end']
    while True:
        size=struct.unpack_from('<Q',raw,end)[0]
        if not 0<size<256:break
        try:
            text=raw[end+8:end+8+size].decode('ascii')
            if any(ord(c)<32 for c in text):break
            m=model(raw,text)
            assert m['start']==end
            end=m['end']
        except (ValueError,AssertionError,UnicodeDecodeError):break
    r=Reader(raw,end);indices=np.frombuffer(r.vector(4),dtype='<u4').copy();vstart=r.pos
    vertices=r.vector(64)
    assert r.pos==len(raw)-2,(r.pos,len(raw))
    return end,indices,vstart,vertices

if __name__=='__main__':
    for low,high in [('jakb-c-lod0','jakc-highres-lod0'),('daxter-lod0','daxter-highres-lod0')]:
        lp,ld=shape_data(low);hp,hd=shape_data(high)
        nearest=cKDTree(hp).query(lp)[1]
        print(low,'shapes',len(ld),len(hd),'positionbounds',lp.min(0),lp.max(0))
        for i,delta in enumerate(ld):
            relevant=np.linalg.norm(delta,axis=1)>1e-5
            pred=hd[:,nearest,:]
            # Compare over every face vertex, not only the moving subset.
            face=np.linalg.norm(ld,axis=2).max(0)>1e-5
            a=delta[face].ravel();b=pred[:,face].reshape(len(hd),-1)
            corr=b@a/(np.linalg.norm(b,axis=1)*np.linalg.norm(a)+1e-20)
            top=np.argsort(-corr)[:4]
            print(i,'moving',relevant.sum(),'max',round(np.linalg.norm(delta,axis=1).max(),4),'best',[(int(t),round(float(corr[t]),3)) for t in top])
    for file,name in [('prototype/GAME-baseline.fr3','jakb-c-lod0'),('extraction/out/jak3/fr3/ARENACST.fr3','jakc-highres-lod0'),('prototype/GAME-armor.fr3','jakb-c-lod0')]:
        raw=decompress(BASE/file);m=model(raw,name);off,inds,vstart,verts=buffers(raw,name)
        print(file,'indices',len(inds),'vertices',len(verts)//64)
        for i,e in enumerate(m['effects']):
            r=Reader(e['mod']);md=r.draws();fd=r.draws();v=r.vector(64)
            print(i,'eyes',[DRAW.unpack(d)[2] for d in e['draws']],'mod',len(md),len(v)//64)
