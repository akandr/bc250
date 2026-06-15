#!/usr/bin/env python3
"""Peek schemas of historical results files to find perf-comparable ones."""
import json, glob, os

ROOT = "/Users/akandr/projects/bc250/benchmarks"
for p in sorted(glob.glob(f"{ROOT}/results-*.json")):
    name = os.path.basename(p)
    try:
        d = json.load(open(p))
    except Exception as e:
        print(f"=== {name} === LOAD ERR {e}"); continue
    print(f"=== {name} ({os.path.getsize(p)//1024} KiB) ===")
    if isinstance(d, dict):
        print("  top keys:", list(d)[:8])
        if "metadata" in d:
            md = d["metadata"]
            print("  meta date:", md.get("date") or md.get("timestamp") or md.get("started"))
        for k in list(d):
            if k == "metadata":
                continue
            v = d[k]
            sz = len(v) if hasattr(v, "__len__") else "?"
            print(f"  {k}: type={type(v).__name__} len={sz}")
            if isinstance(v, list) and v and isinstance(v[0], dict):
                print(f"    row keys: {list(v[0])[:18]}")
            elif isinstance(v, dict) and v:
                first_k = next(iter(v))
                first_v = v[first_k]
                print(f"    inner '{first_k}': type={type(first_v).__name__}")
                if isinstance(first_v, dict):
                    print(f"      inner keys: {list(first_v)[:18]}")
                elif isinstance(first_v, list) and first_v and isinstance(first_v[0], dict):
                    print(f"      list[0] keys: {list(first_v[0])[:18]}")
            break
    elif isinstance(d, list):
        print(f"  list len={len(d)}")
        if d and isinstance(d[0], dict):
            print(f"  row keys: {list(d[0])[:18]}")
    print()
