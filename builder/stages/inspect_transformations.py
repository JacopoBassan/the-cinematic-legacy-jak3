import json,struct
from fr3_models import BASE,Reader,decompress,model,DRAW

def textures(raw):
    r=Reader(raw,2);r.take(r.num('Q'));out=[]
    for _ in range(r.num('Q')):
        w,h,combo=struct.unpack('<HHI',r.take(8));pixels=r.vector(4)
        name=r.take(r.num('Q')).decode();page=r.take(r.num('Q')).decode();flag=r.take(1)
        out.append(dict(name=name,page=page,width=w,height=h,combo=combo,pixels=pixels,flag=flag))
    return out,r.pos

if __name__=='__main__':
    for path,name in [('prototype/GAME-faces.fr3','jakb-c-lod0'),('prototype/GAME-baseline.fr3','jakb-c-lod0'),('extraction/out/jak3/fr3/ARENACST.fr3','jakc-highres-lod0'),('extraction/out/jak3/fr3/MHCTYCST.fr3','darkjak-highres-lod0')]:
        raw=decompress(BASE/path);tex,_=textures(raw);m=model(raw,name)
        print('\n',path,name)
        for i,e in enumerate(m['effects']):
            ds=[DRAW.unpack(d) for d in e['draws']]
            print(i,'tris',sum(d[5] for d in ds),'tex',[(d[1],tex[d[1]]['name'] if d[1]>=0 else 'ANIM') for d in ds])
        print('TRANSFORM TEXTURES',[(i,t['name'],t['width'],t['height']) for i,t in enumerate(tex) if 'jakchires' in t['name'] or 'dark' in t['name'] and 'jak' in t['name']])
