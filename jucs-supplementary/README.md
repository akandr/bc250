# JUCS supplementary material

Supplementary document for *Deploying LLM Inference on a Repurposed UMA APU:
Transferable Lessons from a Vulkan-Only, 16 GB Edge Platform* (J.UCS submission
#204239).

`supplement.pdf` holds the per-run and per-cell detail the paper refers to:
heap placement under `GGML_VK_PREFER_HOST_MEMORY`, KV-cache footprint by width,
HellaSwag and Winogrande accuracy with the paired McNemar test, the multi-hop
breakdown at the 1024-token budget, multi-needle recall by depth, the 40-CU
speed-up by context tier, the MXFP4 path, the configurations that failed at the
quantisation ceiling, and the two figures removed from the main text.

Every table names the file it comes from. Those files are in
`../benchmarks/results-jucs-revision/` and the other `benchmarks/results-*`
directories of this repository.

## Building

```
latexmk -lualatex supplement.tex
```

Needs LuaLaTeX with `fontspec`, `booktabs`, `titlesec` and `fancyhdr`, plus the
TeX Gyre fonts.

## Contents

| File | What it is |
|------|------------|
| `supplement.tex` | source |
| `supplement.pdf` | built document, 7 pages |
| `figures/` | the two figures removed from the main text |

The manuscript itself is not in this repository. It is deposited separately;
see the paper link in the top-level `Readme.md`.

The harnesses, raw results and this supplement are archived at the tagged release
`jucs-revision-1` of the repository, Zenodo DOI [10.5281/zenodo.22668984](https://doi.org/10.5281/zenodo.22668984).
