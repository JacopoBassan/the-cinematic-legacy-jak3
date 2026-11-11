"""Submit all allocated joint matrices for the selected NPC replacements."""
NAMES=('king-rider-lod0','ashelin-lod0','sig-rider-lod0','bt-sig-lod0','jinx-lod0','crimson-guard-lod0',
       'errol-lowres-lod0','errol-lowres-lod1','kleever-rider-lod0')

def patch_source(source):
    anchor='''          ;; The replacement may weight joints absent from the original visible fragments.
          ;; Use the same allocated matrix count as foreground-draw, not a guessed bone count.
          (when cinematic-replacement?'''
    if source.count(anchor)!=1:
        raise ValueError('NPC matrix submission patch anchor changed')
    predicate='(or cinematic-replacement? '+ ' '.join(f'(string= (-> dc mgeo name) "{name}")' for name in NAMES)+')'
    return source.replace(anchor,anchor.rsplit('cinematic-replacement?',1)[0]+predicate,1)
