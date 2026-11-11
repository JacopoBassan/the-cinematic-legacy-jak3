"""Preserve cinematic draw and reflection modes lost by the GLB importer."""
import json
from fr3_models import BASE,DRAW,decompress,compress,model,serialize

def main():
    path=BASE/'prototype/GAME-armor.fr3'
    raw=decompress(path)
    donor=decompress(BASE/'extraction/out/jak3/fr3/ARENACST.fr3')
    baseline=decompress(BASE/'prototype/GAME-baseline.fr3')
    updates=[]
    for low,high in [('jakb-c-lod0','jakc-highres-lod0'),('daxter-lod0','daxter-highres-lod0')]:
        target=model(raw,low)
        source=model(donor,high)
        # The replacement importer does not initialize native deformation decode
        # constants. Keep the gameplay contract instead of serializing heap data.
        target['tail']=target['tail'][:8]+model(baseline,low)['tail'][8:]
        if len(target['effects'])!=len(source['effects']):
            raise ValueError('Authored effect boundaries are required before draw-mode restoration')
        for effect,native in zip(target['effects'],source['effects']):
            if len(effect['draws'])!=len(native['draws']):
                raise ValueError('Authored draw boundaries changed')
            effect['draws']=[old[:4]+new[4:] for new,old in zip(effect['draws'],native['draws'])]
            effect['tail']=native['tail'][:4]+effect['tail'][4:]
        updates.append(target)
    for target in sorted(updates,key=lambda m:m['start'],reverse=True):
        raw=raw[:target['start']]+serialize(target)+raw[target['end']:]
    compress(path,raw)
    print('Restored authored draw and reflection modes for Jak and Daxter')

if __name__=='__main__':
    main()
