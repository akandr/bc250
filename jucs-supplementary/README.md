# JUCS supplementary material

Supplementary document for *Deploying LLM Inference on a Repurposed UMA APU:
Transferable Lessons from a Vulkan-Only, 16 GB Edge Platform* (J.UCS submission
#204239, revised version).

`supplement.pdf` holds the per-run and per-cell detail the paper refers to, and
the deployment, harness and evaluator detail moved out of the main text:
the kernel memory limits and runtime configuration, the 40-CU unlock at
register level, heap placement under `GGML_VK_PREFER_HOST_MEMORY`, per-token
weight traffic from the GGUF tensor tables and the effective-bandwidth check
across the cohort, the decode-window sweep, KV-cache footprint by width,
KV-cache width at task level, the perplexity evaluator, flash-attention shifts
and top-1 flip rates, HellaSwag and Winogrande accuracy with the paired McNemar
test, the multi-hop breakdown at the 1024-token budget, multi-needle recall by
depth, the 40-CU speed-up by context tier, the 40-CU sweep's harness changes,
the MXFP4 path, the configurations that failed at the quantisation ceiling,
the Mistral-Small prompt-format dependency, the key/value-head control with
keys and values quantised separately, the arithmetic item under four request
sequences, and the two figures removed from the main text.

Every table names the file it comes from. Those files are in
`../benchmarks/results-jucs-revision/` and the other `benchmarks/results-*`
directories of this repository.

## Building

```
lualatex supplement.tex   # twice, for the table of contents
```

Needs LuaLaTeX with `fontspec`, `booktabs`, `titlesec`, `fancyhdr` and
`placeins`, plus the TeX Gyre fonts.

## Contents

| File | What it is |
|------|------------|
| `supplement.tex` | source |
| `supplement.pdf` | built document, 15 pages |
| `figures/` | the two figures removed from the main text |

The manuscript itself is not in this repository. It is deposited separately;
see the paper link in the top-level `Readme.md`.

The harnesses, raw results and this supplement are archived at the tagged release
`jucs-revision-4` of the repository (`jucs-revision-1` and `jucs-revision-2` are the earlier
snapshots; `jucs-revision-3` was tagged on the wrong commit and is identical to `jucs-revision-2`;
the fourth adds the tensor-table scan of every model file on the board and the
effective-bandwidth check behind Table S3); Zenodo concept DOI
[10.5281/zenodo.22668983](https://doi.org/10.5281/zenodo.22668983) resolves to the latest version.
