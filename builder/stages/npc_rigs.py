"""Read selected v5 GOAL art-group skeletons without a full extraction.

These are the original inverse bind matrices. FR3 matrix IDs are one based;
joint structures store their index zero based. Only a fully validated contiguous
joint table matching the known catalog is accepted.
"""
import struct
from pathlib import Path
import numpy as np

RAW = None

def skeleton(art_name, expected):
    data = (RAW / (art_name + '-ag.go')).read_bytes()
    assert struct.unpack_from('<H', data, 8)[0] == 5
    base = struct.unpack_from('<I', data, 4)[0]
    by_id = {int(i)-1: name for name, i in expected.items()}
    matches = []
    for start in range(base, min(len(data)-80*len(expected), 65536), 16):
        if struct.unpack_from('<III', data, start)[::2] != (0xffffffff, 0):
            continue
        out = {}
        for i in range(len(expected)):
            at = start+80*i
            tag, ptr, index, parent = struct.unpack_from('<4I', data, at)
            if tag != 0xffffffff or index != i or ptr+base+4 > len(data):
                break
            length = struct.unpack_from('<I', data, ptr+base)[0]
            if not 0 < length < 100:
                break
            name = data[ptr+base+4:ptr+base+4+length].decode('ascii', errors='replace')
            if name != by_id[i]:
                break
            matrix = np.array(struct.unpack_from('<16f', data, at+16)).reshape(4, 4).T
            if not np.all(np.isfinite(matrix)) or not np.allclose(matrix[3], [0,0,0,1], atol=1e-5):
                break
            out[name] = dict(id=i+1, inv_bind=matrix, parent=(parent+base-start-4)//80+1 if parent != 0xffffffff else None)
        if len(out) == len(expected):
            matches.append(out)
    assert len(matches) == 1, (art_name, len(matches))
    return matches[0]

def effect_matrices(art_name, model_name, expected_count):
    """Decode original per-effect Merc matrix transfers, including unused IDs."""
    data=(RAW/(art_name+'-ag.go')).read_bytes()
    assert struct.unpack_from('<H',data,8)[0]==5
    base=struct.unpack_from('<I',data,4)[0]
    names=[];needle=model_name.encode()
    for at in range(base,len(data)-len(needle)-8,4):
        if data[at:at+4]==struct.pack('<I',len(needle)) and data[at+4:at+4+len(needle)]==needle:names.append(at-base)
    matches=[]
    for at in range(base,len(data)-176-expected_count*32,16):
        if struct.unpack_from('<I',data,at)[0]!=0xffffffff or struct.unpack_from('<I',data,at+8)[0] not in names:continue
        if struct.unpack_from('<I',data,at+48+24)[0]!=expected_count:continue
        out=[]
        for ei in range(expected_count):
            effect=at+176+ei*32
            frag=base+struct.unpack_from('<I',data,effect+4)[0]
            count=struct.unpack_from('<H',data,effect+18)[0]
            if not 0<count<2000:break
            ids=set()
            for _ in range(count):
                n=data[frag+3]
                assert n<128 and frag+4+2*n<len(data)
                ids.update(data[frag+4:frag+4+2*n:2]);frag+=4+2*n
            out.append(dict(matrix_ids=sorted(ids),texture_index=data[effect+26],fragment_count=count))
        if len(out)==expected_count:matches.append(out)
    assert len(matches)==1,(art_name,model_name,len(matches))
    return matches[0]

