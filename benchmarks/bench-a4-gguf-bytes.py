#!/usr/bin/env python3
"""A4: GGUF tensor-table byte accounting, paper revision experiment.

The manuscript's fixed-versus-active decomposition infers an always-resident term
F from the measured throughput ratio.  This harness measures it instead: it reads
the GGUF tensor table directly and sums the on-disk bytes of the expert tensors
against everything else, so the per-token weight traffic of a sparse model can be
computed rather than fitted.

Per-token bytes for an MoE = (all non-expert tensor bytes)
                           + (n_expert_used / n_expert) * (expert tensor bytes)
Per-token bytes for a dense model = all tensor bytes.

No GPU, no inference: pure file metadata.  Output: JSON to stdout.
"""
import json, os, struct, sys

GGUF_MAGIC = 0x46554747
# ggml_type -> (block size in elements, type size in bytes)
TRAITS = {   # ggml_type -> (block size in elements, bytes per block)
    0:(1,4), 1:(1,2), 2:(32,18), 3:(32,20), 6:(32,22), 7:(32,24), 8:(32,34),
    9:(32,36), 10:(256,84), 11:(256,110), 12:(256,144), 13:(256,176),
    14:(256,210), 15:(256,292), 16:(256,66), 17:(256,74), 18:(256,98),
    19:(256,50), 20:(32,18), 21:(256,110), 22:(256,82), 23:(256,136),
    24:(1,1), 25:(1,2), 26:(1,4), 27:(1,8), 28:(1,8), 29:(256,56),
    30:(1,2), 34:(256,54), 35:(256,66), 36:(256,18), 39:(32,17),
}
TYPENAME = {0:"F32",1:"F16",2:"Q4_0",3:"Q4_1",6:"Q5_0",7:"Q5_1",8:"Q8_0",
            9:"Q8_1",10:"Q2_K",11:"Q3_K",12:"Q4_K",13:"Q5_K",14:"Q6_K",15:"Q8_K",
            16:"IQ2_XXS",17:"IQ2_XS",18:"IQ3_XXS",19:"IQ1_S",20:"IQ4_NL",
            21:"IQ3_S",22:"IQ2_S",23:"IQ4_XS",24:"I8",25:"I16",26:"I32",
            27:"I64",28:"F64",29:"IQ1_M",30:"BF16",34:"TQ1_0",35:"TQ2_0",39:"MXFP4"}

class R:
    def __init__(s, f): s.f = f
    def u32(s): return struct.unpack("<I", s.f.read(4))[0]
    def u64(s): return struct.unpack("<Q", s.f.read(8))[0]
    def i64(s): return struct.unpack("<q", s.f.read(8))[0]
    def s(s):
        n = s.u64(); return s.f.read(n).decode("utf-8", "replace")
    def val(s, t):
        if t == 8: return s.s()
        if t == 9:
            et = s.u32(); n = s.u64(); return [s.val(et) for _ in range(n)]
        fmt = {0:"<B",1:"<b",2:"<H",3:"<h",4:"<I",5:"<i",6:"<f",7:"<?",10:"<Q",11:"<q",12:"<d"}[t]
        sz = struct.calcsize(fmt)
        return struct.unpack(fmt, s.f.read(sz))[0]

def parse(path):
    with open(path, "rb") as f:
        r = R(f)
        assert r.u32() == GGUF_MAGIC, "not a GGUF file"
        ver = r.u32(); n_tensors = r.u64(); n_kv = r.u64()
        kv = {}
        for _ in range(n_kv):
            k = r.s(); t = r.u32(); kv[k] = r.val(t)
        tensors = []
        for _ in range(n_tensors):
            name = r.s(); nd = r.u32()
            dims = [r.u64() for _ in range(nd)]
            ttype = r.u32(); off = r.u64()
            n_elem = 1
            for d in dims: n_elem *= d
            blk, tsz = TRAITS.get(ttype, (1, 4))
            nbytes = (n_elem // blk) * tsz if blk > 1 else n_elem * tsz
            tensors.append({"name": name, "dims": dims, "type": ttype,
                            "type_name": TYPENAME.get(ttype, f"t{ttype}"),
                            "n_elem": n_elem, "bytes": nbytes})
    return ver, kv, tensors

def is_expert(name):
    # llama.cpp names fused per-layer expert stacks *_exps; some builds also
    # carry a separate shared expert (*_shexp), which is read every token and
    # therefore counts as always-resident, not as an activated expert.
    return "_exps" in name and "_shexp" not in name

def main(path, label):
    ver, kv, tensors = parse(path)
    arch = kv.get("general.architecture", "?")
    n_exp = None; n_used = None
    for k, v in kv.items():
        if k.endswith(".expert_count"): n_exp = v
        if k.endswith(".expert_used_count"): n_used = v
    exp_b = sum(t["bytes"] for t in tensors if is_expert(t["name"]))
    fix_b = sum(t["bytes"] for t in tensors if not is_expert(t["name"]))
    exp_p = sum(t["n_elem"] for t in tensors if is_expert(t["name"]))
    fix_p = sum(t["n_elem"] for t in tensors if not is_expert(t["name"]))
    frac = (n_used / n_exp) if (n_exp and n_used) else 1.0
    per_tok = fix_b + frac * exp_b
    # dominant quantisation by byte share
    bytype = {}
    for t in tensors:
        bytype[t["type_name"]] = bytype.get(t["type_name"], 0) + t["bytes"]
    out = {
        "label": label, "path": path, "gguf_version": ver, "arch": arch,
        "n_expert": n_exp, "n_expert_used": n_used, "n_tensors": len(tensors),
        "file_bytes": os.path.getsize(path),
        "tensor_bytes_total": exp_b + fix_b,
        "expert_bytes": exp_b, "always_resident_bytes": fix_b,
        "expert_params": exp_p, "always_resident_params": fix_p,
        "total_params": exp_p + fix_p,
        "activated_expert_fraction": frac,
        "per_token_bytes": per_tok,
        "per_token_gib": per_tok / 2**30,
        "always_resident_share_of_per_token": fix_b / per_tok if per_tok else None,
        "activated_expert_params": exp_p * frac if n_exp else 0,
        "per_token_params": fix_p + exp_p * frac,
        "bits_per_param_always_resident": (fix_b * 8 / fix_p) if fix_p else None,
        "bits_per_param_expert": (exp_b * 8 / exp_p) if exp_p else None,
        "bytes_by_type": {k: v for k, v in sorted(bytype.items(), key=lambda x: -x[1])},
        "largest_always_resident": sorted(
            [{"name": t["name"], "gib": t["bytes"]/2**30, "type": t["type_name"]}
             for t in tensors if not is_expert(t["name"])],
            key=lambda x: -x["gib"])[:8],
    }
    return out

if __name__ == "__main__":
    res = []
    for spec in sys.argv[1:]:
        label, path = spec.split("=", 1)
        res.append(main(path, label))
    print(json.dumps(res, indent=1))
