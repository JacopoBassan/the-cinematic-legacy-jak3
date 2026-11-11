"""Generate cinematic eye resources from the user's extracted game data.

Generated GOAL and textures are private game assets. This module contains only
the adapter and extraction logic.
"""
import json
import struct
import sys
from pathlib import Path

SPECS = [
    ('kleiver', 'kleever-highres', 'desliz.fr3', 'klever-eye', 'klever-eyelid', ['kleever-rider-lod0']),
    ('ashelin', 'ashelin-highres', 'desbcst.fr3', 'ashelin-eye', 'ashelin-eyelid', ['ashelin-lod0']),
    ('jinx', 'jinx-highres', 'gridcst.fr3', 'jinx-iris', 'jinx-eyelid', ['jinx-lod0']),
    ('sig', 'sig-highres', 'arenacst.fr3', 'sig-eye', 'sig-eyelid', ['sig-rider-lod0', 'bt-sig-lod0']),
]

# Keep existing actors in their original texture banks when retiring a donor.
TEXTURE_BANKS = {'kleiver': 1, 'ashelin': 2, 'jinx': 3, 'sig': 4}


def with_automatic_blink(frame):
    """Preserve the authored resting lid while enabling native idle blinking.

    eye.gc uses a negative lid L as resting closure 1+L, then blends toward
    fully closed using the native random blink. Nonnegative lids encode direct
    closure and disable that blink. Convert only that sign convention, retaining
    every other packed control byte, including unequal gaze and pupil scales.
    """
    if type(frame) is not int or not 0 <= frame < 1 << 64:
        raise ValueError('Expected one unsigned packed eye frame')
    lid = struct.unpack('<b', bytes([(frame >> 16) & 255]))[0]
    if lid < 0:
        return frame
    return (frame & ~(255 << 16)) | ((lid - 128) & 255) << 16


def eye_data(raw_path):
    raw_path = Path(raw_path)
    data = Path(raw_path).read_bytes()
    if struct.unpack_from('<H', data, 8)[0] != 5:
        raise ValueError('Unsupported art-group format')
    base = struct.unpack_from('<I', data, 4)[0]
    def u32(at): return struct.unpack_from('<I', data, at)[0]
    def name(ptr):
        at = base + ptr
        size = u32(at)
        if not 0 < size < 256: raise ValueError('Invalid art element name')
        return data[at+4:at+4+size].decode('ascii')
    merc = base + u32(base+36) - 4
    ctrl = base + u32(merc+112)
    if not base < ctrl < len(data)-496:
        raise ValueError('Missing cinematic eye controller')
    shaders = list(struct.unpack_from('<60Q', data, ctrl+16))
    candidates = []
    for i in range(3, u32(base+12)):
        at = base + u32(base+32+4*i) - 4
        if not base <= at <= len(data)-32: continue
        label = name(u32(at+8))
        ep = u32(at+28)
        if ep in (0, 0xffffffff) or not 'idle' in label: continue
        maximum = struct.unpack_from('<h', data, base+ep)[0]
        if not 0 <= maximum < 2000: raise ValueError('Invalid eye animation')
        frames = struct.unpack_from('<'+str((maximum+1)*2)+'Q', data, base+ep+8)
        candidates.append((label, frames))
    if not candidates: raise ValueError('No authored idle eye pose')
    # Prefer the neutral idle. A single pose still receives engine blinking;
    # do not invent gaze or borrow a conversational expression from a table.
    label, frames = min(candidates, key=lambda x: (x[0] != raw_path.stem.removesuffix('-ag')+'-idle', len(x[0])))
    return shaders, frames, label


def generate(data_dir, output, fr3_dir):
    sys.path.insert(0, str(Path(__file__).parent/'stages'))
    import build_npc_expansion as b
    data_dir, output, fr3_dir = map(Path, (data_dir, output, fr3_dir))
    catalog = {}
    for filename in ('npc-build-catalog.json', 'additional-npc-catalog.json'):
        for m in json.loads((Path(__file__).parent/'stages'/filename).read_text())['models']:
            catalog[m['name']] = m
    lines = ['(in-package goal)',
        '(defun cinematic-eye-cleanup ()',
        '  (dotimes (i 32)',
        '    (let ((ctrl (-> *eye-control-array* data i)))',
        '      (when (and (!= (-> ctrl level-index) -1)',
        '                 (or '+ ' '.join(f'(string-charp= "cinematic-{s[0]}" (-> ctrl art-group-name))' for s in SPECS)+')',
        '                 (or (not (-> ctrl process process))',
        '                     (!= (-> ctrl process pid) (-> (-> ctrl process process) 0 pid))))',
        '        (free-eye-index i))))',
        '  (none))']
    reports = []
    dispatch = []
    for key, art, filename, iris, lid, actors in SPECS:
        si = TEXTURE_BANKS[key]
        textures, _ = b.textures(b.decompress(data_dir/'out/jak3/fr3'/filename))
        selected = [next(t for t in textures if t['name'] == n) for n in (iris, lid)]
        for texture in selected:
            if texture['width'] not in (64, 128) or texture['height'] not in (64, 128):
                raise ValueError('Unsupported cinematic eye texture dimensions')
            if len(texture['pixels']) != texture['width'] * texture['height'] * 4:
                raise ValueError('Incomplete cinematic eye texture')
        shaders, frames, animation = eye_data(data_dir/'decompiler_out/jak3/raw_obj'/(art+'-ag.go'))
        # One-frame idle tracks need automatic blinks, but resetting their lid
        # to -1 erases the donor's resting squint. Preserve its closure baseline.
        authored_lids = [struct.unpack('<b', bytes([(frame >> 16) & 255]))[0] / 128
                         for frame in frames] if len(frames) == 2 else None
        if len(frames) == 2:
            frames = tuple(with_automatic_blink(frame) for frame in frames)
        pages = {}
        for ti, texture in enumerate(selected):
            combo = texture['combo']; page, idx = combo >> 16, combo & 65535
            pages.setdefault(page, []).append((idx, texture, ti))
        for page, items in pages.items():
            length = max(i for i, _, _ in items)+1
            pn = f'cin-{key}-page-{page}'
            lines.append(f'(define {pn} (new \'static \'texture-page :name "{items[0][1]["page"]}" :id {page} :length {length} :data (new \'static \'array texture {length} '+ ' '.join(['#f']*length)+')))')
            for idx, texture, ti in items:
                # These are PC texture-pool bindings, not pixel buffers or PS2
                # DMA allocation. The pinned v0.3.8 native allocator uses the
                # lower bank. PC slots above physical PS2 VRAM (0x4000 blocks)
                # are not needed; this bank fits the 14-bit TEX0 field.
                tbp = 0x3000+si*8+ti*4
                lines.append(f'(set! (-> {pn} data {idx}) (new \'static \'texture :w {texture["width"]} :h {texture["height"]} :num-mips 1 :tex1-control 9 :psm 19 :name "{texture["name"]}" :dest (new \'static \'array uint16 7 {tbp} 0 0 0 0 0 0) :width (new \'static \'array uint8 7 2 0 0 0 0 0 0)))')
        for suffix, values in [('shaders', shaders), ('frames', frames)]:
            lines.append(f'(define cin-{key}-{suffix} (new \'static \'array uint64 {len(values)} '+' '.join(f'#x{x:x}' for x in values)+'))')
        for i in range(6):
            shader = f'(-> (the-as (inline-array adgif-shader) cin-{key}-shaders) {i})'
            if i % 3 == 1:
                lines.append(f'(adgif-shader-login-no-remap {shader})')
            else:
                texture = selected[0 if i % 3 == 0 else 1]
                combo = texture['combo']
                lines.append(f'(logclear! (-> {shader} link-test) (link-test-flags needs-log-in bit-9))')
                lines.append(f'(adgif-shader<-texture! {shader} (-> cin-{key}-page-{combo>>16} data {combo&65535}))')
        lines.extend([f'(define cin-{key}-clock 0.0)', f'(defun cin-{key}-update ((p process-drawable))',
            '  (let ((previous (-> *kernel-context* login-level-index)) (index 0))',
            '    (set! (-> *kernel-context* login-level-index) (the-as int (-> p draw level-index)))',
            f'    (set! index (find-free-eye-index (the-as int (-> p draw default-texture-page)) "cinematic-{key}" 60))',
            '    (set! (-> *kernel-context* login-level-index) previous)',
            '    (logior! (-> p skel status) (joint-control-status eye-anim))',
            '    (let ((ctrl (-> *eye-control-array* data index)))',
            "      (set! (-> ctrl process) (new 'static 'handle :process (-> p ppointer) :pid (-> p pid)))",
            f'      (set! (-> ctrl shaders) (the-as (inline-array adgif-shader) cin-{key}-shaders))',
            '      (set! (-> ctrl bucket) (the-as uint (vu1-bucket-map (the-as int (-> *level* draw-index-map (-> p draw level-index))) (the-as int (-> p draw default-texture-page)) (merc-mode texture))))',
            f'      (+! cin-{key}-clock (* 30.0 (-> p clock seconds-per-frame)))',
            f'      (let ((frame (mod (the int cin-{key}-clock) {len(frames)//2})))',
            f'        (convert-eye-data (-> ctrl left) (-> cin-{key}-frames (* frame 2)))',
            f'        (convert-eye-data (-> ctrl right) (-> cin-{key}-frames (+ 1 (* frame 2)))))'])
        lines.append('      ))')
        for page in pages:
            lines.append(f'  (__pc-texture-upload-now cin-{key}-page-{page} (tex-upload-mode seg0-1-2))')
        lines.append('  (none))')
        for actor in actors:
            dispatch.append(f'    ((string= name "{actor}") (cin-{key}-update p))')
        touched = set(f for a in actors for f in catalog[a]['files'])
        for name in touched:
            path = fr3_dir/name
            raw = b.decompress(path); tx, end = b.textures(raw)
            missing = [t for t in selected if not any(t['combo'] == x['combo'] and t['pixels'] == x['pixels'] for x in tx)]
            for t in missing:
                if any(t['combo'] == x['combo'] for x in tx):
                    raise ValueError('Cinematic eye texture ID collision')
            reader = b.Reader(raw, 2); reader.take(reader.num('Q')); start = reader.pos
            raw = raw[:start]+struct.pack('<Q',len(tx)+len(missing))+b''.join(b.texture_bytes(t) for t in tx+missing)+raw[end:]
            b.compress(path, raw)
        reports.append(dict(character=key, animation=animation, frames=len(frames)//2, files=sorted(touched), textures=[t['name'] for t in selected],
                            automaticBlink=len(frames) == 2,
                            authoredLids=authored_lids,
                            restingLidClosure=[1 + lid if lid < 0 else lid for lid in authored_lids] if authored_lids is not None else None))
    lines += ['(defun cinematic-eye-anim ((p process-drawable))',
        '  (cinematic-eye-cleanup)',
        '  (when (and (-> p draw mgeo) (nonzero? (the-as uint (-> p draw mgeo))))',
        '  (let ((name (-> p draw mgeo name)))', '  (cond']+dispatch+[
        '    (else (when (logtest? (-> p skel status) (joint-control-status eye-anim-valid eye-anim)) (merc-eye-anim p))))))', '  (none))']
    output.write_text('\n'.join(lines)+'\n')
    return reports


def patch_source(source):
    old = '''    (if (logtest? (-> this skel status) (joint-control-status eye-anim-valid eye-anim))
        (merc-eye-anim this)
        )'''
    if source.count(old) != 1:
        raise ValueError('Unexpected process-drawable eye dispatch')
    return source.replace('(in-package goal)', '(in-package goal)\n(define-extern cinematic-eye-anim (function process-drawable none))', 1).replace(old, '    (cinematic-eye-anim this)', 1)


