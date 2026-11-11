"""Private continuous Dark Jak authored vertex-palette prototype.

This requires the paired marker-only FR3 patch. It uses an otherwise unused
vertex padding byte and a new, explicitly uploaded metadata matrix. Real joint
matrices, vertex colors, weights, alpha, and material controls remain unchanged.
"""
from hashlib import sha256

MERC_SHADER_SHA256 = "cd5f5e182bccf0b8a4d084e0843a49d7452e20d8cdee2a2ec793188bf3277d32"
MARKER = 0xD3
METADATA_INDEX = 68
MAGIC = 4573.125
MAGIC2 = -1937.75
FOREGROUND_MARKER = "Cinematic Dark Jak: per-draw authored vertex palette metadata."


def replace_once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError(f"Pinned source anchor changed: {anchor[:100]!r}")
    return source.replace(anchor, replacement, 1)


def patch_foreground(source):
    """Apply after the existing core, Daxter, and NPC foreground patches."""
    if FOREGROUND_MARKER in source:
        raise ValueError("Dark Jak metadata patch already applied")
    source = replace_once(source,
        '''        (qwc-total 0)
        (cinematic-replacement?''',
        '''        (qwc-total 0)
        (cinematic-color-meta? (string= (-> dc mgeo name) "jakb-c-lod0"))
        (cinematic-color-meta-slot (the-as (pointer pointer) 0))
        (cinematic-replacement?''')
    source = replace_once(source,
        '          ;; end matrix string\n',
        f'''          ;; {FOREGROUND_MARKER}
          ;; Slot {METADATA_INDEX} is beyond every real weighted matrix. The pointed data is
          ;; appended to this same DMA packet below, so it remains valid per draw.
          (when cinematic-color-meta?
            (set! (-> matrix-slot-string matrix-out-idx) {METADATA_INDEX})
            (+! matrix-out-idx 1)
            (set! cinematic-color-meta-slot (the-as (pointer pointer) dma-buf))
            (&+! dma-buf 16)
            (+! qwc-total 1))

          ;; end matrix string
''')
    source = replace_once(source,
        '''    (set! (-> start-packet dma) (new 'static 'dma-tag :id (dma-tag-id cnt) :qwc qwc-total))
    dma-buf''',
        f'''    ;; The native PC renderer reads 112 bytes for each matrix pointer. Keep
    ;; this payload after all normal fields, including native merc pointers.
    (when cinematic-color-meta?
      (let ((metadata (the-as (pointer float) dma-buf)))
        (dotimes (i 28) (set! (-> metadata i) 0.0))
        (set! (-> metadata 0) {MAGIC})
        (when (and (-> dc process) (nonzero? (the-as uint (-> dc process)))
                   (type? (-> dc process) target))
          (set! (-> metadata 1) (-> (the-as target (-> dc process)) darkjak-interp)))
        (set! (-> metadata 2) {MAGIC2})
        (set! (-> cinematic-color-meta-slot) dma-buf)
        (&+! dma-buf 112)
        (+! qwc-total 7)))

    (set! (-> start-packet dma) (new 'static 'dma-tag :id (dma-tag-id cnt) :qwc qwc-total))
    dma-buf''')
    return source


def gameplay_hair_texture_scale(native, cinematic):
    """Measure the owned dark textures, keeping cinematic spatial detail."""
    import hashlib
    import numpy as np
    from fr3_models import decompress
    from inspect_transformations import textures
    rows=[]
    for path,name in ((native,'jakc-hair-dark'),(cinematic,'jakchires-hair-dark')):
        items,_=textures(decompress(path))
        item=next(t for t in items if t['name']==name)
        pixels=np.frombuffer(item['pixels'],'u1').reshape(-1,4)
        mean=np.average(pixels[:,:3].astype(float),axis=0,weights=pixels[:,3].astype(float))
        if not np.isfinite(mean).all() or not (mean>0).all():
            raise ValueError('Invalid dark hair texture statistics')
        rows.append(dict(name=name,rgbaSha256=hashlib.sha256(item['pixels']).hexdigest(),
                         alphaWeightedMeanRGB=mean.tolist()))
    scale=np.array(rows[0]['alphaWeightedMeanRGB'])/np.array(rows[1]['alphaWeightedMeanRGB'])
    if not ((scale>0)&(scale<1)).all():
        raise ValueError('Expected darker gameplay hair than cinematic hair')
    return dict(method='Alpha-weighted gameplay/cinematic dark texture RGB mean ratio',
                textures=rows,scale=scale.tolist(),yellowTipsExcludedFromGrayScale=True,
                normalAndFacialHairUnchanged=True)


def gameplay_yellow_tip_scale(native, cinematic):
    """Match owned warm-tip luminance without erasing cinematic yellow placement."""
    import numpy as np
    from fr3_models import decompress, model, DRAW, Reader
    from inspect_faces import buffers
    from inspect_transformations import textures

    def measure(path, slot, texture_name, native_tips):
        raw = decompress(path)
        item = model(raw, 'jakb-c-lod0')
        _, indices, _, vertex_bytes = buffers(raw, 'jakb-c-lod0')
        vertices = np.frombuffer(vertex_bytes, 'u1').reshape(-1, 64)
        ids = set()
        for effect in item['effects']:
            reader = Reader(effect['mod'])
            reader.draws()
            fixed = reader.draws()
            for draw in effect['draws'] + fixed:
                values = DRAW.unpack(draw)
                if values[1] == slot:
                    if values[0] != 0xf90e265d:
                        raise ValueError('Unsupported owned hair sampler or draw mode')
                    ids.update(int(i) for i in indices[values[3]:values[3]+values[4]]
                               if i != 0xffffffff)
        if not ids or min(ids) < 0 or max(ids) >= len(vertices):
            raise ValueError('Invalid owned hair draw indices')
        chosen = vertices[sorted(ids)].copy()
        rgb = chosen[:, 56:59].astype(float)
        if native_tips:
            selected = ((rgb[:, 2] < rgb.max(1)*0.8)
                        & (rgb.max(1)-rgb.min(1) > 10))
        else:
            selected = (rgb[:, 0] > rgb[:, 1]) & (rgb[:, 2] < rgb[:, 0]*0.25)
        if not selected.any():
            raise ValueError('Owned warm hair tips missing')
        uv = chosen.view('<f4').reshape(-1, 16)[selected, 12:14]
        if not np.isfinite(uv).all():
            raise ValueError('Invalid owned hair UVs')
        texture_items, _ = textures(raw)
        texture = next(t for t in texture_items if t['name'] == texture_name)
        pixels = np.frombuffer(texture['pixels'], 'u1').reshape(
            texture['height'], texture['width'], 4).astype(float)
        # Both source hair materials use bilinear filtering and repeat wrapping.
        xy = uv * [texture['width'], texture['height']] - 0.5
        base = np.floor(xy).astype(int)
        fraction = xy-base
        sampled = np.zeros((len(uv), 4))
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            weight = ((fraction[:, 0] if dx else 1-fraction[:, 0])
                      * (fraction[:, 1] if dy else 1-fraction[:, 1]))
            sampled += pixels[(base[:, 1]+dy) % texture['height'],
                              (base[:, 0]+dx) % texture['width']] * weight[:, None]
        surface = rgb[selected]/128.0 * sampled[:, :3]
        if not (sampled[:, 3] > 0).any():
            raise ValueError('Owned warm tips have no visible texels')
        luminance = float(np.average(surface @ np.array([0.2126, 0.7152, 0.0722]),
                                    weights=sampled[:, 3]))
        if not np.isfinite(luminance) or luminance <= 0:
            raise ValueError('Invalid warm-tip luminance')
        return dict(texture=texture_name, rgbaSha256=sha256(texture['pixels']).hexdigest(),
                    selectedVertices=int(selected.sum()),
                    alphaWeightedSurfaceLuminance=luminance)

    original = measure(native, -6, 'jakc-hair-dark', True)
    replacement = measure(cinematic, -13, 'jakchires-hair-dark', False)
    scale = (original['alphaWeightedSurfaceLuminance']
             / replacement['alphaWeightedSurfaceLuminance'])
    if not 0 < scale <= 1:
        raise ValueError('Expected brighter cinematic yellow tips than gameplay')
    return dict(method='Alpha-weighted bilinear dark texture x vertex Rec.709 luminance ratio',
                native=original, cinematic=replacement, scale=scale,
                yellowPlacementAndHuePreserved=True, grayAndNormalHairUnchanged=True)


def patch_shader(original, palette=None, hair_texture_scale=None, yellow_tip_scale=1.0):
    """Require exact pinned shader bytes, return the modified shader bytes."""
    if sha256(original).hexdigest() != MERC_SHADER_SHA256:
        raise ValueError("Unsupported merc2.vert input hash")
    source = original.decode("utf-8").replace("\r\n", "\n")
    palette=palette or [(1.0,1.0,1.0)]
    if not 1 <= len(palette) <= 256-MARKER:
        raise ValueError('Invalid native hair palette length')
    import math
    if any(len(row)!=3 or any(not math.isfinite(c) or not 0<=c<=1 for c in row) for row in palette):
        raise ValueError('Invalid native hair chromaticity')
    entries=', '.join('vec3('+', '.join(f'{c:.9f}' for c in row)+')' for row in palette)
    hair_texture_scale=(1.0,1.0,1.0) if hair_texture_scale is None else hair_texture_scale
    if len(hair_texture_scale)!=3 or any(not math.isfinite(c) or not 0<c<=1 for c in hair_texture_scale):
        raise ValueError('Invalid gameplay hair texture scale')
    if not math.isfinite(yellow_tip_scale) or not 0 < yellow_tip_scale <= 1:
        raise ValueError('Invalid gameplay yellow tip scale')
    scale='vec3('+', '.join(f'{c:.9f}' for c in hair_texture_scale)+')'
    source = replace_once(source,
        "layout (location = 5) in uvec3 mats;",
        "layout (location = 5) in uvec4 mats;")
    source = replace_once(source,
        "  vtx_color = rgba * light_color;",
        f'''  // Only the mod's authored hair/eyebrow vertices carry this padding marker.
  // Merc2 already supplies four unsigned bytes at location 5. The first three
  // retain their native joint indices; the fourth does not skin the vertex.
  vec4 authored_rgba = rgba;
  if (mats.w >= {MARKER}u && mats.w < {MARKER+len(palette)}u) {{
    vec4 metadata = bones[{METADATA_INDEX}].X[0];
    if (metadata.x == {MAGIC} && metadata.z == {MAGIC2}) {{
      float dark_amount = clamp(metadata.y, 0.0, 1.0);
      // Palette generated locally from the owned gameplay hair. Preserve its
      // warm tips while removing the cinematic model's excessive green tint.
      float dark_gray = max(rgba.r, max(rgba.g, rgba.b));
      const vec3 native_palette[{len(palette)}] = vec3[]({entries});
      // The cinematic mesh already marks its yellow tips in vertex RGB.
      // Nearest gameplay palette transfer must not whiten those authored tips.
      bool authored_yellow = rgba.r > rgba.g && rgba.b < rgba.r * 0.25;
      vec3 dark_color = authored_yellow ? rgba.rgb : dark_gray * native_palette[mats.w - {MARKER}u];
      // The first palette code belongs to facial hair. Only scalp hair uses
      // the owned gameplay dark texture's color and brightness; retain its
      // higher-resolution cinematic texture detail.
      if (mats.w > {MARKER}u && !authored_yellow) dark_color *= {scale};
      // Match native warm-tip brightness without changing their hue or placement.
      if (mats.w > {MARKER}u && authored_yellow) dark_color *= {yellow_tip_scale:.9f};
      authored_rgba.rgb = mix(rgba.rgb, dark_color, dark_amount);
    }}
  }}
  vtx_color = authored_rgba * light_color;''')
    return source.encode("utf-8")


def validate_protocol(hair_texture_scale=None, yellow_tip_scale=1.0):
    """Check normal, transition, and Dark endpoints for each marker class."""
    import math
    scale = (1.0, 1.0, 1.0) if hair_texture_scale is None else hair_texture_scale
    if len(scale) != 3 or any(not math.isfinite(c) or not 0 < c <= 1 for c in scale):
        raise ValueError('Invalid gameplay hair texture scale')
    if not math.isfinite(yellow_tip_scale) or not 0 < yellow_tip_scale <= 1:
        raise ValueError('Invalid gameplay yellow tip scale')
    samples = [(57, 94, 42, 128), (128, 113, 15, 128),
               (116, 101, 1, 128), (121, 113, 8, 128),
               (20, 57, 9, 128), (31, 78, 29, 128), (128, 123, 119, 128)]
    checks = 0
    for rgba in samples:
        gray = max(rgba[:3])
        yellow = rgba[0] > rgba[1] and rgba[2] < rgba[0] * 0.25
        for palette in ((1.0, 1.0, 1.0), (1.0, 1.0, 0.5)):
            for marker in (0, MARKER, MARKER + 1):
                target = rgba[:3] if yellow or marker == 0 else tuple(gray * c for c in palette)
                if marker > MARKER and not yellow:
                    target = tuple(c * s for c, s in zip(target, scale))
                if marker > MARKER and yellow:
                    target = tuple(c * yellow_tip_scale for c in target)
                for amount in (0.0, 0.25, 0.5, 0.75, 1.0):
                    result = tuple(c * (1 - amount) + t * amount for c,t in zip(rgba[:3],target)) + (rgba[3],)
                    assert result[3] == rgba[3]
                    assert amount != 0.0 or result == rgba
                    assert amount != 1.0 or result[:3] == target
                    assert not (marker == 0 or (marker == MARKER and yellow)) or result[:3] == rgba[:3]
                    assert all(min(c,t) <= v <= max(c,t) for c,t,v in zip(rgba[:3],target,result[:3]))
                    checks += 1
                if marker == MARKER and not yellow:
                    assert target == tuple(gray * c for c in palette)
    return {"metadata_index": METADATA_INDEX, "marker": MARKER,
            "input_shader_sha256": MERC_SHADER_SHA256,
            "metadata_bytes": 112, "additional_dma_bytes": 128,
            "magic": [MAGIC, MAGIC2], "sample_palette_checks": checks,
            "hair_texture_scale": list(scale),
            "yellow_tip_scale": yellow_tip_scale,
            "normal_endpoint_preserved": True,
            "unmarked_vertices_preserved": True,
            "facial_hair_excluded_from_texture_scale": True,
            "authored_yellow_placement_and_hue_preserved": True}
