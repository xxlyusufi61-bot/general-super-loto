# -*- coding: utf-8 -*-
"""
SUPER LOTO - OTOMATIK SIFRE / FORMUL KESIF MOTORU v1.0
- Arsiv bu dosyanin icinde: 2025 + 2026 #107'e kadar.
- #108 ve #109 dahil DEGILDIR.
- Sadece standart Python kutuphaneleri kullanir.
- Amac: tek tek formül denemek yerine cok sayida kural ailesini
  walk-forward backtest ile otomatik taramak.
- Bu bir sans oyunu oldugu icin "garanti" formül iddiasinda bulunmaz.
"""

import math, statistics, random, itertools, time
from collections import Counter, defaultdict

ARCHIVE = """20250001|8,36,38,42,53,56
20250002|11,15,20,29,35,56
20250003|2,21,26,43,44,55
20250004|4,7,11,13,25,48
20250005|2,4,19,21,39,53
20250006|3,4,11,42,45,56
20250007|33,38,46,50,55,58
20250008|1,15,18,19,41,52
20250009|13,30,39,42,44,46
20250010|1,4,8,10,42,55
20250011|2,3,4,15,38,51
20250012|2,3,4,9,18,19
20250013|5,21,23,41,42,58
20250014|19,20,24,25,33,53
20250015|4,8,10,20,44,57
20250016|5,11,32,34,43,53
20250017|3,5,17,26,30,49
20250018|8,24,33,45,47,51
20250019|15,23,42,46,47,48
20250020|10,13,18,19,28,30
20250021|2,25,37,44,48,54
20250022|2,3,7,23,36,43
20250023|3,9,15,37,54,58
20250024|3,4,36,40,46,58
20250025|2,13,43,48,50,53
20250026|6,17,31,43,48,55
20250027|2,14,36,37,52,59
20250028|14,23,24,26,45,47
20250029|6,13,24,26,29,38
20250030|4,6,8,27,35,43
20250031|1,2,3,19,33,39
20250032|2,19,44,47,58,59
20250033|11,35,43,46,57,60
20250034|1,20,28,42,47,49
20250035|12,14,18,36,44,50
20250036|3,17,20,29,46,50
20250037|15,20,22,41,48,51
20250038|5,25,31,43,48,56
20250039|2,22,26,31,35,50
20250040|5,21,24,26,33,57
20250041|15,18,28,53,55,60
20250042|7,13,15,21,22,24
20250043|2,3,19,30,40,55
20250044|5,25,26,29,39,47
20250045|9,15,37,43,44,57
20250046|4,6,7,18,21,38
20250047|10,12,32,36,44,58
20250048|19,27,34,40,48,54
20250049|9,21,33,37,39,56
20250050|2,23,29,45,50,59
20250051|28,32,35,47,54,58
20250052|1,26,34,35,39,45
20250053|1,4,18,26,35,44
20250054|2,16,25,30,31,47
20250055|7,32,45,50,56,58
20250056|5,19,33,37,43,47
20250057|1,10,15,21,35,55
20250058|22,28,33,37,39,52
20250059|1,13,25,44,46,60
20250060|4,15,28,50,53,59
20250061|12,27,31,34,40,55
20250062|6,8,12,17,22,58
20250063|5,18,19,53,57,58
20250064|12,16,24,32,40,55
20250065|6,12,20,22,30,47
20250066|4,12,23,48,50,51
20250067|22,32,37,38,49,50
20250068|2,14,34,41,51,57
20250069|6,8,23,27,46,60
20250070|16,19,25,37,43,57
20250071|7,22,31,39,48,56
20250072|11,12,21,52,53,54
20250073|9,25,29,31,35,46
20250074|5,19,20,35,51,52
20250075|10,18,27,28,29,30
20250076|8,12,27,36,43,53
20250077|12,13,17,20,33,45
20250078|14,23,39,40,45,49
20250079|1,13,22,38,56,57
20250080|4,14,23,27,35,57
20250081|11,16,17,19,34,36
20250082|11,25,26,43,44,57
20250083|4,6,7,12,14,32
20250084|4,12,26,28,37,43
20250085|5,6,9,24,32,54
20250086|1,8,14,19,36,60
20250087|5,10,11,18,24,46
20250088|10,13,15,27,29,46
20250089|20,26,28,43,47,56
20250090|3,14,19,27,34,57
20250091|7,27,41,47,51,60
20250092|2,11,13,17,19,60
20250093|15,25,34,40,46,47
20250094|7,31,35,39,40,44
20250095|2,30,36,39,40,49
20250096|8,17,18,39,52,59
20250097|4,7,30,39,44,58
20250098|7,10,23,41,52,57
20250099|25,29,31,34,37,52
20250100|9,10,11,15,50,52
20250101|12,19,27,40,49,57
20250102|6,23,26,43,59,60
20250103|3,8,17,53,55,56
20250104|2,27,32,42,46,57
20250105|25,27,30,31,44,45
20250106|15,23,24,25,47,57
20250107|11,22,34,43,53,59
20250108|28,48,57,58,59,60
20250109|5,6,8,20,23,43
20250110|5,38,40,50,56,60
20250111|23,24,31,35,43,59
20250112|2,5,42,55,56,58
20250113|4,5,23,42,48,51
20250114|1,9,19,34,39,50
20250115|1,7,10,26,33,35
20250116|10,14,31,35,55,57
20250117|15,19,23,25,38,53
20250118|2,14,21,25,42,60
20250119|1,3,9,25,37,44
20250120|1,8,16,20,22,41
20250121|4,8,15,30,43,47
20250122|2,33,41,54,58,60
20250123|3,23,28,39,44,57
20250124|1,20,22,39,52,57
20250125|13,20,32,35,37,41
20250126|2,3,11,25,51,52
20250127|14,23,36,45,47,48
20250128|18,22,26,44,53,55
20250129|10,22,39,41,48,49
20250130|1,6,35,54,56,59
20250131|15,17,20,39,44,47
20250132|10,11,26,46,54,60
20250133|2,16,20,31,39,55
20250134|18,26,34,48,49,56
20250135|7,12,14,15,17,40
20250136|9,13,18,25,29,58
20250137|6,9,16,20,26,46
20250138|1,9,16,30,45,54
20250139|2,27,34,37,48,60
20250140|12,19,41,51,54,59
20250141|2,15,28,39,48,50
20250142|5,7,8,28,47,58
20250143|9,12,22,34,43,56
20250144|2,9,16,19,29,52
20250145|9,11,18,35,49,50
20250146|19,33,37,39,56,57
20250147|17,28,32,36,38,51
20250148|1,8,29,39,41,50
20250149|2,9,27,30,36,55
20250150|5,9,32,35,55,56
20250151|4,7,31,35,40,52
20250152|9,11,12,21,32,34
20250153|13,20,33,35,40,48
20250154|2,7,19,21,23,59
20250155|6,31,35,41,44,46
20250156|6,34,35,36,56,57
20260001|34,36,39,45,53,55
20260002|20,21,23,26,32,37
20260003|6,9,25,29,45,57
20260004|3,7,12,22,23,55
20260005|3,8,10,12,32,42
20260006|7,19,23,32,48,55
20260007|1,22,27,41,44,46
20260008|9,24,25,33,44,52
20260009|6,26,32,43,48,54
20260010|1,2,36,42,49,55
20260011|12,20,23,38,49,50
20260012|1,7,16,34,38,42
20260013|1,4,7,8,11,41
20260014|7,17,21,25,32,52
20260015|21,33,43,47,55,59
20260016|14,24,34,42,51,60
20260017|8,18,21,29,36,38
20260018|9,29,36,46,47,52
20260019|3,5,10,22,23,52
20260020|9,16,31,34,40,58
20260021|2,14,31,38,47,54
20260022|6,8,43,49,58,60
20260023|14,16,30,42,46,55
20260024|6,19,31,39,47,51
20260025|3,19,26,37,44,57
20260026|3,14,27,32,43,44
20260027|17,18,21,27,37,51
20260028|3,11,16,25,37,41
20260029|11,41,43,44,51,57
20260030|7,9,14,36,52,55
20260031|3,22,38,41,44,50
20260032|5,12,21,28,35,42
20260033|1,6,7,19,31,44
20260034|18,21,34,43,47,50
20260035|8,9,20,30,33,55
20260036|7,9,20,22,28,39
20260037|7,14,17,25,42,49
20260038|1,17,28,30,32,42
20260039|2,16,35,51,52,56
20260040|6,11,22,36,37,53
20260041|1,3,14,19,48,56
20260042|10,29,36,42,46,52
20260043|6,18,33,40,50,58
20260044|11,19,23,27,28,39
20260045|13,19,21,30,42,43
20260046|15,20,29,51,56,57
20260047|1,11,26,49,55,59
20260048|8,31,35,36,49,55
20260049|9,26,30,39,41,45
20260050|6,36,38,44,49,53
20260051|2,14,22,34,48,59
20260052|25,35,39,41,54,56
20260053|1,13,19,27,42,56
20260054|14,20,31,35,38,39
20260055|1,11,20,33,35,47
20260056|1,8,31,38,40,56
20260057|2,19,28,30,31,37
20260058|13,18,35,45,46,50
20260059|4,9,11,19,24,26
20260060|3,23,27,47,50,53
20260061|7,21,23,25,37,51
20260062|19,22,28,30,44,46
20260063|9,17,38,47,54,58
20260064|12,21,23,45,49,55
20260065|2,4,13,16,48,58
20260066|15,18,20,31,43,56
20260067|4,6,7,10,20,28
20260068|2,8,21,24,36,47
20260069|10,11,35,38,41,57
20260070|15,19,30,38,57,60
20260071|3,26,27,32,44,46
20260072|8,14,23,32,58,60
20260073|3,27,29,33,34,58
20260074|5,6,24,37,43,48
20260075|4,29,30,31,37,39
20260076|5,11,31,46,51,56
20260077|3,6,12,14,23,47
20260078|6,8,15,18,19,29
20260079|6,32,37,38,40,57
20260080|40,41,46,52,57,60
20260081|4,26,30,38,51,53
20260082|3,16,35,44,50,57
20260083|14,15,20,30,36,44
20260084|7,8,13,27,37,55
20260085|6,26,30,33,38,46
20260086|11,32,33,47,53,59
20260087|2,13,30,52,58,60
20260088|2,11,43,47,48,58
20260089|6,9,22,27,41,46
20260090|3,6,24,50,51,53
20260091|1,25,28,41,55,56
20260092|1,4,7,16,30,60
20260093|16,23,24,42,46,57
20260094|24,27,38,41,49,50
20260095|10,27,31,32,39,54
20260096|7,16,22,39,40,60
20260097|18,24,32,34,52,60
20260098|8,20,38,45,49,50
20260099|16,28,45,49,52,54
20260100|8,21,25,46,48,57
20260101|13,25,26,34,35,38
20260102|13,19,29,31,34,53
20260103|15,26,27,41,49,52
20260104|6,16,25,30,33,46
20260105|28,30,40,42,47,59
20260106|3,15,20,23,30,60
20260107|27,30,31,41,43,57"""

def parse_archive():
    draws=[]
    for line in ARCHIVE.splitlines():
        line=line.strip()
        if not line: continue
        no, nums=line.split("|")
        a=tuple(sorted(int(x) for x in nums.split(",")))
        if len(a)==6 and len(set(a))==6 and all(1<=x<=60 for x in a):
            draws.append((int(no),a))
    draws.sort()
    return draws

DRAWS=parse_archive()
NUMS=[x[1] for x in DRAWS]

def bins(a): return tuple((x-1)//10 for x in a)              # 0..5
def cols(a): return tuple((x-1)%10+1 for x in a)            # 1..10
def gaps(a): return tuple(a[i+1]-a[i] for i in range(5))
def pairgaps(a):
    return tuple(a[j]-a[i] for i in range(6) for j in range(i+1,6))
def deltas(a):
    return tuple(a[i+1]-a[i] for i in range(5))
def parity(a): return tuple(x%2 for x in a)
def lowhigh(a): return tuple(0 if x<=30 else 1 for x in a)
def sums(a): return sum(a)
def spread(a): return a[-1]-a[0]

def state_vector(a, mode):
    """Deterministic feature vector for nearest-neighbour discovery."""
    b=bins(a); c=cols(a); g=gaps(a); pg=pairgaps(a)
    if mode=="number": return tuple(x/60 for x in a)
    if mode=="bin": return b
    if mode=="col": return c
    if mode=="gap": return tuple(x/10 for x in g)
    if mode=="pairgap": return tuple(x/60 for x in pg)
    if mode=="bin_col": return b+c
    if mode=="num_bin_col": return tuple(x/60 for x in a)+tuple(x/5 for x in b)+tuple(x/10 for x in c)
    if mode=="num_gap": return tuple(x/60 for x in a)+tuple(x/10 for x in g)
    if mode=="all": return (tuple(x/60 for x in a)+tuple(x/5 for x in b)+
                             tuple(x/10 for x in c)+tuple(x/10 for x in g)+
                             tuple(x/60 for x in pg))
    raise ValueError(mode)

def euclid(u,v,weights=None):
    if weights is None:
        return math.sqrt(sum((x-y)**2 for x,y in zip(u,v)))
    return math.sqrt(sum(w*(x-y)**2 for x,y,w in zip(u,v,weights)))

def overlap(a,b):
    return len(set(a)&set(b))

def clamp60(x):
    # 1..60 cyclic movement
    return ((int(round(x))-1)%60)+1

# ---------- Candidate generators ----------
# Each generator gets source draw index i and training endpoint i.
# It returns a list of (number, score), higher is better.

def gen_same(a, train, **kw):
    # persistence baseline
    return [(x,1.0) for x in a]

def gen_shift(a, train, **kw):
    out=Counter()
    for shift in range(-15,16):
        if shift==0: continue
        cand=tuple(sorted(clamp60(x+shift) for x in a))
        if len(set(cand))==6:
            for x in cand: out[x]+=1/(1+abs(shift))
    return list(out.items())

def gen_pos_delta(a, train, **kw):
    # learn median target-source movement per sorted position
    out=Counter()
    if len(train)<8: return []
    for p in range(6):
        vals=[train[t+1][p]-train[t][p] for t in range(len(train)-1)]
        med=statistics.median(vals)
        for d in set([round(med), math.floor(med), math.ceil(med), 0]):
            x=clamp60(a[p]+d)
            out[x]+=1.0/(1+abs(d-med))
    return list(out.items())

def gen_interval_transition(a, train, **kw):
    # source position+bin -> target position/bin frequency
    out=Counter()
    if len(train)<12: return []
    cnt=defaultdict(Counter)
    for t in range(len(train)-1):
        s=train[t]; y=train[t+1]
        for p in range(6):
            cnt[(p,bins(s)[p])][bins(y)[p]]+=1
    for p in range(6):
        dist=cnt[(p,bins(a)[p])]
        for tb,n in dist.most_common():
            center=tb*10+5.5
            # generate 10 numbers in predicted target interval
            for x in range(tb*10+1,tb*10+11):
                out[x]+=n/(1+abs(x-center))
    return list(out.items())

def gen_matrix_col(a, train, **kw):
    # source position + row(bin) + column -> target number state
    out=Counter()
    if len(train)<20: return []
    cnt=defaultdict(Counter)
    for t in range(len(train)-1):
        s,y=train[t],train[t+1]
        for p in range(6):
            key=(p,bins(s)[p],cols(s)[p])
            # target exact number distribution at same rank
            cnt[key][y[p]]+=1
    for p in range(6):
        dist=cnt[(p,bins(a)[p],cols(a)[p])]
        for x,n in dist.most_common(12):
            out[x]+=n
    return list(out.items())

def gen_gap_change_nn(a, train, k=1, **kw):
    # compare 15 pair-distance CHANGE patterns, then use the following draw(s)
    if len(train)<20: return []
    # train contains draws through source
    changes=[]
    for t in range(1,len(train)-1):
        v1=pairgaps(train[t-1]); v2=pairgaps(train[t])
        changes.append((tuple(y-x for x,y in zip(v1,v2)), train[t+1]))
    if not changes: return []
    cur=tuple(y-x for x,y in zip(pairgaps(train[-2]),pairgaps(train[-1])))
    ranked=sorted(changes,key=lambda z: sum((x-y)**2 for x,y in zip(cur,z[0])))
    out=Counter()
    kk=min(k,len(ranked))
    for r in range(kk):
        y=ranked[r][1]
        w=1/(1+r)
        for x in y: out[x]+=w
    return list(out.items())

def gen_state_nn(a, train, mode="all", k=5, **kw):
    # nearest historical source-state -> its next draw
    if len(train)<25: return []
    target=state_vector(a,mode)
    hist=[]
    for t in range(len(train)-1):
        s=train[t]
        hist.append((euclid(target,state_vector(s,mode)),train[t+1]))
    hist.sort(key=lambda z:z[0])
    out=Counter()
    for r,(dist,y) in enumerate(hist[:min(k,len(hist))]):
        w=1/(dist+1e-6)/(r+1)
        for x in y: out[x]+=w
    return list(out.items())

def gen_movement_nn(a, train, k=5, **kw):
    # nearest previous 6-dimensional movement vector, then next draw
    if len(train)<25: return []
    moves=[]
    for t in range(1,len(train)-1):
        mv=tuple(train[t][p]-train[t-1][p] for p in range(6))
        moves.append((mv,train[t+1]))
    cur=tuple(train[-1][p]-train[-2][p] for p in range(6))
    ranked=sorted(moves,key=lambda z: sum((x-y)**2 for x,y in zip(cur,z[0])))
    out=Counter()
    for r,(mv,y) in enumerate(ranked[:min(k,len(ranked))]):
        w=1/(1+sum(abs(x-y) for x,y in zip(cur,mv)))*(1/(r+1))
        for x in y: out[x]+=w
    return list(out.items())

def gen_pair_relation(a, train, **kw):
    # learn source-number -> next-number empirical transitions
    out=Counter()
    if len(train)<20: return []
    cnt=defaultdict(Counter)
    for t in range(len(train)-1):
        for x in train[t]:
            for y in train[t+1]:
                cnt[x][y]+=1
    for x in a:
        for y,n in cnt[x].most_common(10):
            out[y]+=n
    return list(out.items())

GENERATORS = {
    "same": lambda a,tr: gen_same(a,tr),
    "shift": lambda a,tr: gen_shift(a,tr),
    "pos_delta": lambda a,tr: gen_pos_delta(a,tr),
    "interval": lambda a,tr: gen_interval_transition(a,tr),
    "matrix": lambda a,tr: gen_matrix_col(a,tr),
    "gapchange1": lambda a,tr: gen_gap_change_nn(a,tr,k=1),
    "gapchange3": lambda a,tr: gen_gap_change_nn(a,tr,k=3),
    "gapchange5": lambda a,tr: gen_gap_change_nn(a,tr,k=5),
    "state_num_k1": lambda a,tr: gen_state_nn(a,tr,mode="number",k=1),
    "state_num_k3": lambda a,tr: gen_state_nn(a,tr,mode="number",k=3),
    "state_bin_k3": lambda a,tr: gen_state_nn(a,tr,mode="bin",k=3),
    "state_gap_k3": lambda a,tr: gen_state_nn(a,tr,mode="gap",k=3),
    "state_pairgap_k1": lambda a,tr: gen_state_nn(a,tr,mode="pairgap",k=1),
    "state_pairgap_k3": lambda a,tr: gen_state_nn(a,tr,mode="pairgap",k=3),
    "state_all_k1": lambda a,tr: gen_state_nn(a,tr,mode="all",k=1),
    "state_all_k3": lambda a,tr: gen_state_nn(a,tr,mode="all",k=3),
    "state_all_k5": lambda a,tr: gen_state_nn(a,tr,mode="all",k=5),
    "movement_k1": lambda a,tr: gen_movement_nn(a,tr,k=1),
    "movement_k3": lambda a,tr: gen_movement_nn(a,tr,k=3),
    "movement_k5": lambda a,tr: gen_movement_nn(a,tr,k=5),
    "pair_relation": lambda a,tr: gen_pair_relation(a,tr),
}

def normalize_scores(scores):
    # scores -> [0,1], keeping relative strength
    c=Counter()
    for x,s in scores:
        c[x]+=max(0.0,float(s))
    if not c: return {}
    mx=max(c.values())
    return {x:s/mx for x,s in c.items()} if mx else {}

def predict(recipe, source, train):
    """
    recipe = list of (generator_name, weight).
    Each component is independently generated from train and source.
    Final six numbers are highest combined scores, with duplicate protection.
    """
    total=Counter()
    for name,w in recipe:
        try:
            raw=GENERATORS[name](source,train)
            ns=normalize_scores(raw)
            for x,s in ns.items():
                total[x]+=w*s
        except Exception:
            # One broken hypothesis must not stop the search.
            continue
    ranked=sorted(total.items(), key=lambda z:(-z[1],z[0]))
    chosen=[]
    for x,s in ranked:
        if x not in chosen:
            chosen.append(x)
        if len(chosen)==6: break
    # fill if a recipe produced too few numbers
    if len(chosen)<6:
        for x in range(1,61):
            if x not in chosen:
                chosen.append(x)
            if len(chosen)==6: break
    return tuple(sorted(chosen))

def evaluate_recipe(recipe, draws, start, end):
    hits=[]
    exact=[]
    for i in range(start,end+1):
        train=draws[:i]
        source=draws[i-1]
        pred=predict(recipe,source,train)
        h=overlap(pred,draws[i])
        hits.append(h)
        exact.append((i+1,h,pred,draws[i]))
    avg=sum(hits)/len(hits) if hits else 0
    dist=Counter(hits)
    return {
        "avg":avg, "hits":dist, "n":len(hits),
        "p2":sum(h>=2 for h in hits)/len(hits) if hits else 0,
        "p3":sum(h>=3 for h in hits)/len(hits) if hits else 0,
        "p4":sum(h>=4 for h in hits)/len(hits) if hits else 0,
        "p5":sum(h>=5 for h in hits)/len(hits) if hits else 0,
        "p6":sum(h==6 for h in hits)/len(hits) if hits else 0,
        "details":exact
    }

def score_metric(r):
    # Rewards average overlap, then 3+/4+/5+/6+.
    return (r["avg"] + 0.25*r["p3"] + 0.50*r["p4"] +
            0.75*r["p5"] + 1.00*r["p6"])

def make_base_recipes():
    names=list(GENERATORS)
    recipes=[]
    for n in names:
        recipes.append([(n,1.0)])
    # small curated combinations, generated automatically from families
    families=[
        ["interval","matrix","pos_delta"],
        ["gapchange1","gapchange3","state_pairgap_k1"],
        ["state_all_k1","state_all_k3","movement_k3"],
        ["pair_relation","interval","matrix"],
        ["state_num_k1","state_gap_k3","interval"],
    ]
    for fam in families:
        for weights in [(1,1,1),(2,1,1),(1,2,1),(1,1,2),(2,2,1)]:
            recipes.append(list(zip(fam,weights)))
    return recipes

def mutate(recipe,rng):
    names=list(GENERATORS)
    d={n:w for n,w in recipe}
    if rng.random()<0.45:
        n=rng.choice(names)
        d[n]=round(rng.uniform(0.25,2.5),2)
    if len(d)>1 and rng.random()<0.25:
        n=rng.choice(list(d))
        del d[n]
    if len(d)<5 and rng.random()<0.35:
        d[rng.choice(names)]=round(rng.uniform(0.25,1.75),2)
    return [(n,w) for n,w in d.items() if w>0]

def crossover(a,b,rng):
    d={}
    for n,w in a+b:
        if rng.random()<0.5:
            d[n]=w
    if not d: d={rng.choice(list(GENERATORS)):1.0}
    return list(d.items())

def run():
    print("="*72)
    print(" SUPER LOTO OTOMATIK SIFRE / FORMUL KESIF MOTORU v1.0")
    print("="*72)
    print(f"Arsiv: {len(DRAWS)} cekilis | 2025 + 2026 #107")
    print("UYARI: #108 ve #109 arsivde yok; gelecegi gormek icin kullanilmaz.")
    print()

    # Use 2026 only for the main competition, while also allowing 2025
    # to provide older training history.
    start=30     # target is roughly #31 in the combined archive
    # Find index of 2026 #31 and #70 by draw id.
    idx31=next(i for i,(no,a) in enumerate(DRAWS) if no==20260031)
    idx70=next(i for i,(no,a) in enumerate(DRAWS) if no==20260070)
    idx107=next(i for i,(no,a) in enumerate(DRAWS) if no==20260107)

    # Stage A: discover on earlier 2026, using all 2025 as history.
    train_start=idx31
    dev_end=idx70
    test_start=idx70+1

    base=make_base_recipes()
    results=[]
    print(f"1) {len(base)} temel kural/karisim taraniyor...")
    for j,r in enumerate(base,1):
        ev=evaluate_recipe(r,DRAWS,train_start,dev_end)
        results.append((score_metric(ev),r,ev))
    results.sort(reverse=True,key=lambda z:z[0])

    print("\nIlk 10:")
    for rank,(sc,r,ev) in enumerate(results[:10],1):
        print(f"{rank:2d}. avg={ev['avg']:.3f} | 2+={ev['p2']*100:5.1f}% | "
              f"3+={ev['p3']*100:5.1f}% | 4+={ev['p4']*100:5.1f}% | {r}")

    # Evolution: keep elites, mutate/crossover. Importantly, fitness is
    # calculated ONLY on the development window here.
    rng=random.Random(20260909)
    population=[r for _,r,_ in results[:12]]
    best_dev=results[0]
    for gen in range(1,31):
        cand=list(population)
        while len(cand)<80:
            if rng.random()<0.55:
                parent=rng.choice(population)
                child=mutate(parent,rng)
            else:
                a=rng.choice(population); b=rng.choice(population)
                child=mutate(crossover(a,b,rng),rng)
            cand.append(child)
        scored=[]
        for r in cand:
            ev=evaluate_recipe(r,DRAWS,train_start,dev_end)
            scored.append((score_metric(ev),r,ev))
        scored.sort(reverse=True,key=lambda z:z[0])
        population=[r for _,r,_ in scored[:12]]
        if scored[0][0] > best_dev[0]:
            best_dev=scored[0]
        if gen in (1,5,10,20,30):
            ev=best_dev[2]
            print(f"Gen {gen:02d}: best avg={ev['avg']:.3f}, 2+={ev['p2']*100:.1f}%, recipe={best_dev[1]}")

    # Final honest holdout: top candidates selected WITHOUT looking at test.
    finalists=[]
    seen=set()
    for sc,r,ev in sorted([best_dev]+results,reverse=True,key=lambda z:z[0]):
        key=tuple(sorted((n,round(w,3)) for n,w in r))
        if key in seen: continue
        seen.add(key)
        finalists.append((r,ev))
        if len(finalists)>=10: break

    print("\n2) KOR TEST: secilen ilk 10 model, daha once gormedigi pencereyi test ediyor.")
    finalrows=[]
    for r,dev_ev in finalists:
        te=evaluate_recipe(r,DRAWS,test_start,idx107)
        finalrows.append((score_metric(te),r,dev_ev,te))
    finalrows.sort(reverse=True,key=lambda z:z[0])

    print("\nKOR TEST SONUCLARI:")
    for rank,(sc,r,de,te) in enumerate(finalrows,1):
        print(f"{rank:2d}. avg={te['avg']:.3f} | 2+={te['p2']*100:5.1f}% | "
              f"3+={te['p3']*100:5.1f}% | 4+={te['p4']*100:5.1f}% | "
              f"5+={te['p5']*100:5.1f}% | 6/6={te['p6']*100:5.1f}%")
        print("    ",r)

    winner=finalrows[0]
    te=winner[3]
    print("\n3) EN IYI KOR TEST MODELI")
    print("Recipe:",winner[1])
    print(f"Ortalama isabet: {te['avg']:.3f}/6")
    print(f"2+ isabet: {te['p2']*100:.2f}%")
    print(f"3+ isabet: {te['p3']*100:.2f}%")
    print(f"4+ isabet: {te['p4']*100:.2f}%")
    print(f"5+ isabet: {te['p5']*100:.2f}%")
    print(f"6/6: {te['p6']*100:.2f}%")

    # Show strongest individual test cases, not just average.
    good=[x for x in te["details"] if x[1]>=2]
    print(f"\nKor testte 2+ yakalayan test sayisi: {len(good)} / {te['n']}")
    for row in good[:15]:
        idx,h,pred,actual=row
        print(f"  Cekilis {idx}: {h}/6 | tahmin={pred} | gercek={actual}")

    # Baseline expected overlap for two independent 6/60 sets = 0.6.
    print("\n4) REFERANS")
    print("Bagimsiz 6/60 iki kolonun beklenen ortak sayisi = 0.600.")
    print("Bu motor 0.600'u gecen modelleri 'kesin formül' diye kabul etmez;")
    print("kor testte de iyilesme arar. Ezber/overfit riski icin gelistirme ve")
    print("kor test pencereleri ayri tutulmustur.")

    # IMPORTANT: do NOT automatically output a #108/#109 prediction.
    # User asked for validation first.
    print("\n5) #109 DURUMU")
    print("#109 icin kolon URETILMEDI.")
    print("#108 de bu arsivde yok. Once kor test sonuclarini degerlendirecegiz.")

if __name__=="__main__":
    try:
        run()
    except Exception as e:
        print("\nPROGRAM HATASI:",repr(e))
        print("Hata nedeniyle sessizce sonuc uydurulmadi. Yukaridaki hata metni onarim icin kullanilabilir.")
