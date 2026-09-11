# Article measurement passes

Raw per-run measurement data behind the tables of *Deploying LLM Inference on a Repurposed UMA
APU* (J.UCS #204239). `_provenance_ledger.yaml` maps every quantitative claim of the submitted
manuscript to the file and compute-unit mode it derives from; this README describes the files.

All passes ran on the same board (Fedora 43, kernel 6.18.9, Mesa 25.3.4, BIOS AMI P3.00,
Ollama 0.20.0 / llama.cpp b9265, `oberon` governor at 1500 MHz, `ttm.pages_limit=4194304`).
`stock24cu` in a filename means the factory 24-CU configuration, `patched40cu` means the
community CU unlock enabled via `bc250_cc_write_mode=3`. Each pass directory carries the
environment snapshot taken at its start.

| Pass | Date | CU mode | Contents |
|---|---|---|---|
| A | 2026-06-03 | stock 24 | perf, filled-context, cold start, KV-quant sweeps |
| B | 2026-06-04 | patched 40 | the same four steps, paired against Pass A |
| C | 2026-06-04 | stock 24 | `GGML_VK_PREFER_HOST_MEMORY` heap A/B |
| D | 2026-06-04 | stock 24 | Ollama-served perf re-run (see the note below) |
| E | 2026-06-04 | stock 24 | MXFP4 probe, heap A/B at 32K, multi-needle retrieval |
| F, G | 2026-06-04/05 | stock 24 | filled-context re-runs behind the submitted Tables 5 and 9 |
| H | 2026-06-10 | stock 24 | `phi4:14b` filled-context check |

Which tables come from here, in the numbering of the **revised** manuscript:

- **Table 3** (generation speed and VRAM): the Ollama rows of Pass D, plus `qwen3.5:9b` from Pass A.
- **Table 5** (run-to-run stability): the Ollama rows of Pass D.
- **Table 12** (40-CU paired re-run): Pass A for the 24-CU column, Pass B for the 40-CU column.
- **Section 5.6** (Q8_0 versus Q4_K_M weights on `qwen3:8b`): Pass D.
- `step-1-perf.40cu-canonical.json` and `sclk-w6c2-10hz.csv` are the two loose files the ledger names.

Tables 4, 6, 7, 8, 9, 10 and 13 and every supplementary table come from `benchmarks/`, not from
here; each names its source file in its caption. The filled-context tables were superseded by the
16 GiB clean re-sweep in `benchmarks/results-16gib-resweep/`, as the ledger records.

## Pass D was a partial re-run: eleven rows in it are stale

Pass D re-measured only the Ollama-served models and was written with the harness's `merge=True`,
so it **carried forward the previous pass's entries verbatim for every other model**. Eleven
entries in `passD/step-1-perf.passD-stock24cu-20260604-1243.json` are therefore byte-identical
copies of the **40-CU** Pass B records, despite the `stock24cu` filename:

```
deepseek-r1-14b   gemma4-latest   gpt-oss-20b-mxfp4   gpt-oss-20b-ollama
granite-4.0-h-tiny   mistral-small-3.2-q3km-ollama   qwen3-coder-30b-iq2m
qwen3.5-35b-iq2m   qwen3.5-9b-ollama   qwen3.6-35b-iq2m   qwq-32b-iq2m
```

Run `_audit_passD_staleness.py` to reproduce that list; it prints every model with its Pass A,
Pass B and Pass D medians and flags each row as stale, carried, or freshly measured. **No number
in the article is taken from a stale Pass D row.** The article's 24-CU llama.cpp figures come from
Pass A and its 40-CU figures from Pass B; the sixteen freshly measured Ollama rows of Pass D are
the ones Tables 3 and 5 use.

`_canonical_stock24cu_perf.json` is the derived canonical 24-CU table with a `source` field per
model, built by `_build_canonical.py`; `_canonical_table5_9.json` is its filled-context
counterpart. The remaining `_*.py` files are the analysis and cross-check scripts used while
preparing the manuscript, and the `_table*.tex` fragments are the LaTeX tables they generated.
