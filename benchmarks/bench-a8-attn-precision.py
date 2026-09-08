#!/usr/bin/env python3
"""A8: precision of the attention weights, per model, from the GGUF tensor table.

Step A7 found that the Q4_0 KV-cache penalty is not ordered by a model's overall
quantisation: IQ2_M mixture-of-experts models are nearly immune while Q4_K_M dense
models pay up to 1.6 %.  The A4 byte accounting suggests why -- those MoE builds
keep their always-resident tensors, attention included, at far higher precision
than their experts.  If the KV penalty compounds with error already present in the
attention path, it should track the precision of the *attention* projections
specifically rather than the model average.

This reads the tensor table and reports bits per parameter for the attention
projections alone, so that hypothesis can be tested against the measured
penalties.  Metadata only: no GPU, no inference.
"""
import json, os, re, struct, sys

GGUF_MAGIC = 0x46554747
TRAITS = {0:(1,4),1:(1,2),2:(32,18),3:(32,20),6:(32,22),7:(32,24),8:(32,34),
          9:(32,36),10:(256,84),11:(256,110),12:(256,144),13:(256,176),
          14:(256,210),15:(256,292),16:(256,66),17:(256,74),18:(256,98),
          19:(256,50),20:(32,18),21:(256,110),22:(256,82),23:(256,136),
          24:(1,1),25:(1,2),26:(1,4),27:(1,8),28:(1,8),29:(256,56),
          30:(1,2),34:(256,54),35:(256,66),36:(256,18),39:(32,17)}
TYPENAME = {0:"F32",1:"F16",2:"Q4_0",3:"Q4_1",6:"Q5_0",7:"Q5_1",8:"Q8_0",10:"Q2_K",
            11:"Q3_K",12:"Q4_K",13:"Q5_K",14:"Q6_K",16:"IQ2_XXS",17:"IQ2_XS",
            18:"IQ3_XXS",19:"IQ1_S",20:"IQ4_NL",21:"IQ3_S",22:"IQ2_S",23:"IQ4_XS",
            29:"IQ1_M",30:"BF16",39:"MXFP4"}

class R:
    def __init__(s,f): s.f=f
    def u32(s): return struct.unpack("<I",s.f.read(4))[0]
    def u64(s): return struct.unpack("<Q",s.f.read(8))[0]
    def s_(s):
        n=s.u64(); return s.f.read(n).decode("utf-8","replace")
    def val(s,t):
        if t==8: return s.s_()
        if t==9:
            et=s.u32(); n=s.u64(); return [s.val(et) for _ in range(n)]
        fmt={0:"<B",1:"<b",2:"<H",3:"<h",4:"<I",5:"<i",6:"<f",7:"<?",10:"<Q",11:"<q",12:"<d"}[t]
        return struct.unpack(fmt,s.f.read(struct.calcsize(fmt)))[0]

def parse(path):
    with open(path,"rb") as f:
        r=R(f)
        assert r.u32()==GGUF_MAGIC
        r.u32(); nt=r.u64(); nkv=r.u64()
        kv={}
        for _ in range(nkv):
            k=r.s_(); t=r.u32(); kv[k]=r.val(t)
        ts=[]
        for _ in range(nt):
            name=r.s_(); nd=r.u32()
            dims=[r.u64() for _ in range(nd)]
            tt=r.u32(); r.u64()
            n=1
            for d in dims: n*=d
            blk,sz=TRAITS.get(tt,(1,4))
            ts.append((name,n,(n//blk)*sz if blk>1 else n*sz,tt))
    return kv,ts

# attention projections only: q/k/v/o and fused qkv, excluding norms and biases
ATTN = re.compile(r"\.attn_(q|k|v|o|qkv)\.weight$")

def main(specs):
    out=[]
    for spec in specs:
        label,path=spec.split("=",1)
        kv,ts=parse(path)
        a_p=a_b=0; t_p=t_b=0; types={}
        for name,n,b,tt in ts:
            t_p+=n; t_b+=b
            if ATTN.search(name):
                a_p+=n; a_b+=b
                types[TYPENAME.get(tt,f"t{tt}")]=types.get(TYPENAME.get(tt,f"t{tt}"),0)+b
        out.append({"label":label,"arch":kv.get("general.architecture"),
                    "attn_params":a_p,"attn_bytes":a_b,
                    "bits_per_param_attn": (a_b*8/a_p) if a_p else None,
                    "bits_per_param_model": (t_b*8/t_p) if t_p else None,
                    "attn_types_by_bytes":dict(sorted(types.items(),key=lambda x:-x[1])),
                    "n_attn_tensors":sum(1 for n,_,_,_ in ts if ATTN.search(n))})
    print(json.dumps(out,indent=1))

if __name__=="__main__": main(sys.argv[1:])
