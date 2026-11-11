"""Reconnect cinematic Jak materials to native animated Dark Jak outputs."""
import json,struct,subprocess,hashlib
from fr3_models import BASE,Reader,DRAW,model,decompress,compress,serialize
from inspect_transformations import textures

raw=decompress(BASE/'prototype/GAME-faces.fr3')
tex,_=textures(raw)
m=model(raw,'jakb-c-lod0')
original=model(decompress(BASE/'extraction/out/jak3/fr3/ARENACST.fr3'),'jakc-highres-lod0')
slots={}
for effect,donor in zip(m['effects'],original['effects']):
    assert len(effect['draws'])==len(donor['draws'])
    for a,b in zip(effect['draws'],donor['draws']):
        a,b=DRAW.unpack(a),DRAW.unpack(b)
        if b[1]<0:
            assert a[5]==b[5]
            slots[tex[a[1]]['name']]=b[1]
assert slots=={'jakchires-hair':-13,'jakchires-eyebrow':-9,'jakchires-facert':-12,'jakchires-arm':-7,'jakchires-facelft':-11}
changed=0
def patch(draw):
    global changed
    a=list(DRAW.unpack(draw));name=tex[a[1]]['name'] if a[1]>=0 else None
    if name in slots:
        assert a[2]==255
        a[1]=slots[name];changed+=1
    return DRAW.pack(*a)
for effect in m['effects']:
    effect['draws']=[patch(d) for d in effect['draws']]
    r=Reader(effect['mod']);md=r.draws();fd=r.draws()
    effect['mod']=struct.pack('<Q',len(md))+b''.join(patch(d) for d in md)+struct.pack('<Q',len(fd))+b''.join(patch(d) for d in fd)+effect['mod'][r.pos:]
fixed=raw[:m['start']]+serialize(m)+raw[m['end']:]
assert len(fixed)==len(raw)
compress(BASE/'prototype/GAME-transformations.fr3',fixed)

