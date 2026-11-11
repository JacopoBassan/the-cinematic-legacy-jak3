"""Keep the native ground shadow below the cinematic Daxter mesh."""
from dark_vertex_runtime import replace_once

def patch_source(source):
    source=replace_once(source,'(define *cinematic-jak-shadow-clip* #t)',
        '(define *cinematic-jak-shadow-clip* #t)\n(define *cinematic-dax-shadow-clip* #t)\n(define *cinematic-dax-shadow-offset* 128.0)')
    anchor='                    (set! (-> t2-10 geo-ref dma)'
    return replace_once(source,anchor,'''                    ;; Daxter is shorter than Jak. Clip relative to his foot/root
                    ;; height, not the one-metre offset that puts his entire
                    ;; ground shadow below the floor. Keep native projection.
                    (when (and *cinematic-dax-shadow-clip*
                               (string= (-> arg0 mgeo name) "daxter-lod0")
                               (string= (-> v1-0 name) "daxter-shadow")
                               (not (logtest? (-> t2-10 settings flags) (shadow-flags shdf02)))
                               (-> arg0 skeleton))
                      (set! (-> t2-10 settings top-plane w)
                        (- (- (-> t2-10 settings center y)
                              (-> arg0 skeleton bones 0 transform trans y))
                           *cinematic-dax-shadow-offset*))
                      (logior! (-> t2-10 settings flags) (shadow-flags shdf03)))
'''+anchor)
