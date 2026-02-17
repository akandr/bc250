---
name: web-search
description: Search the web using DuckDuckGo via ddgr CLI. Use for current events, weather, facts, lookups.
metadata: { "openclaw": { "emoji": "🔍", "requires": { "bins": ["ddgr"] } } }
---

# Web Search

Use `ddgr` to search the web from the terminal.

## Quick search

```bash
ddgr --num 5 --noprompt "your search query"
```

## Search and show snippets

```bash
ddgr --num 3 --noprompt --expand "topic you want to learn about"
```

## Tips
- Use `--num N` to control number of results (default 5)
- Use `--noprompt` to avoid interactive mode
- Use `--expand` to show full abstracts
- For weather, prefer `curl wttr.in/City` (faster, more detailed)
- Combine with `curl` to fetch a specific URL from results if needed

## Fetching a page

To read content from a URL found in search results:

```bash
curl -sL "https://example.com/page" | head -100
```
