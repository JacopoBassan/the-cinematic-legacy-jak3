"""Render the dedicated Dark Jak model through the native gameplay draw path."""
from hashlib import sha256

MERC_SHADER_SHA256 = "cd5f5e182bccf0b8a4d084e0843a49d7452e20d8cdee2a2ec793188bf3277d32"
METADATA_INDEX = 68
MAGIC = 4573.125
MAGIC2 = -1937.75
FOREGROUND_MARKER = "Cinematic Dark Jak: per-draw transition blend."


def replace_once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError(f"Pinned source anchor changed: {anchor[:100]!r}")
    return source.replace(anchor, replacement, 1)


def patch_blend_metadata(source):
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


def render_name(name, process, replacement):
    return f'''(if (and *cinematic-dark-eye-mesh?* (= {process} *target*)
               (string= {name} "jakb-c-lod0"))
          "{replacement}"
          {name})'''


def patch_foreground(source, replacement):
    # Keep native draw control. Pass the same model name to both PC renderers.
    source = patch_blend_metadata(source)
    anchor = '(defun pc-merc-draw-request '
    source = replace_once(source, anchor, '(define *cinematic-dark-eye-mesh?* #f)\n\n' + anchor)
    path = '(-> dc mgeo name)'
    anchor = f'(charp<-string (the (pointer uint8) (&-> data-ptr 0)) {path})'
    name = render_name(path, '(-> dc process)', replacement)
    return replace_once(source, anchor, f'(charp<-string (the (pointer uint8) (&-> data-ptr 0)) {name})')


def patch_eye(source, replacement):
    # Snapshot before eye upload. The render pass must use the same name even
    # when the game advances the transformation before it submits the body.
    anchor = '(defun update-eyes ()'
    source = replace_once(source, anchor, f'''(define-extern *cinematic-dark-eye-mesh?* symbol)
{anchor}
  (set! *cinematic-dark-eye-mesh?*
    (if (and *target* (> (-> *target* darkjak-interp) 0.0)) #t #f))''')
    path = '(-> (the process-drawable v1-7) draw mgeo name)'
    anchor = f'(set! *current-eye-merc-ctrl-name* {path})'
    return replace_once(source, anchor, f'(set! *current-eye-merc-ctrl-name* {render_name(path, "v1-7", replacement)})')


def patch_shader(source, colors):
    if sha256(source).hexdigest() != MERC_SHADER_SHA256:
        raise ValueError('Original OpenGOAL Merc shader changed')
    if not colors or len(colors) > 255:
        raise ValueError('Invalid Dark Jak transition palette')
    source = source.decode('utf-8')
    source = replace_once(source, 'layout (location = 5) in uvec3 mats;',
                          'layout (location = 5) in uvec4 mats;')
    original_colors = ', '.join('vec3(' + ', '.join(str(int(value)) + '.0' for value in rgb) + ')'
                                for rgb in colors)
    return replace_once(source, '  vtx_color = rgba * light_color;', f'''  vec4 model_color = rgba;
  // The Dark mesh retains its cutscene colors. During the transformation,
  // hair and facial hair use the same blend fraction as the native textures.
  if (mats.w > 0u && mats.w <= {len(colors)}u) {{
    vec4 blend = bones[{METADATA_INDEX}].X[0];
    if (blend.x == {MAGIC} && blend.z == {MAGIC2}) {{
      const vec3 regular_colors[{len(colors)}] = vec3[]({original_colors});
      model_color.rgb = mix(regular_colors[mats.w - 1u] / 255.0, rgba.rgb, clamp(blend.y, 0.0, 1.0));
    }}
  }}
  vtx_color = model_color * light_color;''').encode('utf-8')
