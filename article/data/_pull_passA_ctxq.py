"""Pull qwen3.5-9b filled-ctx and others from Pass A."""
import json, statistics

d = json.load(open("passA/step-2-ctx-quality.passA-stock24cu-20260603-1115.json"))

for name in ["qwen3.5-9b-ollama", "gemma4-latest", "granite-4.0-h-tiny",
             "gpt-oss-20b-ollama", "mistral-small-3.2-q3km-ollama",
             "deepseek-r1-14b", "qwen3-coder-30b-iq2m", "qwen3.5-35b-iq2m",
             "qwen3.6-35b-iq2m", "qwq-32b-iq2m", "mistral-small-3.2-q3km",
             "gpt-oss-20b-mxfp4", "qwen3.5-9b-q4km"]:
    if name not in d["results"]:
        print(f"--- {name}: NOT IN PASS A ctx-quality ---")
        continue
    print(f"\n=== {name} (Pass A ctx-quality) ===")
    for tier_str, t in d["results"][name].get("tiers", {}).items():
        runs = t.get("runs", [])
        ok = [r for r in runs if r.get("status") == "ok" and r.get("gen_tok_s")]
        gens = [r["gen_tok_s"] for r in ok]
        pps = [r["prefill_tok_s"] for r in ok if r.get("prefill_tok_s")]
        npass = sum(1 for r in ok if r.get("needle_pass"))
        statuses = [r.get("status") for r in runs]
        gen_med = statistics.median(gens) if gens else None
        pp_med = statistics.median(pps) if pps else None
        cv = (statistics.pstdev(gens) / statistics.mean(gens) * 100) if len(gens) >= 2 else None
        cv_str = f"{cv:.2f}%" if cv is not None else "---"
        gen_str = f"{gen_med:.2f}" if gen_med is not None else "---"
        pp_str = f"{pp_med:.1f}" if pp_med is not None else "---"
        print(f"  {tier_str:>6}: n_ok={len(ok)}/{len(runs)}  "
              f"gen={gen_str:>7}  cv={cv_str:>7}  pp={pp_str:>7}  "
              f"needle={npass}/{len(ok)}  statuses={statuses}")
