"""Generate a fitted cinematic cloth adapter from locally extracted game data.

The generated GOAL file contains game geometry and belongs only in the private
build directory. This source module contains no copied cloth coordinates.
"""
import math
import struct
from pathlib import Path

BELT_DROP_METERS = -0.0380063653


def parse_dump(path):
    words, labels, types = [], {}, {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line.endswith(':') and line.startswith('L'):
            labels[line[:-1]] = len(words)
        elif line.startswith('.word '):
            value = line.split()[1]
            words.append(int(value, 16) if value.startswith('0x') else value)
        elif line.startswith('.type '):
            types[len(words)] = line.split()[1]
            words.append(line)
        elif line.startswith('.symbol '):
            words.append(line)
    return words, labels, types


def generate(dump, output):
    words, labels, types = parse_dump(dump)
    skirt = next(i for i, kind in types.items() if kind == 'art-cloth-geo')
    def string(label):
        at = labels[label]
        count = words[at]
        return b''.join(struct.pack('<I', x) for x in words[at+1:at+1+(count+3)//4]).split(b'\0')[0].decode()
    if 'skirt' not in string(words[skirt+2]) or words[skirt+3] != 117:
        raise ValueError('Unsupported cinematic skirt mesh')
    def floats(values):
        result = [struct.unpack('<f', struct.pack('<I', x))[0] for x in values]
        if not all(math.isfinite(x) for x in result):
            raise ValueError('Invalid cinematic cloth coordinate')
        return result
    shift = BELT_DROP_METERS * 4096
    lines = ['(in-package goal)',
             '(define cinematic-original-cloth-setup (method-of-type cloth-on-skeleton setup-from-params!))',
             '(defun cinematic-fit-skirt! ((m art-cloth-geo))']
    for i in range(117):
        row = floats(words[skirt+20+8*i:skirt+26+8*i])
        if row[3] != 1.0:
            raise ValueError('Unsupported cinematic cloth homogeneous coordinate')
        row[1] += shift
        for field, value in zip(('pt x', 'pt y', 'pt z', 'pt w', 'u', 'v'), row):
            lines.append(f'  (set! (-> m mesh {i} {field}) {value!r})')
    at = labels[words[skirt+12]]
    if words[at] != 26:
        raise ValueError('Unsupported cinematic skirt anchors')
    lines.append('  (set! (-> m anchor-transforms length) 26)')
    for i in range(26):
        p = at+3+8*i
        if string(words[p+4]) != 'hips' or words[p+5] >> 16 != i:
            raise ValueError('Unsupported cinematic skirt anchor mapping')
        row = floats(words[p:p+4])
        # The hip bind transform maps local -X to character +Y.
        row[0] -= shift
        for axis, value in zip('xyzw', row):
            lines.append(f'  (set! (-> m anchor-transforms data {i} offset {axis}) {value!r})')
        lines.extend([f'  (set! (-> m anchor-transforms data {i} constraint-index) {i})',
                      f'  (set! (-> m anchor-transforms data {i} joint) -1)'])
    at = labels[words[skirt+15]]
    if words[at] != 117:
        raise ValueError('Unsupported cinematic cloth thickness array')
    thickness = b''.join(struct.pack('<I', x) for x in words[at+3:at+33])[:117]
    for i, value in enumerate(thickness):
        lines.append(f'  (set! (-> m cloth-thickness data {i}) {value})')
    lines.extend([f'  (set! (-> m thickness-scalar) {floats([words[skirt+16]])[0]!r})',
                  '  (none))', '''
(defmethod setup-from-params! ((this cloth-on-skeleton) (params cloth-params) (owner handle))
  (let ((p (when (-> owner process)
             (let ((proc (-> (-> owner process))))
               (when (= (-> owner pid) (-> proc pid)) proc)))))
    (when (and p (type? p target) (= (-> params mesh) 10)
               (not (logtest? (-> this flags) (cloth-flag inited))))
      (let ((m (the-as art-cloth-geo (-> (the-as target p) draw art-group data 10))))
        (when (and m (type? m art-cloth-geo) (= (-> m num-points) 117)
                   (string= (-> m name) "jak-ext-geo-c+0-jakclod0-skirt")
                   (>= (-> m anchor-transforms allocated-length) 26))
          (cinematic-fit-skirt! m)
          ;; Params are shared and can retain the previous native count (34).
          ;; Extra zero-initialized pins otherwise pull the cloth to world zero.
          (set! (-> params num-anchor-points) 26)))))
  (cinematic-original-cloth-setup this params owner))'''])
    Path(output).write_text('\n'.join(lines)+'\n')
    return {'donor': 'jakc-highres-ag', 'points': 117, 'anchors': 26,
            'beltDropMeters': BELT_DROP_METERS, 'collisionSpheres': 'native gameplay',
            'sashAndScarf': 'unchanged'}


def append_object(archive, object_path, output, object_name='cinematic-skirt'):
    raw = Path(archive).read_bytes()
    name = object_name.encode('ascii')
    if not name or len(name) > 59:
        raise ValueError('Invalid archive object name')
    count, = struct.unpack_from('<I', raw)
    cursor = 64
    for _ in range(count):
        size, = struct.unpack_from('<I', raw, cursor)
        if raw[cursor+4:cursor+64].split(b'\0')[0] == name:
            raise ValueError('Cinematic skirt object already installed in input archive')
        cursor += 64 + ((size+15)&~15)
    if cursor != len(raw):
        raise ValueError('Invalid archive structure')
    obj = Path(object_path).read_bytes()
    if obj[:4] != b'GOAL':
        raise ValueError('Expected compiled GOAL object')
    result = bytearray(raw)
    struct.pack_into('<I', result, 0, count+1)
    result.extend(struct.pack('<I', len(obj))+name.ljust(60, b'\0')+obj+b'\0'*(-len(obj)%16))
    Path(output).write_bytes(result)
