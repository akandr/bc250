#!/usr/bin/env python3
"""market-think.py — Deep per-ticker market intelligence.

Runs an extended LLM chain-of-thought analysis on a single financial
asset. Designed to be scheduled as individual cron jobs per ticker,
spread across the evening hours after US market close.

Usage:
  market-think.py --ticker btc       # Deep analysis of Bitcoin
  market-think.py --ticker amd       # Deep analysis of AMD
  market-think.py --summary          # Aggregate all daily analyses
  market-think.py --list             # Show available tickers

Output: /opt/netscan/data/market/think/<ticker>-YYYYMMDD.json
"""

import argparse, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_CHAT  = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

THINK_DIR    = Path("/opt/netscan/data/market/think")
MARKET_DIR   = Path("/opt/netscan/data/market")
PROFILE_FILE = Path("/opt/netscan/profile.json")

SIGNAL_RPC   = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM  = "+<BOT_PHONE>"
SIGNAL_TO    = "+<OWNER_PHONE>"

# ── Ticker map (slug → yfinance symbol + metadata) ─────────────────────────

TICKERS = {
    "btc":     {"symbol": "BTC-USD",   "name": "Bitcoin",              "sector": "crypto",        "type": "crypto"},
    "eth":     {"symbol": "ETH-USD",   "name": "Ethereum",             "sector": "crypto",        "type": "crypto"},
    "aapl":    {"symbol": "AAPL",      "name": "Apple",                "sector": "tech",          "type": "stock"},
    "amd":     {"symbol": "AMD",       "name": "AMD",                  "sector": "semiconductor", "type": "stock"},
    "arm":     {"symbol": "ARM",       "name": "ARM Holdings",         "sector": "semiconductor", "type": "stock"},
    "asml":    {"symbol": "ASML",      "name": "ASML Holding",         "sector": "semiconductor", "type": "stock"},
    "intc":    {"symbol": "INTC",      "name": "Intel Corp",           "sector": "semiconductor", "type": "stock"},
    "tsm":     {"symbol": "TSM",       "name": "TSMC",                 "sector": "semiconductor", "type": "stock"},
    "samsung": {"symbol": "005930.KS", "name": "Samsung Electronics",  "sector": "semiconductor", "type": "stock"},
    "vz":      {"symbol": "VZ",        "name": "Verizon",              "sector": "telecom",       "type": "stock"},
    "wbd":     {"symbol": "WBD",       "name": "Warner Bros Discovery","sector": "media",         "type": "stock"},
    "fmc":     {"symbol": "FMC",       "name": "FMC Corp",             "sector": "specialty",     "type": "stock"},
    "smh":     {"symbol": "SMH",       "name": "VanEck Semi ETF",      "sector": "semiconductor", "type": "etf"},
    "sp500":   {"symbol": "^GSPC",     "name": "S&P 500",              "sector": "market",        "type": "index"},
    "cnya":    {"symbol": "CNYA",      "name": "iShares MSCI China",   "sector": "china",         "type": "etf"},
    "gldm":    {"symbol": "GLDM",      "name": "SPDR Gold MiniShares", "sector": "gold",          "type": "etf"},
    "euny":    {"symbol": "EUNY.DE",   "name": "iShares Euro Corp Bond","sector": "bonds",        "type": "etf"},
    "gazp":    {"symbol": "GAZP.ME",   "name": "Gazprom",              "sector": "energy",        "type": "stock"},
}

# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)


def signal_send(msg):
    """Send a message via Signal JSON-RPC."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {"account": SIGNAL_FROM, "recipient": [SIGNAL_TO], "message": msg},
            "id": "market-think",
        }).encode()
        req = urllib.request.Request(SIGNAL_RPC, data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        log(f"Signal alert sent ({len(msg)} chars)")
    except Exception as e:
        log(f"Signal send failed: {e}")


def call_ollama(system_prompt, user_prompt, temperature=0.5, max_tokens=4000, think=True):
    """Call Ollama for LLM analysis. think=True enables chain-of-thought."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"Model {OLLAMA_MODEL} not found")
                return None
    except Exception as e:
        log(f"Ollama health check failed: {e}")
        return None

    # /nothink disables CoT; omitting it enables deep reasoning
    prefix = "" if think else "/nothink\n"

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prefix + user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 24576,
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload,
        headers={"Content-Type": "application/json"})

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            # Strip <think>...</think> CoT block, keep only the analysis
            if "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            content = sanitize_llm_output(content)
            log(f"LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return content
    except Exception as e:
        log(f"Ollama call failed: {e}")
        return None


# ── Data fetcher ───────────────────────────────────────────────────────────

def fetch_ticker_data(ticker_info):
    """Fetch comprehensive data for one ticker via yfinance."""
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    symbol = ticker_info["symbol"]
    log(f"Fetching {symbol}...")

    try:
        t = yf.Ticker(symbol)
        fi = t.fast_info

        data = {
            "symbol": symbol,
            "name": ticker_info["name"],
            "sector": ticker_info["sector"],
            "type": ticker_info["type"],
        }

        # Current price
        current = float(fi.last_price) if hasattr(fi, 'last_price') and fi.last_price else None
        prev_close = float(fi.previous_close) if hasattr(fi, 'previous_close') and fi.previous_close else None

        if current is None:
            hist = t.history(period="5d")
            if not hist.empty:
                current = float(hist['Close'].iloc[-1])
                if len(hist) >= 2:
                    prev_close = float(hist['Close'].iloc[-2])

        if current is None:
            data["error"] = "no_price"
            return data

        data["price"] = round(current, 4)
        data["prev_close"] = round(prev_close, 4) if prev_close else None

        if prev_close and prev_close > 0:
            data["day_change_pct"] = round(((current - prev_close) / prev_close) * 100, 2)
        else:
            data["day_change_pct"] = 0

        # 30-day history for trends
        try:
            hist30 = t.history(period="1mo")
            if not hist30.empty:
                closes = [round(float(c), 4) for c in hist30['Close'].values]
                data["history_30d"] = closes
                data["high_30d"] = round(float(hist30['High'].max()), 4)
                data["low_30d"] = round(float(hist30['Low'].min()), 4)
                data["avg_volume_30d"] = int(hist30['Volume'].mean()) if hist30['Volume'].mean() > 0 else None
                if len(closes) >= 2:
                    data["change_30d_pct"] = round(((closes[-1] - closes[0]) / closes[0]) * 100, 2)
        except Exception:
            pass

        # Market cap and metadata
        try:
            data["market_cap"] = getattr(fi, 'market_cap', None)
            data["currency"] = getattr(fi, 'currency', 'USD') or 'USD'
            data["exchange"] = getattr(fi, 'exchange', '?') or '?'
            data["year_high"] = float(fi.year_high) if hasattr(fi, 'year_high') and fi.year_high else None
            data["year_low"] = float(fi.year_low) if hasattr(fi, 'year_low') and fi.year_low else None
        except Exception:
            pass

        log(f"  {ticker_info['name']}: {current:.4f} ({data.get('day_change_pct', 0):+.2f}% day, "
            f"{data.get('change_30d_pct', 'N/A')}% 30d)")
        return data

    except Exception as e:
        log(f"  {symbol}: ERROR — {e}")
        return {"symbol": symbol, "name": ticker_info["name"], "error": str(e)[:200]}


# ── Sector-specific deep analysis prompts ──────────────────────────────────

def get_ticker_prompt(slug, ticker_info, data):
    """Build a deep analysis prompt tailored to the asset's sector."""
    name = ticker_info["name"]
    sector = ticker_info["sector"]

    # Build price context
    price_ctx = f"Current price: {data.get('price', 'N/A')} {data.get('currency', 'USD')}"
    if data.get('day_change_pct') is not None:
        price_ctx += f", day change: {data['day_change_pct']:+.2f}%"
    if data.get('change_30d_pct') is not None:
        price_ctx += f", 30-day change: {data['change_30d_pct']:+.2f}%"
    if data.get('year_high') and data.get('year_low'):
        price_ctx += f"\n52-week range: {data['year_low']} – {data['year_high']}"
    if data.get('market_cap'):
        mc = data['market_cap']
        if mc and mc > 1e12:
            price_ctx += f"\nMarket cap: ${mc/1e12:.1f}T"
        elif mc and mc > 1e9:
            price_ctx += f"\nMarket cap: ${mc/1e9:.1f}B"

    # Load user profile
    profile_ctx = ""
    if PROFILE_FILE.exists():
        try:
            pf = json.load(open(PROFILE_FILE))
            profile_ctx = f"\nUser context: {pf.get('role', 'embedded Linux engineer')} tracking this for career/investment decisions."
        except Exception:
            pass

    # History context
    hist_ctx = ""
    if data.get("history_30d"):
        h = data["history_30d"]
        if len(h) >= 5:
            # Simple trend description
            start, end = h[0], h[-1]
            mid = h[len(h)//2]
            hist_ctx = f"\n30-day price path: {start} → {mid} (mid) → {end} (now)"

    system = f"""You are a senior financial intelligence analyst with deep expertise in {sector} markets.
Write ONLY in English. Provide strategic, deep analysis — not surface-level price commentary.
Think carefully about cause-and-effect, competitive dynamics, and second-order implications.
The reader is an embedded Linux / semiconductor engineer in Poland.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

    # ── Semiconductor-specific prompt ──
    if sector == "semiconductor":
        user = f"""Perform a deep strategic analysis of {name} ({data.get('symbol', slug)}):

{price_ctx}{hist_ctx}{profile_ctx}

Analyze the following dimensions:

1. RECENT CORPORATE DEVELOPMENTS (past 3-6 months)
   Layoffs, hiring surges, acquisitions, divestitures, leadership changes,
   restructuring, legal actions, earnings surprises.

2. TECHNOLOGY STRATEGY & ROADMAP
   New chip architectures, process node transitions, product launches.
   IP development, AI accelerator plans, automotive/ADAS initiatives.
   How does this affect demand for embedded Linux / camera / SoC engineers?

3. COMPETITIVE POSITIONING
   Market share vs competitors. Design win momentum.
   Customer concentration risks. Pricing power trends.
   Which business segments are growing vs shrinking?

4. SUPPLY CHAIN & GEOPOLITICS
   Fab capacity constraints or investments. Export control impact.
   China revenue exposure. CHIPS Act / EU Chips Act implications.
   TSMC dependency. Memory market cycle position.

5. HIRING & TALENT SIGNALS
   Is {name} expanding or contracting engineering teams?
   Which skill areas (kernel, firmware, camera, ADAS, AI, verification)?
   Poland/Europe office changes? Salary trend signals?

6. INVESTMENT THESIS
   Key catalysts and risks for next 6-12 months.
   Bull case vs bear case (2-3 sentences each).

Be SPECIFIC — name concrete events, products, executives, dates.
Target: 500-700 words of analysis. English only."""

    # ── Crypto prompt ──
    elif sector == "crypto":
        user = f"""Deep strategic analysis of {name}:

{price_ctx}{hist_ctx}{profile_ctx}

1. REGULATORY LANDSCAPE
   SEC actions, EU MiCA implementation, global regulatory shifts.
   ETF approval/flow status. Stablecoin regulation impact.

2. NETWORK & TECHNOLOGY
   Protocol upgrades, L2 scaling, developer activity metrics.
   DeFi TVL trends, NFT market state, ecosystem health.

3. INSTITUTIONAL ADOPTION
   ETF inflows/outflows, corporate treasury moves.
   Banking integration, payment processor adoption.

4. MACRO CORRELATION & FLOWS
   Correlation with equities, gold, bonds. Rate sensitivity.
   Mining economics, hash rate trends (for BTC).

5. RISK MATRIX
   Top 3 risks: regulatory, technical, macro.
   Exchange/custody risks, concentration risks.

Target: 400-500 words. English only."""

    # ── Tech / Telecom / Media / Specialty ──
    elif sector in ("tech", "telecom", "media", "specialty"):
        user = f"""Deep strategic analysis of {name}:

{price_ctx}{hist_ctx}{profile_ctx}

1. RECENT CORPORATE DEVELOPMENTS
   Major events past 3-6 months: M&A, restructuring, leadership changes.
   Earnings trajectory, guidance changes.

2. BUSINESS STRATEGY
   Where is {name} investing? Which divisions growing vs shrinking?
   New product/service launches. Partnership deals.

3. TECHNOLOGY & INNOVATION
   Key platform/product developments. R&D direction.
   AI strategy. Cloud/infrastructure moves.
   Relevance to embedded systems / Linux ecosystem?

4. COMPETITIVE DYNAMICS
   Market position, pricing power, customer retention.
   Competitive threats, disruptive risks.

5. CAREER RELEVANCE
   Is {name} hiring in Poland/Europe?
   Technology stack signals for embedded/camera/SoC engineers.

6. OUTLOOK
   Bull and bear case. Key catalysts next 12 months.

Target: 400-600 words. English only."""

    # ── Macro: market indices, gold, bonds, China, energy ──
    elif sector in ("market", "gold", "bonds", "china", "energy"):
        user = f"""Deep macro analysis for {name}:

{price_ctx}{hist_ctx}{profile_ctx}

1. CURRENT DRIVERS
   What macro forces are driving {name}'s recent performance?
   Monetary policy, fiscal stimulus, geopolitical events.

2. FORWARD INDICATORS
   Leading indicators for this asset class.
   Central bank signals, economic data releases, positioning data.

3. GEOPOLITICAL RISKS
   Trade wars, sanctions, political instability specific to {name}.
   Energy policy shifts, supply disruptions, alliance changes.

4. TECH SECTOR IMPACT
   How does {name}'s trajectory affect semiconductor / tech companies?
   Currency effects on European tech, input cost pressures, demand signals.
   Supply chain implications for hardware companies.

5. CAREER IMPLICATIONS
   How do trends in {sector} affect the tech job market in Poland/Europe?
   Capex cycles, FDI flows, regulatory environment.

Target: 300-400 words. English only."""

    else:
        user = f"""Strategic analysis of {name} ({sector}):

{price_ctx}{hist_ctx}{profile_ctx}

Cover: recent developments, competitive position, technology direction,
career/hiring relevance, risk assessment.
Target: 300-500 words. English only."""

    return system, user


# ── Per-ticker think ───────────────────────────────────────────────────────

def think_one_ticker(slug):
    """Deep chain-of-thought analysis of one ticker."""
    if slug not in TICKERS:
        log(f"Unknown ticker: {slug}")
        log(f"Available: {', '.join(sorted(TICKERS.keys()))}")
        sys.exit(1)

    ticker_info = TICKERS[slug]
    t_start = time.time()

    log(f"{'='*60}")
    log(f"MARKET THINK — {ticker_info['name']} ({ticker_info['symbol']})")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already analyzed today
    today = datetime.now().strftime("%Y%m%d")
    out_file = THINK_DIR / f"{slug}-{today}.json"
    if out_file.exists():
        log(f"Already analyzed today: {out_file.name}")
        return

    # 1. Fetch market data
    log("\n── Fetching market data ──")
    data = fetch_ticker_data(ticker_info)

    if data.get("error"):
        log(f"Data fetch issue: {data['error']} — proceeding with limited context")

    # 2. Deep LLM analysis with chain-of-thought
    log("\n── Deep analysis (chain-of-thought enabled) ──")
    system, user = get_ticker_prompt(slug, ticker_info, data)
    analysis = call_ollama(system, user, temperature=0.5, max_tokens=4000, think=True)

    if not analysis:
        log("LLM analysis failed")
        analysis = ""

    # 3. Build reference URLs for this ticker
    symbol = ticker_info["symbol"]
    ref_urls = {}
    if ticker_info["type"] == "crypto":
        ref_urls["yahoo"] = f"https://finance.yahoo.com/quote/{symbol}/"
        slug_lower = slug.lower()
        coin_map = {"btc": "bitcoin", "eth": "ethereum"}
        if slug_lower in coin_map:
            ref_urls["coingecko"] = f"https://www.coingecko.com/en/coins/{coin_map[slug_lower]}"
    else:
        ref_urls["yahoo"] = f"https://finance.yahoo.com/quote/{symbol}/"
        clean_sym = symbol.split(".")[0].replace("^", "")
        ref_urls["google"] = f"https://www.google.com/finance/quote/{clean_sym}"
        if ticker_info["type"] == "stock":
            ref_urls["news"] = f"https://finance.yahoo.com/quote/{symbol}/news/"

    # 4. Save
    elapsed = time.time() - t_start
    output = {
        "meta": {
            "ticker": slug,
            "symbol": ticker_info["symbol"],
            "name": ticker_info["name"],
            "sector": ticker_info["sector"],
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "duration_s": round(elapsed),
        },
        "data": data,
        "analysis": analysis,
        "reference_urls": ref_urls,
    }

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes) in {elapsed:.0f}s")


# ── Summary aggregation ───────────────────────────────────────────────────

def think_summary():
    """Aggregate all daily ticker analyses into an executive brief."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"MARKET THINK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    # Read all today's ticker analyses
    analyses = []
    missing = []
    for slug in sorted(TICKERS.keys()):
        think_file = THINK_DIR / f"{slug}-{today}.json"
        if think_file.exists():
            try:
                data = json.load(open(think_file))
                analyses.append(data)
            except Exception as e:
                log(f"  {slug}: read error — {e}")
                missing.append(slug)
        else:
            missing.append(slug)

    log(f"\nFound {len(analyses)}/{len(TICKERS)} ticker analyses for today")
    if missing:
        log(f"Missing: {', '.join(missing)}")

    if len(analyses) < 5:
        log("Not enough analyses to summarize (need ≥5). Skipping.")
        return

    # Build combined analysis for summary prompt
    summaries = []
    for a in analyses:
        name = a["meta"]["name"]
        sector = a["meta"]["sector"]
        analysis_text = a.get("analysis", "")[:600]
        d = a.get("data", {})
        price_info = ""
        if d.get("price"):
            price_info = f" | {d['price']} {d.get('currency','USD')} ({d.get('day_change_pct', 0):+.2f}%)"
        summaries.append(f"### {name} ({sector}){price_info}\n{analysis_text}")

    ticker_block = "\n\n---\n\n".join(summaries)

    system = """You are the Chief Market Intelligence Officer. Write ONLY in English.
Synthesize per-ticker deep analyses into a concise executive market brief.
Focus on cross-cutting themes, sector trends, and strategic implications.
The reader is an embedded Linux engineer in Poland tracking career and investments."""

    user = f"""Today's deep-dive analyses across {len(analyses)} assets:

{ticker_block}

Create an EXECUTIVE MARKET INTELLIGENCE BRIEF:

1. TOP DEVELOPMENTS TODAY (3-5 bullet points)
   Most significant cross-market events and their implications.

2. SEMICONDUCTOR SECTOR OUTLOOK
   Consolidated view of AMD, Intel, ARM, ASML, TSMC, Samsung, SMH.
   Net hiring direction, technology shifts, competitive dynamics.

3. MACRO CROSS-CURRENTS
   How bonds, gold, China, energy, and crypto affect tech sector.

4. STRATEGIC RISKS & OPPORTUNITIES
   Top 3 risks and top 3 opportunities across all tracked assets.

5. CAREER ACTION ITEMS (2-3 sentences)
   Specific implications for embedded Linux / semiconductor careers in Poland.

Be concise but specific. Target: 600-900 words. English only."""

    log("\n── Running summary analysis (chain-of-thought) ──")
    summary = call_ollama(system, user, temperature=0.4, max_tokens=5000, think=True)

    if not summary:
        log("Summary LLM call failed")
        return

    # Save summary
    elapsed = time.time() - t_start
    output = {
        "meta": {
            "type": "summary",
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "tickers_analyzed": len(analyses),
            "duration_s": round(elapsed),
        },
        "summary": summary,
        "tickers": [a["meta"]["ticker"] for a in analyses],
    }

    out_file = THINK_DIR / f"summary-{today}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    # Update latest symlink
    latest = THINK_DIR / "latest-summary.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    # Regenerate dashboard
    try:
        import subprocess
        subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                       timeout=120, capture_output=True)
        log("Dashboard regenerated")
    except Exception as e:
        log(f"Dashboard regen failed: {e}")

    # Cleanup old think files (keep 14 days ≈ 280 files)
    all_files = sorted(THINK_DIR.glob("*.json"))
    if len(all_files) > 300:
        for f in all_files[:-300]:
            if f.name.startswith("latest"):
                continue
            f.unlink()
            log(f"  Pruned: {f.name}")

    log(f"\nSummary complete in {elapsed:.0f}s")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep per-ticker market intelligence")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ticker', '-t', help="Ticker slug to analyze")
    group.add_argument('--summary', '-s', action='store_true', help="Generate daily executive summary")
    group.add_argument('--list', '-l', action='store_true', help="List available tickers")
    args = parser.parse_args()

    if args.list:
        for slug, info in sorted(TICKERS.items()):
            print(f"  {slug:10s}  {info['symbol']:12s}  {info['name']:24s}  [{info['sector']}]")
        return

    if args.summary:
        think_summary()
    else:
        think_one_ticker(args.ticker)


if __name__ == "__main__":
    main()
