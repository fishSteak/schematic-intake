import json, itertools, math
def brentq(fn,a,b,xtol=1e-18):
    fa=fn(a)
    for _ in range(100):
        m=(a+b)/2;fm=fn(m)
        if fm==0 or b-a < max(xtol,abs(m)*1e-14):return m
        if fa*fm<=0:b=m
        else:a=m;fa=fm
    return (a+b)/2
class NP:
    @staticmethod
    def linspace(a,b,n):return [a+(b-a)*i/(n-1) for i in range(n)]
    @staticmethod
    def geomspace(a,b,n):return [a*(b/a)**(i/(n-1)) for i in range(n)]
np=NP()
def mul(a,b):
    out=[0.]*(len(a)+len(b)-1)
    for i,x in enumerate(a):
        for j,y in enumerate(b):out[i+j]+=x*y
    return out
def add(a,b):return [(a[i] if i<len(a) else 0)+(b[i] if i<len(b) else 0) for i in range(max(len(a),len(b)))]
def pv(a,x):
    y=0.
    for c in a[::-1]:y=y*x+c
    return y
from pathlib import Path

def area(num, den):
    scan=[-x for x in np.geomspace(1e-5,1e12,300)]
    poles=[]
    for a,b in zip(scan[:-1],scan[1:]):
        if pv(den,a)*pv(den,b)<0:poles.append(brentq(lambda x:pv(den,x),b,a))
    assert len(poles)==3, poles
    deriv=[i*den[i] for i in range(1,len(den))]
    residues=[pv(num,p)/pv(deriv,p) for p in poles]
    def h(t):return sum(r*math.exp(p*t) for r,p in zip(residues,poles))
    grid=[0.]+np.geomspace(1e-6/max(-p for p in poles),50/min(-p for p in poles),220)
    zeros=[]
    for x,y in zip(grid[:-1],grid[1:]):
        if h(x)*h(y)<0: zeros.append(brentq(h,x,y,xtol=1e-18))
    edges=[0.]+zeros+[math.inf]
    positive=negative=0.
    for x,y in zip(edges[:-1],edges[1:]):
        val=sum(r/p*((0 if math.isinf(y) else math.exp(p*y))-math.exp(p*x)) for r,p in zip(residues,poles))
        if val>0:positive+=val
        else:negative-=val
    assert abs(positive-negative)<1e-6
    return positive, negative, zeros, poles, residues

def calc(r21=10000,r20=20000,r22=10000,r35=10000,pot=100000):
    b=r21*r20/(r21+r20); k=r20/(r21+r20); f=r35+pot;a=r22
    C=1e-6;c=1e-8;d=2.2e-12
    pm=mul;pa=add
    den=pa(pa(pm(pm([1,f*d],[1,b*c]),[1,a*C]),pm([0,f*C],[1,b*c])),pm([0,b*C],[1,f*d]))
    av=area([0,f*C*k],den); au=area([0,f*C,f*C*b*c],den)
    return {'bound_v':24*av[0]+12*au[0], 'av':av[0],'au':au[0], 'params':[r21,r20,r22,r35,pot], 'v_zeros_s':av[2], 'u_zeros_s':au[2], 'poles':av[3]}

nom=[calc(pot=float(x)) for x in np.linspace(0,100000,501)]
worst=None
worst_current=None
for signs in itertools.product([.99,1.01],repeat=4):
    for pot in np.linspace(0,110000,551):
        r=calc(10000*signs[0],20000*signs[1],10000*signs[2],10000*signs[3],float(pot))
        if worst is None or r['bound_v']>worst['bound_v']:worst=r
        r['current_bound_a']=r['bound_v']/(r['params'][3]+r['params'][4])
        if worst_current is None or r['current_bound_a']>worst_current['current_bound_a']:worst_current=r.copy()
out={'method':'Analytic pole-residue interval integration after numerical zero bracketing; no RMS. C9=1uF C7=10nF C11=2.2pF nominal.', 'nominal_samples':len(nom), 'nominal_max':max(nom,key=lambda x:x['bound_v']), 'nominal_endpoints':[nom[0],nom[-1]], 'resistor_corner_samples':16*551,'resistor_corner_max':worst,'resistor_corner_current_max':worst_current,'scope':'Grid in potentiometer resistance, all 16 fixed resistor +/-1% corners, pot extended to 110k for +10%; numerical coverage, not interval certification between samples.'}
Path('docs/independent_reviews/2026-10-22/sources/C11-area-results.json').write_text(json.dumps(out,indent=2),encoding='utf8')
print(json.dumps(out,indent=2))




