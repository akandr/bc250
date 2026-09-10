#!/usr/bin/env python3
"""Effective decode bandwidth from GGUF per-token bytes x measured decode rate.

Input: step-a4-gguf-bytes-all.json (bench-a4-scan-board.py, every model file on
the board) plus the manuscript's decode rates, which are copied here from the
canonical tables so the check is reproducible without re-running inference:
  Table 3  (Ollama, verified-stock 24 CU, 4K, n=3 median)
  Table 12 (llama.cpp / Ollama paired re-run, 24-CU column)
  Section 5.6 (qwen3:8b at Q8_0 weights, Ollama)
QwQ-32B is no longer on the board; its per-token bytes are its GGUF file size
(11.3 GiB), as in the supplement.  KV-cache reads are excluded (a few MB per
token at 4K).  Peak streaming bandwidth 357 GB/s is the roofline microbenchmark.
"""
import json, sys, statistics
PEAK = 357.0  # GB/s
GIB = 2**30
scan = json.load(open(sys.argv[1] if len(sys.argv) > 1 else 'step-a4-gguf-bytes-all.json'))
bytes_by = {m['label']: m['per_token_bytes'] for m in scan['models'] if 'per_token_bytes' in m}
# (row label, scan label, class, path, decode tok/s, source of the rate)
ROWS = [
 ('llama3.2:3b',      'ollama:llama3.2:3b',        'Q4 dense',  'Ollama',    80.6, 'Table 3'),
 ('phi4-mini',        'ollama:phi4-mini:latest',   'Q4 dense',  'Ollama',    67.9, 'Table 3'),
 ('gemma3:4b',        'ollama:gemma3:4b',          'Q4 dense',  'Ollama',    47.8, 'Table 3'),
 ('qwen3:8b',         'ollama:qwen3:8b',           'Q4 dense',  'Ollama',    31.6, 'Table 3'),
 ('qwen3.5:9b',       'ollama:qwen3.5:9b',         'Q4 dense',  'Ollama',    23.1, 'Table 3'),
 ('mistral-nemo:12b', 'ollama:mistral-nemo:12b',   'Q4 dense',  'Ollama',    26.0, 'Table 3'),
 ('qwen3:14b',        'ollama:qwen3:14b',          'Q4 dense',  'Ollama',    20.1, 'Table 3'),
 ('deepseek-r1:14b',  'ollama:deepseek-r1:14b',    'Q4 dense',  'Ollama',    22.1, 'Table 3'),
 ('deepseek-r1-14b',  'file:deepseek-r1-14b.gguf', 'Q4 dense',  'llama.cpp', 21.2, 'Table 12'),
 ('qwen3.5-35b-a3b',  'ollama:qwen3.5-35b-a3b-iq2m:latest', 'IQ2_M', 'llama.cpp', 59.5, 'Table 12'),
 ('qwen3.6-35b-a3b',  'file:qwen3.6-35b-a3b-iq2m.gguf',     'IQ2_M', 'llama.cpp', 59.6, 'Table 12'),
 ('QwQ-32B',          None,                        'IQ2_M',     'llama.cpp',  9.6, 'Table 12'),
 ('qwen3:8b (Q8_0)',  'ollama:qwen3:8b-q8_0',      'Q8_0 dense','Ollama',    26.1, 'Section 5.6'),
]
out = []
for name, key, cls, path, tps, src in ROWS:
    b = bytes_by[key] if key else 11.3 * GIB
    gbs = b * tps / 1e9
    out.append(dict(model=name, cls=cls, path=path, per_token_gib=b / GIB, decode_tps=tps,
                    rate_source=src, effective_gb_s=gbs, attained=gbs / PEAK,
                    bytes_source='scan' if key else 'file size (model removed)'))
print(f"{'model':18s} {'class':10s} {'path':9s} {'GiB/tok':>8s} {'tok/s':>6s} {'GB/s':>7s} {'attain':>7s}")
for r in out:
    print(f"{r['model']:18s} {r['cls']:10s} {r['path']:9s} {r['per_token_gib']:8.3f} {r['decode_tps']:6.1f} "
          f"{r['effective_gb_s']:7.1f} {100*r['attained']:6.1f}%")
for cls, path in [('Q4 dense', 'Ollama'), ('IQ2_M', 'llama.cpp')]:
    v = [r['effective_gb_s'] for r in out if r['cls'] == cls and r['path'] == path]
    m = statistics.mean(v)
    print(f"\n{cls} via {path}: n={len(v)} mean {m:.1f} GB/s, min {min(v):.1f} ({100*(min(v)/m-1):+.1f}%), "
          f"max {max(v):.1f} ({100*(max(v)/m-1):+.1f}%), sd {statistics.stdev(v):.1f} ({100*statistics.stdev(v)/m:.1f}%)")
json.dump({'peak_gb_s': PEAK, 'rows': out}, open('a4-attainment.json', 'w'), indent=1)
