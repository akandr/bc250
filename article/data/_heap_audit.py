#!/usr/bin/env python3
"""Heap-isolation audit: did Pass A vs Pass B correctly isolate CU effect from
heap-placement effect?
"""
import json, os
DATA = os.path.dirname(os.path.abspath(__file__))

# (1) Confirm both passes ran with default heap (PHM not set in env_extra)
print("=== (1) Heap-relevant env in Pass A and Pass B perf runs ===")
print("(both should show empty env_extra for PHM/GGML_VK/VK_/RADV keys)\n")
for pass_name, path in [("A", "passA/step-1-perf.passA-stock24cu-20260603-1115.json"),
                         ("B", "passB/step-1-perf.passB-patched40cu-20260604-0033.json")]:
    d = json.load(open(os.path.join(DATA, path)))
    envs = set()
    for name, rec in d["results"].items():
        for r in rec.get("runs", []):
            ee = r.get("env_extra", {})
            relevant = tuple(sorted((k, v) for k, v in ee.items()
                                    if any(s in k for s in ("PREFER_HOST", "GGML_VK", "VK_", "RADV"))))
            envs.add(relevant)
    print(f"  Pass {pass_name}: distinct heap-relevant env_extra sets = {envs}")

# (2) projected_vm_gib distribution (default = DEVICE_LOCAL 11 GiB heap with PHM=0)
print("\n=== (2) projected_vm_gib (default placement, weights -> 11 GiB DEVICE_LOCAL heap) ===")
print("Heap sizes: BAR-mapped HOST_VISIBLE 5.5 GiB, DEVICE_LOCAL 11 GiB.")
print("Models above 5.5 GiB working set would spill BAR heap if PHM=1 were set;")
print("PHM=0 sends them to the 11 GiB heap directly, no spill.\n")
A = json.load(open(os.path.join(DATA, "passA/step-1-perf.passA-stock24cu-20260603-1115.json")))
print(f"{'model':<34} {'pVM_GiB':>9} {'fits_5.5':>9} {'fits_11':>8}")
print("-" * 65)
maxv = 0
for name, rec in A["results"].items():
    runs = rec.get("runs", [])
    if not runs: continue
    vms = [r.get("projected_vm_gib") for r in runs if r.get("projected_vm_gib")]
    if not vms: continue
    pvm = vms[0]
    maxv = max(maxv, pvm)
    print(f"{name:<34} {pvm:>9.2f} {'yes' if pvm < 5.5 else 'NO':>9} {'yes' if pvm < 11 else 'NO':>8}")
print(f"\n  Max projected_vm in run cohort: {maxv:.2f} GiB. Ceiling at run time was 11.0 GiB (Pass A) -> 12.0 GiB (Pass A.1, Pass B).")

# (3) status of existing heap-ab data on bc250
print("\n=== (3) Existing heap-ab data ===")
print("  /home/akandr/phase-c-out/results/step-heap-ab.json    137 KB  2026-05-29 (PRE-Pass-A; was 40-CU patched, mislabelled-as-stock era)")
print("  /home/akandr/phase-c-out/results/step-w4b-heap-ab-filled.json 47 KB 2026-06-01")
print("  Article anchor table @ 65k (qwen3.5:9b -12%, gemma3:4b flat) is 40-CU.")
print("  Article §5.9.1 sub-32K replication claims '24-CU stock' on granite + deepseek-r1-14b.")
print("  CRITICAL: Pass A (verified-stock 24-CU) did NOT include heap-ab.")
print("  -> if the May data was 40-CU patched (now strongly suspected), the §5.9.1")
print("     '24-CU stock' provenance must be re-verified or re-run, identical to the")
print("     Table-1 mislabel issue.")
