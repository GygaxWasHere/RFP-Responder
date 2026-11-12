"""Josef Frank / Svensk Tenn-inspired botanical SVG pattern, recoloured for Milestone/Apollo.

botanical(pattern_id, stem, leaf, flower, centre, berry, scale, opacity, leaf2=None, bird=True)
returns a hidden <svg><defs><pattern id=...> block; fill any rect with url(#pattern_id).

Proven palettes (2026-10 RFP Analyser mock-ups):
  dark wallpaper : botanical("p","#1C2326","#1C2326","#002F42","#005275","#B8A600",1.25,1,leaf2="#172024")  on #0D1012
  blue fabric    : botanical("p","#0076A8","#00648F","#0FB8FF","#0099DA","#FFE700",1.0,1,leaf2="#005275")   on #002F42
  light band     : botanical("p","#B9DDF3","#B9DDF3","#0099DA","#0C62AD","#0C62AD",0.8,0.6,leaf2="#D4EDFC") on #FFFFFF

Run directly to write pattern-demo.html next to this file.
"""
import math
def _bez(p0,p1,p2,p3,t):
    u=1-t
    x=u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0]
    y=u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]
    dx=3*u*u*(p1[0]-p0[0])+6*u*t*(p2[0]-p1[0])+3*t*t*(p3[0]-p2[0])
    dy=3*u*u*(p1[1]-p0[1])+6*u*t*(p2[1]-p1[1])+3*t*t*(p3[1]-p2[1])
    return x,y,math.degrees(math.atan2(dy,dx))
def _stem(p, n, t0, t1, leaf, size, alt=True):
    d=f'M{p[0][0]} {p[0][1]} C{p[1][0]} {p[1][1]},{p[2][0]} {p[2][1]},{p[3][0]} {p[3][1]}'
    out=''
    for i in range(n):
        t=t0+(t1-t0)*i/max(1,n-1); x,y,a=_bez(*p,t); side=(1 if i%2 else -1) if alt else 1
        ang=a+side*48; r=math.radians(ang); cx=x+math.cos(r)*size; cy=y+math.sin(r)*size
        out+=f'<path d="M{x:.1f} {y:.1f} Q{cx-math.sin(r)*size*.45:.1f} {cy+math.cos(r)*size*.45:.1f} {x+math.cos(r)*size*2:.1f} {y+math.sin(r)*size*2:.1f} Q{cx+math.sin(r)*size*.45:.1f} {cy-math.cos(r)*size*.45:.1f} {x:.1f} {y:.1f}z" fill="{leaf}"/>'
    return d,out
def _flower(cx,cy,r,n,c1,c2,c3):
    o=''.join(f'<ellipse cx="{cx}" cy="{cy-r*.62}" rx="{r*.36}" ry="{r*.62}" fill="{c1}" transform="rotate({i*360/n:.1f} {cx} {cy})"/>' for i in range(n))
    o+=''.join(f'<ellipse cx="{cx}" cy="{cy-r*.36}" rx="{r*.2}" ry="{r*.36}" fill="{c2}" transform="rotate({i*360/n+180/n:.1f} {cx} {cy})"/>' for i in range(n))
    o+=f'<circle cx="{cx}" cy="{cy}" r="{r*.2}" fill="{c3}"/>'
    o+=''.join(f'<circle cx="{cx+math.cos(i*1.257)*r*.12:.1f}" cy="{cy+math.sin(i*1.257)*r*.12:.1f}" r="{r*.04:.1f}" fill="{c2}"/>' for i in range(5))
    return o
def _bird(x,y,s,body,wing,flip=1):
    return f'''<g transform="translate({x} {y}) scale({s*flip} {s})"><path d="M-22 4 L-36 -6 L-34 8z" fill="{wing}"/><ellipse cx="0" cy="0" rx="22" ry="12" fill="{body}"/>
<circle cx="16" cy="-8" r="9" fill="{body}"/><path d="M24 -9 L32 -6 L24 -4z" fill="{wing}"/><path d="M-10 -2 Q4 -18 14 -2 Q2 6 -10 -2z" fill="{wing}"/><circle cx="18" cy="-10" r="1.8" fill="#fff"/></g>'''
def botanical(pid, stem, leaf, flower, centre, berry, scale=1.0, opacity=1.0, leaf2=None, bird=True):
    leaf2 = leaf2 or leaf
    S=320; s=S*scale
    a=[(0,300),(70,250),(40,150),(140,110)]; b=[(140,110),(230,75),(250,40),(320,20)]
    c=[(120,320),(150,260),(230,250),(260,180)]; d=[(60,175),(20,130),(30,80),(0,60)]
    e=[(205,82),(220,140),(280,130),(300,210)]
    paths='';leaves=''
    for p,n,sz,lf in [(a,7,13,leaf),(b,6,12,leaf2),(c,6,11,leaf2),(d,4,10,leaf),(e,4,9,leaf)]:
        dd,l=_stem(p,n,.08,.92,lf,sz); paths+=f'<path d="{dd}" fill="none" stroke="{stem}" stroke-width="2"/>'; leaves+=l
    fl=_flower(140,110,30,8,flower,centre,berry)+_flower(260,180,20,6,flower,centre,berry)+_flower(0,60,16,6,centre,flower,berry)+_flower(320,60+0,16,6,centre,flower,berry)
    tul=f'<path d="M300 210 q-16 -10 -12 -30 q8 8 12 8 q4 0 12 -8 q4 20 -12 30z" fill="{centre}"/>'
    ber=''.join(f'<circle cx="{x}" cy="{y}" r="4" fill="{berry}"/>' for x,y in [(96,200),(104,190),(90,188),(98,178),(184,300),(192,290),(178,288)])
    dots=''.join(f'<circle cx="{x}" cy="{y}" r="2" fill="{flower}"/>' for x,y in [(30,250),(200,20),(290,300),(110,40),(230,330-20)])
    br=_bird(212,234,.85,centre,flower,1) if bird else ''
    return f'''<svg width="0" height="0" style="position:absolute"><defs>
<pattern id="{pid}" width="{s}" height="{s}" patternUnits="userSpaceOnUse"><g transform="scale({scale})" opacity="{opacity}">
{paths}{leaves}{fl}{tul}{ber}{dots}{br}</g></pattern></defs></svg>'''

if __name__ == "__main__":
    import os
    p = botanical("p", "#1C2326", "#1C2326", "#002F42", "#005275", "#B8A600", 1.25, 1, leaf2="#172024")
    html = ('<!doctype html><body style="margin:0">' + p +
            '<svg width="100%" height="100%" style="position:fixed;inset:0"><rect width="100%" height="100%" fill="#0D1012"/>'
            '<rect width="100%" height="100%" fill="url(#p)"/></svg></body>')
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pattern-demo.html")
    open(out, "w", encoding="utf-8").write(html)
    print(out)
