"""Configure NPC controls for the cinematic replacements.

The native rider model has eye draw slots and authored eye pose data, but its
initializer never enables eye-anim. OpenGOAL therefore cannot generate the eye
textures. The Damas patch changes only that flag on the newly initialized rider.
Jinx's shoulder fit runs after joint evaluation while keeping elbow and hand
matrices unchanged.
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


_JINX_ANCHOR = '''          (if (-> jcontrol postbind-function)
              ((-> jcontrol postbind-function) this joints jcontrol)
              )
          (let ((a1-5 (new 'stack-no-clear 'vector)))'''

_JINX_INSERTION = '''          ;; Fit the cinematic Jinx shoulders without changing hand joints.
          (when (and (-> this process) (-> this mgeo) joints jcontrol
                     (= (-> this process draw) this)
                     (= (-> this process skel) jcontrol)
                     (= (-> this process node-list) joints)
                     (-> this process root)
                     (string= (-> this mgeo name) "jinx-lod0")
                     (= (-> this mgeo num-joints) 32)
                     (= (-> joints length) 35))
            (let ((chest (-> joints data 5 joint))
                  (left (-> joints data 8 joint))
                  (right (-> joints data 11 joint))
                  (scale (-> this process root scale)))
              (when (and chest left right
                         (-> joints data 5 bone)
                         (-> joints data 8 bone)
                         (-> joints data 11 bone)
                         (= (-> chest number) 4)
                         (= (-> left number) 7)
                         (= (-> right number) 10)
                         (name= (-> chest name) "chest")
                         (name= (-> left name) "Lshoulder")
                         (name= (-> right name) "Rshoulder")
                         (= (-> left parent) chest)
                         (= (-> right parent) chest)
                         (= (-> joints data 8 parent) (-> joints data 5))
                         (= (-> joints data 11 parent) (-> joints data 5))
                         (>= (-> scale x) 0.01) (<= (-> scale x) 100.0)
                         (<= (fabs (- (-> scale x) (-> scale y))) 0.0001)
                         (<= (fabs (- (-> scale x) (-> scale z))) 0.0001))
                (let ((delta (new 'stack-no-clear 'vector))
                      (left-bone (-> joints data 8 bone transform))
                      (right-bone (-> joints data 11 bone transform)))
                  (vector-copy! delta (-> joints data 5 bone transform rvec))
                  (set! (-> delta w) 0.0)
                  (when (and (> (vector-length delta) 0.000001)
                             (< (vector-length delta) 1000000.0))
                    ;; 64 mm in model units, scaled with the actor.
                    (vector-normalize! delta (* 262.144 (-> scale x)))
                    (-! (-> left-bone trans x) (-> delta x))
                    (-! (-> left-bone trans y) (-> delta y))
                    (-! (-> left-bone trans z) (-> delta z))
                    (+! (-> right-bone trans x) (-> delta x))
                    (+! (-> right-bone trans y) (-> delta y))
                    (+! (-> right-bone trans z) (-> delta z)))))))
'''


def patch_jinx_shoulders(source: str) -> str:
    """Insert the guarded Jinx fit after the native postbind callback."""
    if source.count(_JINX_ANCHOR) != 1 or _JINX_INSERTION in source:
        raise ValueError('Jinx shoulder source does not match the supported original')
    marker = "          (let ((a1-5 (new 'stack-no-clear 'vector)))"
    patched = source.replace(_JINX_ANCHOR, _JINX_ANCHOR.replace(marker, _JINX_INSERTION + marker), 1)
    if patched.replace(_JINX_INSERTION, '', 1) != source:
        raise ValueError('Jinx shoulder patch changed unrelated source')
    return patched
