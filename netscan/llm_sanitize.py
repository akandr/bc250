#!/usr/bin/env python3
"""
llm_sanitize.py — Shared LLM output sanitization for all netscan scripts.

Phase 18: Universal Chinese text removal, think-tag stripping,
hallucination dedup, and artifact cleanup for Qwen3 on BC-250.

Usage:
    from llm_sanitize import sanitize_llm_output

    raw = call_ollama(system, user)
    clean = sanitize_llm_output(raw)
"""

import re


def sanitize_llm_output(text: str, *, dedup: bool = True) -> str:
    """
    Clean raw LLM output: strip thinking tags, Chinese text,
    sample-output preambles, non-ASCII garbage, and repetitive lines.

    Args:
        text:   Raw LLM output string.
        dedup:  If True, deduplicate repetitive lines (hallucination guard).

    Returns:
        Cleaned text string, or empty string if input is None/empty.
    """
    if not text:
        return ""

    # ── 1. Strip thinking blocks ──────────────────────────────────
    # Handle </think> without opening tag (model started thinking inline)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    # Remove matched <think>…</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Remove <|begin_of_thought|>…<|end_of_thought|> blocks
    text = re.sub(
        r"<\|begin_of_thought\|>.*?<\|end_of_thought\|>", "", text, flags=re.DOTALL
    )
    # Remove orphaned opening/closing tags
    text = re.sub(r"</?think>", "", text)
    text = re.sub(r"<\|(?:begin|end)_of_thought\|>", "", text)

    # ── 2. Remove Chinese / CJK characters ───────────────────────
    # CJK Unified Ideographs + CJK punctuation + fullwidth forms
    text = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", text)
    # CJK Compatibility + Extension blocks
    text = re.sub(r"[\u3400-\u4dbf\U00020000-\U0002a6df]+", " ", text)
    # Chinese-specific punctuation that might survive
    text = re.sub(r"[：；！？【】「」『』（）、，。]+", " ", text)

    # ── 3. Remove "sample output" / "example output" preambles ───
    text = re.sub(
        r"^(?:sample output|example output|here is (?:the |my |an? )?(?:analysis|report|summary))[:\s—\-]*",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # ── 4. Strip leading non-ASCII garbage per line ───────────────
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # Remove leading non-printable-ASCII junk but keep valid UTF-8 like ł, ó, ę etc.
        line = re.sub(r"^[^\x20-\x7E\n]+", "", line)
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # ── 5. Collapse excessive whitespace ──────────────────────────
    text = re.sub(r"  +", " ", text)  # multiple spaces → single
    text = re.sub(r"\n{3,}", "\n\n", text)  # 3+ newlines → 2
    text = text.strip()

    # ── 6. Deduplicate repetitive lines (hallucination guard) ─────
    if dedup and text:
        lines = text.split("\n")
        seen = {}
        deduped = []
        for line in lines:
            # Normalize for comparison
            norm = re.sub(r"[^\w\s]", "", line.strip().lower())
            norm = re.sub(r"\s+", " ", norm).strip()
            if len(norm) < 10:  # keep short lines (headers, blanks)
                deduped.append(line)
                continue
            cnt = seen.get(norm, 0) + 1
            seen[norm] = cnt
            if cnt <= 2:  # allow at most 2 occurrences
                deduped.append(line)
        text = "\n".join(deduped).strip()

    return text


def strip_to_json(text: str) -> str:
    """
    Extract JSON from LLM output that might contain thinking tags
    or preamble text before/after the JSON.

    Returns the first JSON object or array found, or empty string.
    """
    if not text:
        return ""

    # Strip thinking blocks first
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Find JSON object or array
    for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
        match = re.search(pattern, text)
        if match:
            return match.group(0)

    return text.strip()
