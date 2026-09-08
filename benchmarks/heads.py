#!/usr/bin/env python3
"""Read attention head geometry from GGUF metadata for the models tested in A12.

The Q4_0 KV collapse turns out to affect Qwen2.5 models at and below 7B but not
the 14B builds, so family alone does not explain it.  KV-cache quantisation acts
per head, so the natural candidate is the number of key/value heads: with few of
them there is less averaging over quantisation error.  This reads head counts
from the tensor metadata; no GPU and no inference.
"""
import json, os, struct, sys
def parse_kv(path):
    with open(path,"rb") as f:
        assert struct.unpack("<I", f.read(4))[0]==0x46554747
        struct.unpack("<I", f.read(4)); nt=struct.unpack("<Q", f.read(8))[0]
        nkv=struct.unpack("<Q", f.read(8))[0]
        def s():
            n=struct.unpack("<Q", f.read(8))[0]; return f.read(n).decode("utf-8","replace")
        def val(t):
            if t==8: return s()
            if t==9:
                et=struct.unpack("<I", f.read(4))[0]; n=struct.unpack("<Q", f.read(8))[0]
                return [val(et) for _ in range(n)]
            fmt={0:"<B",1:"<b",2:"<H",3:"<h",4:"<I",5:"<i",6:"<f",7:"<?",10:"<Q",11:"<q",12:"<d"}[t]
            return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
        kv={}
        for _ in range(nkv):
            k=s(); t=struct.unpack("<I", f.read(4))[0]; kv[k]=val(t)
    return kv
def blob(tag):
    root="/usr/share/ollama/.ollama/models"
    fam,ver=tag.split(":")
    man=f"{root}/manifests/registry.ollama.ai/library/{fam}/{ver}"
    if not os.path.exists(man): return None
    for l in json.load(open(man)).get("layers",[]):
        if "model" in l.get("mediaType",""): return root+"/blobs/"+l["digest"].replace(":","-")
out=[]
for spec in sys.argv[1:]:
    tag,_,path = spec.partition("=")
    p = path or blob(tag)
    if not p or not os.path.exists(p): print(f"{tag}: missing"); continue
    kv=parse_kv(p)
    g=lambda suf: next((v for k,v in kv.items() if k.endswith(suf)), None)
    out.append({"model":tag,"arch":kv.get("general.architecture"),
                "n_head":g(".attention.head_count"),"n_head_kv":g(".attention.head_count_kv"),
                "n_embd":g(".embedding_length"),"n_layer":g(".block_count")})
print(json.dumps(out,indent=1))
