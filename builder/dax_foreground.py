"""Add native Daxter effect controls to the pinned foreground source patch."""

SOURCE_EFFECT = [3, 0, 0, 2, 3, 1, 3]
MARKER = 'Cinematic Daxter: retain native visibility, alpha and fade controls.'


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Pinned foreground source did not match the Daxter patch anchor')
    return source.replace(before, after, 1)


def patch_source(source):
    if MARKER in source:
        raise ValueError('Daxter foreground patch has already been applied')
    source = replace_once(source,
        '(let ((merc-ctrl (-> dc lod-set lod (-> dc cur-lod) geo)))\n          ;; loop to:',
        '''(let* ((merc-ctrl (-> dc lod-set lod (-> dc cur-lod) geo))
                (pc-effect-count (if (string= (-> dc mgeo name) "daxter-lod0")
                                     7 (-> merc-ctrl header effect-count))))
          ;; loop to:''')
    source = replace_once(source,
        '''(when (or (not (string= (-> dc mgeo name) "jakb-c-lod0"))
                        (zero? (-> (-> *foreground* merc-bucket-info) effect effect-idx disable-draw)))''',
        '''(when (zero? (-> (-> *foreground* merc-bucket-info) effect effect-idx disable-draw))''')
    mapping = [f'          ;; {MARKER}',
               '          (when (string= (-> dc mgeo name) "daxter-lod0")',
               '            (let ((original-enable enable-mask) (original-alpha ignore-alpha-mask))',
               '              (set! enable-mask 0)',
               '              (set! ignore-alpha-mask 0)']
    for new, old in enumerate(SOURCE_EFFECT):
        mapping.append(f'              (when (logtest? original-enable {1 << old}) (logior! enable-mask {1 << new}))')
        mapping.append(f'              (when (logtest? original-alpha {1 << old}) (logior! ignore-alpha-mask {1 << new}))')
    mapping.append('              ))\n')
    source = replace_once(source, '          ;; flags\n', '\n'.join(mapping) + '\n          ;; flags\n')
    source = replace_once(source,
        '(set! (-> flags effect-count) (-> merc-ctrl header effect-count))',
        '(set! (-> flags effect-count) pc-effect-count)')
    fade_lines = ['              ((string= (-> dc mgeo name) "daxter-lod0")']
    for new, old in enumerate(SOURCE_EFFECT):
        fade_lines.append(f'               (set! (-> fades {new}) (the-as uint (-> (-> *foreground* merc-bucket-info) effect {old} color-fade)))')
    fade_lines[-1] += ')'
    source = replace_once(source,
        '''              (set! (-> fades 18) 0))
              (else''',
        '              (set! (-> fades 18) 0))\n' + '\n'.join(fade_lines) + '\n              (else')
    # Seven fades consume two quadwords. Pointers follow those two quadwords,
    # and the declared count also controls the renderer's packet decoding.
    allocation = '(let ((num-fades (/ (+ (-> merc-ctrl header effect-count) 3) 4)))'
    if source.count(allocation) != 2:
        raise ValueError('Pinned fade/pointer allocation anchors changed')
    source = source.replace(allocation, '(let ((num-fades (/ (+ pc-effect-count 3) 4)))')
    source = replace_once(source,
        '''            (dotimes (i (-> merc-ctrl header effect-count))
              (set! (-> merc-ptrs i) (-> merc-ctrl effect i))
              )''',
        '''            (dotimes (i pc-effect-count)
              (set! (-> merc-ptrs i)
                    (if (< i (-> merc-ctrl header effect-count))
                        (the-as object (-> merc-ctrl effect i))
                        (the-as object 0)))
              )''')
    return source
