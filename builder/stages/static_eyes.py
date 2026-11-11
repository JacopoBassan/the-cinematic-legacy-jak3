"""Compose a neutral static eye from local game iris and pupil textures.

Matches EyeRenderer's layer order, linear sampling and eye.frag's doubled
PS2 alpha. The neutral scales are merc-eye-anim's no-animation defaults.
No texture pixels are embedded in this source.
"""
import numpy as np

def compose(iris, pupil, name='cinematic-kleiver-neutral-eye', combo=0xfffe0001,
            iris_scale=0.55, pupil_scale=0.45):
    size=64
    coordinate=(np.arange(size,dtype=np.float64)+0.5)/size
    x,y=np.meshgrid(coordinate,coordinate)
    def layer(texture, scale):
        a=np.frombuffer(texture['pixels'],np.uint8).reshape(texture['height'],texture['width'],4)
        u=(x-0.5)/scale+0.5;v=(y-0.5)/scale+0.5
        inside=(u>=0)&(u<=1)&(v>=0)&(v<=1)
        sx=np.clip(u*texture['width']-0.5,0,texture['width']-1)
        sy=np.clip(v*texture['height']-0.5,0,texture['height']-1)
        x0=sx.astype(int);y0=sy.astype(int)
        x1=np.minimum(x0+1,texture['width']-1);y1=np.minimum(y0+1,texture['height']-1)
        dx=(sx-x0)[...,None];dy=(sy-y0)[...,None]
        sampled=(a[y0,x0]*(1-dx)+a[y0,x1]*dx)*(1-dy)+(a[y1,x0]*(1-dx)+a[y1,x1]*dx)*dy
        return sampled,inside
    result=np.empty((size,size,4),np.float64)
    result[:]=np.frombuffer(iris['pixels'],np.uint8)[:4]
    first,mask=layer(iris,iris_scale);result[mask]=first[mask]
    second,mask=layer(pupil,pupil_scale)
    alpha=np.minimum(second[...,3:4]*2/255,1)*mask[...,None]
    result[...,:3]=second[...,:3]*alpha+result[...,:3]*(1-alpha)
    result[...,3]=128
    out=dict(iris,width=size,height=size,pixels=np.rint(result).astype(np.uint8).tobytes(),
             name=name,page='cinematic-mod-generated',combo=combo)
    assert result[size//2,size//2,:3].max()<80, 'Neutral pupil must be dark'
    return out
