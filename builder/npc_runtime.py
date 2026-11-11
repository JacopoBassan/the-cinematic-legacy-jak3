"""Enable Damas's existing gameplay eye controller at actor initialization.

The native rider model has eye draw slots and authored eye pose data, but its
initializer never enables eye-anim. OpenGOAL therefore cannot generate the eye
textures. This patch changes only that flag on the newly initialized rider.
"""

SOURCE_RELATIVE = 'levels/stadium/king-rider.gc'
OBJECT_NAME = 'king-rider'
ARCHIVE_NAME = 'LPATK.DGO'

_ANCHOR = '''  (initialize-skeleton
    self
    (the-as skeleton-group (art-group-get-by-name *level* "skel-king-rider" (the-as (pointer level) #f)))
    (the-as pair 0)
    )
  (logclear! (-> self mask) (process-mask actor-pause))'''

_INSERTION = '''  ;; Enable the rider's existing eye pose data and generated eye textures.
  (logior! (-> self skel status) (joint-control-status eye-anim))
'''


def patch_source(source: str) -> str:
    """Apply one exact initialization change, rejecting ambiguous inputs.

    Using logior preserves every existing animation bit. Geometry visibility,
    draw masks, joint allocation, collision and mission behavior stay intact.
    The public builder validates the complete original source tree separately.
    """
    if source.count(_ANCHOR) != 1 or _INSERTION in source:
        raise ValueError('Damas eye initialization source does not match the supported original')
    marker = '  (logclear! (-> self mask) (process-mask actor-pause))'
    replacement = _ANCHOR.replace(marker, _INSERTION + marker)
    patched = source.replace(_ANCHOR, replacement, 1)
    if patched.replace(_INSERTION, '', 1) != source:
        raise ValueError('Damas eye patch changed unrelated source')
    return patched
