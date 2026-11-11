"""Pinned format43 MercModel serialization, from upstream TFrag3Data.cpp.
Locate exact length-prefixed names; preserve all bytes outside the selected model.
"""
import struct,zstandard
from pathlib import Path
BASE=Path(__import__('os').environ['CINEMATIC_BUILD_WORK'])
DRAW=struct.Struct('<IiBIIIB')
class Reader:
    def __init__(self,data,pos=0):self.data=data;self.pos=pos
    def take(self,n):
        b=self.data[self.pos:self.pos+n];assert len(b)==n;self.pos+=n;return b
    def num(self,fmt):return struct.unpack('<'+fmt,self.take(struct.calcsize('<'+fmt)))[0]
    def vector(self,size):return self.take(self.num('Q')*size)
    def draws(self):return [self.take(DRAW.size) for _ in range(self.num('Q'))]
    def effect(self):
        draws=self.draws();start=self.pos
        self.draws();self.draws();self.vector(64);self.vector(2);self.vector(1);self.take(4);self.vector(32);self.vector(4)
        mod=self.data[start:self.pos];tail=self.take(10)
        return {'draws':draws,'mod':mod,'tail':tail}
def decompress(path):
    b=Path(path).read_bytes();raw=zstandard.ZstdDecompressor().decompress(b[8:]);assert len(raw)==struct.unpack_from('<Q',b)[0];return raw
def compress(path,raw):Path(path).write_bytes(struct.pack('<Q',len(raw))+zstandard.ZstdCompressor(level=1).compress(raw))
def model(raw,name):
    needle=struct.pack('<Q',len(name))+name.encode();start=raw.find(needle);assert start>=0 and raw.find(needle,start+1)<0
    r=Reader(raw,start+len(needle));n=r.num('Q');assert 0<n<100
    effects=[r.effect() for _ in range(n)];tail=r.take(20)
    return {'name':name,'start':start,'end':r.pos,'effects':effects,'tail':tail}
def serialize(m):
    b=struct.pack('<Q',len(m['name']))+m['name'].encode()+struct.pack('<Q',len(m['effects']))
    for e in m['effects']:b+=struct.pack('<Q',len(e['draws']))+b''.join(e['draws'])+e['mod']+e['tail']
    return b+m['tail']
if __name__=='__main__':
    for file,name in [('prototype/GAME-baseline.fr3','jakb-c-lod0'),('extraction/out/jak3/fr3/ARENACST.fr3','jakc-highres-lod0'),('prototype/GAME.fr3','jakb-c-lod0')]:
        raw=decompress(BASE/file);m=model(raw,name);assert serialize(m)==raw[m['start']:m['end']]
        print(file,name,'effects',len(m['effects']))
        for i,e in enumerate(m['effects']):print(i,[DRAW.unpack(d)[5] for d in e['draws']], 'env',e['tail'][8])
