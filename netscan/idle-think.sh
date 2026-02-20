#!/bin/bash
# idle-think.sh — Background LLM thinking during idle time
# Generates research notes, weekly summaries, trend analysis, cross-feed insights,
# career intelligence, web crawl digests, and self-learning reviews
# by reviewing recent digests, repo issues, and private career profile.
#
# Usage:
#   idle-think.sh                  (pick next task from rotation)
#   idle-think.sh --task weekly    (force specific task)
#   idle-think.sh --task trends
#   idle-think.sh --task crossfeed
#   idle-think.sh --task research
#   idle-think.sh --task career    (career-aware analysis)
#   idle-think.sh --task crawl     (fetch & analyze web sources)
#   idle-think.sh --task learn     (self-review of past notes)
#   idle-think.sh --task signal    (smart Signal filter — only pings if something matches)
#
# Schedule:
#   Mon=weekly, Tue=crossfeed, Wed=career, Thu=crawl,
#   Fri=trends, Sat=research, Sun=learn
#   signal runs daily at 19:00 via separate cron (not in rotation)
#
# Guards: Checks if Ollama is already busy (lore-digest / repo-watch running)
#         before starting. Exits gracefully if system is occupied.
#
# Location on bc250: /opt/netscan/idle-think.sh
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
DATA_DIR="/opt/netscan/data"
THINK_DIR="$DATA_DIR/think"
PROFILE_JSON="${SCRIPT_DIR}/profile.json"
PROFILE_PRIVATE="${SCRIPT_DIR}/profile-private.json"
WATCHLIST_JSON="${SCRIPT_DIR}/watchlist.json"
DIGEST_FEEDS="${SCRIPT_DIR}/digest-feeds.json"
REPO_FEEDS="${SCRIPT_DIR}/repo-feeds.json"

mkdir -p "$THINK_DIR"

TASK=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --task) TASK="$2"; shift 2 ;;
        *)      echo "Usage: $0 [--task weekly|trends|crossfeed|research|career|crawl|learn|signal]"; exit 1 ;;
    esac
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle-think starting"

# ─── Guard: don't compete with digest/watch for GPU ───
if pgrep -f "lore-digest.sh" >/dev/null 2>&1 || pgrep -f "repo-watch.sh" >/dev/null 2>&1; then
    echo "  Another script is using the GPU — skipping idle-think"
    exit 0
fi

# Check if Ollama is loaded with a model (means something is running)
OLLAMA_PS=$(curl -s http://localhost:11434/api/ps 2>/dev/null || echo '{"models":[]}')
RUNNING=$(echo "$OLLAMA_PS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('models',[])))" 2>/dev/null || echo "0")
# Having a model loaded is fine — it means Ollama is ready. We only skip if digest/watch are running.

python3 - "$TASK" "$THINK_DIR" "$DATA_DIR" "$PROFILE_JSON" "$DIGEST_FEEDS" "$REPO_FEEDS" "$PROFILE_PRIVATE" "$WATCHLIST_JSON" << 'PYEOF'
import sys, os, json, glob, time, hashlib
import urllib.request
from datetime import datetime, timedelta, timezone

TASK_ARG = sys.argv[1]
THINK_DIR = sys.argv[2]
DATA_DIR = sys.argv[3]
PROFILE_JSON = sys.argv[4]
DIGEST_FEEDS_PATH = sys.argv[5]
REPO_FEEDS_PATH = sys.argv[6]
PROFILE_PRIVATE_PATH = sys.argv[7] if len(sys.argv) > 7 else ""
WATCHLIST_PATH = sys.argv[8] if len(sys.argv) > 8 else ""

# ─── Load configs ───

PROFILE = {}
if os.path.exists(PROFILE_JSON):
    with open(PROFILE_JSON) as f:
        PROFILE = json.load(f)

PROFILE_PRIVATE = {}
if PROFILE_PRIVATE_PATH and os.path.exists(PROFILE_PRIVATE_PATH):
    with open(PROFILE_PRIVATE_PATH) as f:
        PROFILE_PRIVATE = json.load(f)
    print(f"  Loaded private profile: {PROFILE_PRIVATE_PATH}")

WATCHLIST = {"items": [], "resolved": []}
if WATCHLIST_PATH and os.path.exists(WATCHLIST_PATH):
    with open(WATCHLIST_PATH) as f:
        WATCHLIST = json.load(f)
    active = [i for i in WATCHLIST.get("items", []) if i.get("status") == "active"]
    print(f"  Loaded watchlist: {len(active)} active items")

DIGEST_FEEDS = {}
if os.path.exists(DIGEST_FEEDS_PATH):
    with open(DIGEST_FEEDS_PATH) as f:
        DIGEST_FEEDS = json.load(f)

REPO_FEEDS = {}
if os.path.exists(REPO_FEEDS_PATH):
    with open(REPO_FEEDS_PATH) as f:
        REPO_FEEDS = json.load(f)

DASHBOARD_URL = PROFILE.get("dashboard_url", "http://192.168.3.151:8888")
SIGNAL_CFG = PROFILE.get("signal", {})
SIGNAL_RPC = SIGNAL_CFG.get("rpc", "http://127.0.0.1:8080/api/v1/rpc")
SIGNAL_FROM = SIGNAL_CFG.get("from", "+48532825716")
SIGNAL_TO = SIGNAL_CFG.get("to", "+48503326388")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3-14b-abl-nothink:latest"

# ─── Helpers ───

def call_ollama(system_prompt, user_prompt, temperature=0.4, max_tokens=3000, label="think"):
    """Call Ollama for thinking tasks."""
    # Health check
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        if OLLAMA_MODEL not in models:
            print(f"  [{label}] Model {OLLAMA_MODEL} not found")
            return None
    except:
        print(f"  [{label}] Ollama not reachable")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    })

    try:
        req = urllib.request.Request(
            OLLAMA_CHAT, data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=600)
        result = json.loads(resp.read())
        elapsed = time.time() - t0
        content = result.get("message", {}).get("content", "")
        tokens = result.get("eval_count", 0)
        tps = tokens / elapsed if elapsed > 0 else 0
        print(f"  [{label}] OK {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
        return content
    except Exception as ex:
        print(f"  [{label}] Failed: {ex}")
        return None

def signal_send(msg):
    """Send Signal message."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {"account": SIGNAL_FROM, "recipient": [SIGNAL_TO], "message": msg},
            "id": "idle-think"
        })
        req = urllib.request.Request(SIGNAL_RPC, data=payload.encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except:
        return False

def load_recent_digests(days=7):
    """Load recent digest bulletins across all feeds."""
    digests = []
    for fid, fcfg in DIGEST_FEEDS.items():
        if not isinstance(fcfg, dict):
            continue
        feed_dir = os.path.join(DATA_DIR, fcfg["data_dir"])
        for f in sorted(glob.glob(os.path.join(feed_dir, "digest-*.json")), reverse=True)[:days]:
            try:
                with open(f) as fh:
                    d = json.load(fh)
                    digests.append({
                        "feed": fid, "feed_name": fcfg["name"],
                        "date": d.get("date", ""),
                        "threads_analyzed": d.get("threads_analyzed", 0),
                        "total_messages": d.get("total_messages", 0),
                        "bulletin": d.get("bulletin", "")[:3000],
                        "top_threads": [
                            {"subject": t["subject"], "score": t["score"],
                             "keywords": t.get("keywords", [])}
                            for t in d.get("top_threads", [])[:10]
                        ]
                    })
            except:
                pass
    return digests

def load_recent_issues():
    """Load recent repo watch results."""
    issues = []
    for rid, rcfg in REPO_FEEDS.items():
        if not isinstance(rcfg, dict):
            continue
        latest_path = os.path.join(DATA_DIR, rcfg["data_dir"], "latest.json")
        if os.path.exists(latest_path):
            try:
                with open(latest_path) as f:
                    data = json.load(f)
                    issues.append({
                        "repo": rid, "repo_name": rcfg["name"],
                        "checked": data.get("checked", ""),
                        "interesting": data.get("interesting", [])[:15],
                    })
            except:
                pass
    return issues

def save_note(task_type, title, content, context=None):
    """Save a thinking note."""
    dt = datetime.now()
    note = {
        "type": task_type,
        "title": title,
        "content": content,
        "generated": dt.isoformat(timespec="seconds"),
        "model": OLLAMA_MODEL,
        "context": context or {},
    }
    fname = f"note-{task_type}-{dt.strftime('%Y%m%d-%H%M')}.json"
    path = os.path.join(THINK_DIR, fname)
    with open(path, "w") as f:
        json.dump(note, f, indent=2)
    print(f"  Saved: {path}")

    # Also update latest notes index
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    index = []
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                index = json.load(f)
        except:
            pass
    index.insert(0, {
        "file": fname, "type": task_type, "title": title,
        "generated": note["generated"], "chars": len(content),
    })
    index = index[:50]  # keep last 50 notes
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    return note


def career_context_block():
    """Build a formatted career context string from profile-private.json for LLM prompts."""
    if not PROFILE_PRIVATE:
        return ""
    ident = PROFILE_PRIVATE.get("identity", {})
    role = PROFILE_PRIVATE.get("current_role", {})
    ctx = PROFILE_PRIVATE.get("career_context", {})
    phd = PROFILE_PRIVATE.get("phd_interest", {})
    t5 = PROFILE_PRIVATE.get("t5_requirements", {})
    strengths = PROFILE_PRIVATE.get("strengths_for_llm_context", [])
    watch = PROFILE_PRIVATE.get("watch_topics", {})

    lines = []
    lines.append(f"=== CAREER CONTEXT (confidential — for analysis only) ===")
    lines.append(f"Role: {ident.get('title', '')} ({ident.get('career_level', '')}) at {ident.get('employer', '')}")
    lines.append(f"Division: {ident.get('division', '')}")
    lines.append(f"Team: {role.get('team', '')} — {role.get('program', '')}")
    lines.append(f"Customers: {', '.join(role.get('customers', []))}")
    lines.append(f"Goal: {ident.get('career_level', '')} → {ident.get('target_level', '')}")
    lines.append(f"Preference: {ctx.get('preference', '')}")
    lines.append(f"Situation: {ctx.get('situation', '')}")
    if ctx.get("opportunities"):
        lines.append(f"Opportunities: {'; '.join(ctx['opportunities'][:3])}")
    if ctx.get("growth_areas"):
        lines.append(f"Growth areas: {'; '.join(ctx['growth_areas'])}")
    if phd.get("thesis_topics"):
        lines.append(f"PhD topics of interest: {'; '.join(phd['thesis_topics'][:2])}")
    if watch.get("career_relevant"):
        lines.append(f"Career keywords: {', '.join(watch['career_relevant'][:10])}")
    if strengths:
        lines.append(f"Note: {'; '.join(strengths[:2])}")
    return "\n".join(lines)


def load_recent_notes(count=10, task_type=None):
    """Load recent thinking notes for self-learning."""
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    if not os.path.exists(index_path):
        return []
    try:
        with open(index_path) as f:
            index = json.load(f)
    except:
        return []
    if task_type:
        index = [n for n in index if n.get("type") == task_type]
    notes = []
    for entry in index[:count]:
        note_path = os.path.join(THINK_DIR, entry["file"])
        if os.path.exists(note_path):
            try:
                with open(note_path) as f:
                    notes.append(json.load(f))
            except:
                pass
    return notes


# ─── Task definitions ───

def task_weekly():
    """Generate weekly summary across all feeds and repos."""
    print("\n[TASK] Weekly Summary")
    digests = load_recent_digests(7)
    issues = load_recent_issues()

    if not digests and not issues:
        print("  No data for weekly summary")
        return

    # Collect digest summaries
    digest_text = ""
    for d in digests:
        digest_text += f"\n--- {d['feed_name']} ({d['date']}) ---\n"
        digest_text += f"Messages: {d['total_messages']}, Analyzed: {d['threads_analyzed']}\n"
        for t in d["top_threads"][:5]:
            digest_text += f"  • {t['subject']} (score {t['score']})\n"
        digest_text += "\n"

    issue_text = ""
    for i in issues:
        issue_text += f"\n--- {i['repo_name']} ---\n"
        for item in i["interesting"][:8]:
            issue_text += f"  • #{item['id']}: {item['title']} (score {item['score']})\n"

    user_interests = "\n".join(f"- {i}" for i in PROFILE.get("interests", []))
    career = career_context_block()

    system = f"""You are a research assistant for an embedded Linux / multimedia developer.
You produce a weekly research intelligence briefing. Be technical, concise, and insightful.
Focus on actionable insights and emerging trends relevant to the developer's work.

Developer's interests:
{user_interests}

{career}"""

    prompt = f"""Based on this week's monitoring data, produce a WEEKLY RESEARCH BRIEFING.

=== MAILING LIST DIGESTS ===
{digest_text if digest_text else "(no digest data this week)"}

=== REPOSITORY ACTIVITY ===
{issue_text if issue_text else "(no repo data yet)"}

Structure your briefing as:

📋 WEEKLY BRIEFING — [date range]

🔥 TOP DEVELOPMENTS (3-5 most significant items across all sources)
Each with 2-3 sentences explaining why it matters.

📈 TRENDS
Patterns you notice across multiple sources. What subsystems are most active?
What hardware platforms are getting the most attention?

🎯 CAREER RELEVANCE (what connects to the developer's professional context)
Highlight items that could be useful for their domain expertise growth,
potential conference talks, or technical leadership visibility.

💡 ACTION ITEMS / OPPORTUNITIES
Things the developer might want to:
- Review or test
- Contribute to
- Be aware of for their projects

🔮 OUTLOOK
What to watch next week based on current activity.

Keep total output under 3000 chars. Be specific with names, versions, functions."""

    result = call_ollama(system, prompt, temperature=0.3, max_tokens=2500, label="weekly")
    if result:
        note = save_note("weekly", f"Weekly Briefing — {datetime.now().strftime('%d %b %Y')}", result,
                         {"digests": len(digests), "repos": len(issues)})
        return note


def task_trends():
    """Analyze trends across recent digests."""
    print("\n[TASK] Trend Analysis")
    digests = load_recent_digests(14)

    if len(digests) < 2:
        print("  Not enough data for trend analysis (need ≥2 digests)")
        return

    # Collect all keywords across digests
    all_keywords = {}
    all_subjects = []
    for d in digests:
        for t in d.get("top_threads", []):
            all_subjects.append(f"[{d['feed_name']}] {t['subject']}")
            for kw in t.get("keywords", []):
                all_keywords[kw] = all_keywords.get(kw, 0) + 1

    top_keywords = sorted(all_keywords.items(), key=lambda x: -x[1])[:20]
    subjects_text = "\n".join(all_subjects[:40])
    keywords_text = "\n".join(f"  {kw}: {count}x" for kw, count in top_keywords)

    user_interests = "\n".join(f"- {i}" for i in PROFILE.get("interests", []))
    career = career_context_block()

    system = f"""You are a technical trend analyst for Linux kernel and multimedia development.
Identify patterns, emerging work areas, and notable shifts in development activity.
Also note trends relevant to the developer's career growth and domain expertise.

Developer's interests:
{user_interests}

{career}"""

    prompt = f"""Analyze these development trends from the past 2 weeks:

MOST ACTIVE KEYWORDS:
{keywords_text}

RECENT THREAD SUBJECTS ({len(all_subjects)} total):
{subjects_text}

Produce a TREND ANALYSIS:

📊 TREND ANALYSIS — {datetime.now().strftime('%d %b %Y')}

🔄 HOT AREAS (subsystems/drivers getting most attention)
🆕 EMERGING (new topics that weren't active before)
📉 QUIETING (areas that were active but have slowed)
🔗 CONNECTIONS (related activity across different subsystems)

For each, explain WHY it matters for embedded Linux / multimedia development.
Keep under 2000 chars. Be data-driven — reference specific thread subjects and keyword counts."""

    result = call_ollama(system, prompt, temperature=0.4, max_tokens=2000, label="trends")
    if result:
        return save_note("trends", f"Trend Analysis — {datetime.now().strftime('%d %b %Y')}", result,
                         {"digests_analyzed": len(digests), "keywords": len(all_keywords)})


def task_crossfeed():
    """Find connections across different feeds and repos."""
    print("\n[TASK] Cross-feed Insights")
    digests = load_recent_digests(7)
    issues = load_recent_issues()

    if len(digests) < 1:
        print("  No digest data for cross-feed analysis")
        return

    combined = ""
    for d in digests:
        combined += f"\n=== {d['feed_name']} ({d['date']}) ===\n"
        combined += d["bulletin"][:2000] + "\n"

    for i in issues:
        combined += f"\n=== {i['repo_name']} issues ===\n"
        for item in i["interesting"][:10]:
            combined += f"  #{item['id']}: {item['title']}\n"

    system = """You are a systems-level analyst who finds connections between
kernel development, userspace tools, and multimedia frameworks.
Identify where changes in one project affect or relate to another."""

    prompt = f"""Review this week's activity across multiple sources and find CROSS-PROJECT CONNECTIONS:

{combined}

Produce CROSS-FEED INSIGHTS:

🔗 CROSS-FEED INSIGHTS — {datetime.now().strftime('%d %b %Y')}

For each connection found:
• What's happening in Source A and Source B
• Why they're related
• What the developer should know

Also note any:
- Kernel changes that will need userspace tool updates
- GStreamer/FFmpeg changes that relate to kernel driver work
- Hardware support changes that span multiple subsystems

Keep under 2000 chars. Focus on actionable connections."""

    result = call_ollama(system, prompt, temperature=0.4, max_tokens=2000, label="crossfeed")
    if result:
        return save_note("crossfeed", f"Cross-feed Insights — {datetime.now().strftime('%d %b %Y')}", result,
                         {"sources": len(digests) + len(issues)})


def task_research():
    """Pick an interesting topic from recent activity and do a mini research dive."""
    print("\n[TASK] Research Dive")
    digests = load_recent_digests(7)

    if not digests:
        print("  No digest data for research")
        return

    # Find the most-discussed topics
    topic_scores = {}
    for d in digests:
        for t in d.get("top_threads", []):
            for kw in t.get("keywords", []):
                topic_scores[kw] = topic_scores.get(kw, 0) + t["score"]

    # Pick top topic that hasn't been researched recently
    existing_notes = glob.glob(os.path.join(THINK_DIR, "note-research-*.json"))
    recent_topics = set()
    for nf in existing_notes[-5:]:
        try:
            with open(nf) as f:
                n = json.load(f)
                recent_topics.add(n.get("context", {}).get("topic", ""))
        except:
            pass

    top_topics = sorted(topic_scores.items(), key=lambda x: -x[1])
    topic = None
    for kw, score in top_topics:
        if kw not in recent_topics and len(kw) > 2:
            topic = kw
            break

    if not topic:
        topic = top_topics[0][0] if top_topics else "embedded Linux camera"

    # Gather context about this topic from digests
    context_text = ""
    for d in digests:
        for t in d.get("top_threads", []):
            if topic.lower() in " ".join(t.get("keywords", [])).lower():
                context_text += f"• [{d['feed_name']}] {t['subject']} (score {t['score']})\n"

    user_interests = "\n".join(f"- {i}" for i in PROFILE.get("interests", []))
    hardware = "\n".join(f"- {h}" for h in PROFILE.get("hardware", []))
    career = career_context_block()

    system = f"""You are a technical research assistant specializing in Linux kernel and multimedia.
Write a focused research note that helps a developer understand a topic in depth.
Connect findings to the developer's professional context where relevant.

Developer's context:
{user_interests}

Hardware:
{hardware}

{career}"""

    prompt = f"""Research topic: **{topic}**

Recent activity related to this topic:
{context_text if context_text else f"(General interest in {topic})"}

Write a RESEARCH NOTE:

🔬 RESEARCH NOTE: {topic.upper()}

1. BACKGROUND: Brief context on what this is and why it matters
2. CURRENT STATE: What's happening right now in kernel/userspace
3. KEY PLAYERS: Who maintains this, who's driving changes
4. RELEVANCE: How this connects to embedded Linux camera/multimedia work
5. PRACTICAL: What a developer should know to work with this

Keep under 2500 chars. Be specific with function names, driver names, kernel configs.
Focus on practical knowledge, not Wikipedia-style overview."""

    result = call_ollama(system, prompt, temperature=0.5, max_tokens=2500, label="research")
    if result:
        return save_note("research", f"Research: {topic}", result, {"topic": topic})


def task_career():
    """Career-focused analysis: connect monitoring data to professional growth."""
    print("\n[TASK] Career Intelligence")
    if not PROFILE_PRIVATE:
        print("  No private profile — skipping career task")
        return

    digests = load_recent_digests(7)
    issues = load_recent_issues()
    recent_notes = load_recent_notes(5)

    # Gather recent monitoring data
    data_text = ""
    for d in digests:
        data_text += f"\n--- {d['feed_name']} ({d['date']}) ---\n"
        for t in d["top_threads"][:5]:
            data_text += f"  • {t['subject']} (score {t['score']}, kw: {', '.join(t.get('keywords', [])[:3])})\n"
    for i in issues:
        data_text += f"\n--- {i['repo_name']} ---\n"
        for item in i["interesting"][:8]:
            data_text += f"  • #{item['id']}: {item['title']} (score {item['score']})\n"

    # Include recent notes summaries for continuity
    notes_text = ""
    for n in recent_notes[:3]:
        notes_text += f"\n[{n.get('type', '?')} — {n.get('generated', '')[:10]}] {n.get('title', '')}\n"
        notes_text += n.get("content", "")[:500] + "\n"

    career = career_context_block()
    ctx = PROFILE_PRIVATE.get("career_context", {})
    t5 = PROFILE_PRIVATE.get("t5_requirements", {})
    phd = PROFILE_PRIVATE.get("phd_interest", {})

    t5_tech = "\n".join(f"- {r}" for r in t5.get("technical", []))
    t5_lead = "\n".join(f"- {r}" for r in t5.get("leadership", []))
    phd_topics = "\n".join(f"- {t}" for t in phd.get("thesis_topics", []))

    system = f"""You are a career intelligence advisor for an automotive embedded Linux engineer.
You analyze technical activity for career-relevant insights.
Your analysis is PRIVATE and direct — speak frankly about opportunities and gaps.

{career}

T5 REQUIREMENTS (technical):
{t5_tech}

T5 REQUIREMENTS (leadership):
{t5_lead}

PhD topics of interest:
{phd_topics}"""

    prompt = f"""Analyze this week's technical activity through the lens of career growth.

=== RECENT MONITORING DATA ===
{data_text if data_text else "(no recent data)"}

=== RECENT ANALYSIS NOTES ===
{notes_text if notes_text else "(no recent notes)"}

Produce a CAREER INTELLIGENCE BRIEF:

🎯 CAREER INTEL — {datetime.now().strftime('%d %b %Y')}

📌 T5-RELEVANT ITEMS
Activity from monitoring that directly relates to T5 requirements:
- Domain authority opportunities (areas to deepen or demonstrate expertise)
- Systems thinking angles (cross-subsystem connections)
- Innovation hooks (potential whitepapers, talks, or contributions)

📊 INDUSTRY POSITIONING
What's the industry doing that affects the developer's domain?
ADAS trends, camera tech shifts, SoC platform changes.

🎓 PHD CONNECTIONS
Any monitoring items that connect to the thesis topics?
New papers, kernel changes, or industry developments relevant to the PhD.

💡 SUGGESTED ACTIONS (2-3 concrete things)
What should the developer do this week? Be specific:
- Review a particular patch series
- Write a short whitepaper on topic X
- Reach out to maintainer Y about Z

Keep under 2500 chars. Be direct and actionable, not generic."""

    result = call_ollama(system, prompt, temperature=0.4, max_tokens=2500, label="career")
    if result:
        return save_note("career", f"Career Intel — {datetime.now().strftime('%d %b %Y')}", result,
                         {"digests": len(digests), "repos": len(issues)})


def task_crawl():
    """Fetch and analyze crawl target URLs for career-relevant content."""
    print("\n[TASK] Web Crawl & Analyze")
    sources = PROFILE_PRIVATE.get("crawl_sources", {})
    if not sources:
        print("  No crawl sources configured — skipping")
        return

    # Collect all sources into a flat list
    all_sources = []
    for category, items in sources.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            all_sources.extend(items)

    if not all_sources:
        print("  No valid crawl sources found")
        return

    # Pick 2-3 sources to crawl (rotate based on day)
    day_offset = datetime.now().timetuple().tm_yday % len(all_sources)
    selected = []
    for i in range(min(3, len(all_sources))):
        idx = (day_offset + i) % len(all_sources)
        selected.append(all_sources[idx])

    # Fetch pages
    crawl_results = []
    for src in selected:
        url = src.get("url", "")
        label = src.get("label", url)
        print(f"  Crawling: {label} ({url})")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; bc250-bot/1.0)"
            })
            resp = urllib.request.urlopen(req, timeout=20)
            raw = resp.read().decode("utf-8", errors="replace")
            # Strip HTML tags for rough text extraction
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            # Keep first 3000 chars
            crawl_results.append({
                "label": label,
                "url": url,
                "what": src.get("what", ""),
                "text": text[:3000]
            })
            print(f"    Got {len(text)} chars")
        except Exception as ex:
            print(f"    Failed: {ex}")

    if not crawl_results:
        print("  No pages fetched successfully")
        return

    crawl_text = ""
    for cr in crawl_results:
        crawl_text += f"\n=== {cr['label']} ({cr['what']}) ===\n"
        crawl_text += f"URL: {cr['url']}\n"
        crawl_text += cr["text"][:2000] + "\n"

    career = career_context_block()
    watch_topics = PROFILE_PRIVATE.get("watch_topics", {})
    career_kw = watch_topics.get("career_relevant", [])
    industry_kw = watch_topics.get("industry_tracking", [])

    system = f"""You are a web intelligence analyst for an automotive embedded Linux engineer.
You extract career-relevant insights from web pages.

{career}

Career keywords to watch: {', '.join(career_kw[:8])}
Industry keywords: {', '.join(industry_kw[:8])}"""

    prompt = f"""Analyze these freshly crawled web pages for relevant content:

{crawl_text}

Produce a CRAWL DIGEST:

🌐 CRAWL DIGEST — {datetime.now().strftime('%d %b %Y')}

For each source, extract:
- KEY FINDINGS relevant to the developer's domain
- NEWS items (product launches, announcements, regulation changes)
- TECHNICAL items (new drivers, patches, tools, standards)

Then a combined:
🎯 MOST RELEVANT ITEMS (top 3-5 across all sources)
Each with why it matters and suggested follow-up.

Skip irrelevant content. If a page has nothing useful, say so briefly.
Keep under 2500 chars. Focus on actionable intelligence."""

    result = call_ollama(system, prompt, temperature=0.3, max_tokens=2500, label="crawl")
    if result:
        return save_note("crawl", f"Crawl Digest — {datetime.now().strftime('%d %b %Y')}", result,
                         {"sources": [s["label"] for s in selected],
                          "fetched": len(crawl_results)})


def task_learn():
    """Self-learning: review accumulated notes, identify patterns, suggest improvements."""
    print("\n[TASK] Self-Learning Review")
    all_notes = load_recent_notes(15)

    if len(all_notes) < 3:
        print("  Not enough notes for self-learning (need >= 3)")
        return

    # Summarize what we've been generating
    notes_summary = ""
    type_counts = {}
    all_topics = []
    for n in all_notes:
        ntype = n.get("type", "unknown")
        type_counts[ntype] = type_counts.get(ntype, 0) + 1
        notes_summary += f"\n[{ntype} — {n.get('generated', '')[:10]}] {n.get('title', '')}\n"
        notes_summary += n.get("content", "")[:800] + "\n---\n"
        # Collect topics from research notes
        topic = n.get("context", {}).get("topic")
        if topic:
            all_topics.append(topic)

    type_text = ", ".join(f"{t}: {c}" for t, c in sorted(type_counts.items()))

    career = career_context_block()
    user_interests = "\n".join(f"- {i}" for i in PROFILE.get("interests", []))

    system = f"""You are a meta-cognitive assistant — you analyze your own outputs to improve.
You review notes generated by an automated research system and provide feedback
on how to be more useful. Be self-critical and constructive.

Developer's interests:
{user_interests}

{career}"""

    prompt = f"""Review these {len(all_notes)} recent notes generated by the system:

Note distribution: {type_text}
Research topics covered: {', '.join(all_topics) if all_topics else 'none tracked'}

=== RECENT NOTES ===
{notes_summary}

Produce a LEARNING REVIEW:

🧠 LEARNING REVIEW — {datetime.now().strftime('%d %b %Y')}

📊 COVERAGE ASSESSMENT
- What topics are well-covered? What's missing?
- Are we spending too much time on any area?
- What's the quality trend? (improving, declining, stale?)

🕳️ BLIND SPOTS
- Topics relevant to the developer but NOT covered in recent notes
- Career-relevant areas we should be tracking but aren't
- Technical depth gaps (too surface-level on important topics)

🔧 IMPROVEMENT SUGGESTIONS
- Specific topics to research next
- Types of analysis that would be more useful
- Any repetitive patterns to break

📋 SUGGESTED NEXT TASKS (3-5 specific items)
Concrete tasks the system should prioritize in coming days.

Keep under 2500 chars. Be specific and constructive."""

    result = call_ollama(system, prompt, temperature=0.5, max_tokens=2500, label="learn")
    if result:
        return save_note("learn", f"Learning Review — {datetime.now().strftime('%d %b %Y')}", result,
                         {"notes_reviewed": len(all_notes), "types": type_counts})


def task_signal():
    """Smart Signal filter: review all recent data, ping ONLY if something matches watchlist/interests.
    Also updates the watchlist with new discoveries and resolved items."""
    print("\n[TASK] Signal Filter")

    # Load everything: recent notes, digests, issues
    recent_notes = load_recent_notes(10)
    digests = load_recent_digests(3)
    issues = load_recent_issues()

    if not recent_notes and not digests and not issues:
        print("  No data to filter — skipping")
        return

    # Build watchlist text
    active_items = [i for i in WATCHLIST.get("items", []) if i.get("status") == "active"]
    watchlist_text = ""
    for i, item in enumerate(active_items, 1):
        watchlist_text += f"  {i}. {item['topic']}"
        if item.get("why"):
            watchlist_text += f" — {item['why']}"
        watchlist_text += "\n"

    # Build data summary (compact — we only need enough for the LLM to spot matches)
    data_text = ""

    # Recent notes (titles + key excerpts)
    if recent_notes:
        data_text += "\n=== RECENT ANALYSIS NOTES ===\n"
        for n in recent_notes[:6]:
            data_text += f"[{n.get('type', '?')}] {n.get('title', '')}\n"
            # First 600 chars of content for matching
            data_text += n.get("content", "")[:600] + "\n---\n"

    # Digest top threads
    if digests:
        data_text += "\n=== RECENT MAILING LIST THREADS ===\n"
        for d in digests:
            for t in d.get("top_threads", [])[:8]:
                data_text += f"  • [{d['feed_name']}] {t['subject']} (score {t['score']})\n"

    # Repo issues
    if issues:
        data_text += "\n=== RECENT REPO ACTIVITY ===\n"
        for i in issues:
            for item in i["interesting"][:8]:
                data_text += f"  • [{i['repo_name']}] #{item['id']}: {item['title']} (score {item['score']})\n"

    # Build the core interests from profile
    user_interests = "\n".join(f"- {i}" for i in PROFILE.get("interests", []))
    career = career_context_block()

    high_kw = PROFILE.get("interest_keywords", {}).get("high", [])

    system = f"""You are a personal research filter for an embedded Linux engineer.
Your job is to decide if ANYTHING in the recent data is worth a short Signal notification.

The developer does NOT want to be flooded with messages. Only ping if something is:
1. A SPECIFIC MATCH to a watchlist item (sensor mainlined, driver merged, regulation changed)
2. Something UNUSUALLY relevant to their core expertise
3. A time-sensitive opportunity (CFP deadline, patch needing review, breaking change)

If nothing is truly noteworthy → respond with exactly: NO_SIGNAL

Developer's interests:
{user_interests}

{career}

High-priority keywords: {', '.join(high_kw[:15])}"""

    prompt = f"""Review this data and decide: should the developer get a Signal notification?

=== ACTIVE WATCHLIST ({len(active_items)} items) ===
{watchlist_text if watchlist_text else "(empty watchlist)"}

{data_text}

RESPOND IN THIS EXACT FORMAT:

DECISION: SIGNAL or NO_SIGNAL

If SIGNAL, then also provide:
MESSAGE: <a 1-3 sentence notification, casual tone, mention the specific thing and which report page to check — max 280 chars>

Then ALWAYS provide (even if NO_SIGNAL):
WATCHLIST_UPDATE:
- RESOLVE: <item number> (if a watchlist item has been fully addressed/resolved)
- ADD: <new topic to watch> | <why it matters>
(you can have 0 or more of each, max 3 ADDs per run, only ADD genuinely new specific things)

Examples of good messages:
"Hey — that Qualcomm camss PIX path driver got merged upstream. Details in the weekly briefing 🔗 192.168.3.151:8888/notes.html"
"Euro NCAP just updated DMS requirements for 2027. Crawl digest has the breakdown 🔗 192.168.3.151:8888/notes.html"
"New libcamera pipeline handler for IMX8MP landed — might be useful for the Snapdragon port. Check issues page"

Examples of NO_SIGNAL situations:
- General kernel activity, nothing specific to watchlist
- Trends that are interesting but not actionable right now
- Things already known / already covered in previous alerts"""

    result = call_ollama(system, prompt, temperature=0.2, max_tokens=800, label="signal-filter")
    if not result:
        print("  LLM call failed")
        return

    # Parse the response
    lines = result.strip().split("\n")
    decision = "NO_SIGNAL"
    message = ""
    resolves = []
    adds = []

    for line in lines:
        line_s = line.strip()
        if line_s.startswith("DECISION:"):
            decision = line_s.split(":", 1)[1].strip().upper()
        elif line_s.startswith("MESSAGE:"):
            message = line_s.split(":", 1)[1].strip()
        elif line_s.startswith("- RESOLVE:"):
            try:
                idx = int(line_s.split(":", 1)[1].strip().split()[0]) - 1
                if 0 <= idx < len(active_items):
                    resolves.append(idx)
            except:
                pass
        elif line_s.startswith("- ADD:"):
            parts = line_s.split(":", 1)[1].strip().split("|", 1)
            topic = parts[0].strip()
            why = parts[1].strip() if len(parts) > 1 else ""
            if topic and len(topic) > 3:
                adds.append({"topic": topic, "why": why})

    # Update watchlist
    updated = False
    for idx in sorted(resolves, reverse=True):
        item = active_items[idx]
        item["status"] = "resolved"
        item["resolved_date"] = datetime.now().strftime("%Y-%m-%d")
        WATCHLIST.setdefault("resolved", []).append(item)
        WATCHLIST["items"].remove(item)
        print(f"  Resolved watchlist item: {item['topic']}")
        updated = True

    for add in adds[:3]:
        new_item = {
            "topic": add["topic"],
            "why": add.get("why", ""),
            "added": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto",
            "status": "active",
        }
        # Don't add duplicates
        existing_topics = [i["topic"].lower() for i in WATCHLIST.get("items", [])]
        if add["topic"].lower() not in existing_topics:
            WATCHLIST["items"].append(new_item)
            print(f"  Added watchlist item: {add['topic']}")
            updated = True

    # Keep resolved list trimmed
    if "resolved" in WATCHLIST:
        WATCHLIST["resolved"] = WATCHLIST["resolved"][-20:]

    if updated and WATCHLIST_PATH:
        with open(WATCHLIST_PATH, "w") as f:
            json.dump(WATCHLIST, f, indent=2)
        print(f"  Watchlist updated: {WATCHLIST_PATH}")

    # Send Signal if warranted
    if "SIGNAL" in decision and "NO" not in decision and message:
        # Truncate and clean up
        if len(message) > 400:
            message = message[:397] + "..."
        sent = signal_send(message)
        print(f"  Signal sent: {sent} — {message[:80]}...")
        save_note("signal", f"Signal Alert — {datetime.now().strftime('%d %b %Y')}", 
                  f"Decision: SIGNAL\nMessage: {message}\n\nFull LLM response:\n{result}",
                  {"decision": "signal", "watchlist_items": len(active_items),
                   "resolved": len(resolves), "added": len(adds)})
    else:
        print(f"  Decision: NO_SIGNAL — nothing noteworthy today")
        # Still save a short note for the record
        save_note("signal", f"Signal Filter — {datetime.now().strftime('%d %b %Y')} (silent)",
                  f"Decision: NO_SIGNAL\n\nFull LLM response:\n{result}",
                  {"decision": "no_signal", "watchlist_items": len(active_items),
                   "resolved": len(resolves), "added": len(adds)})

    return {"type": "signal", "title": f"Signal Filter — {decision}",
            "content": result, "generated": datetime.now().isoformat(timespec="seconds")}


# ─── Task selection ───

TASKS = {
    "weekly": task_weekly,
    "trends": task_trends,
    "crossfeed": task_crossfeed,
    "research": task_research,
    "career": task_career,
    "crawl": task_crawl,
    "learn": task_learn,
    "signal": task_signal,
}

if TASK_ARG and TASK_ARG in TASKS:
    task_name = TASK_ARG
else:
    # Auto-rotate: career-aware schedule
    # Mon: weekly briefing (covers everything)
    # Tue: crossfeed (connections across sources)
    # Wed: career (career intelligence from recent data)
    # Thu: crawl (fetch web sources for news)
    # Fri: trends (pattern analysis)
    # Sat: research (deep dive on a topic)
    # Sun: learn (self-assessment and improvement)
    # signal runs separately via its own cron entry (19:00 daily), not in rotation
    dow = datetime.now().weekday()  # 0=Mon, 6=Sun
    schedule = {
        0: "weekly",
        1: "crossfeed",
        2: "career",
        3: "crawl",
        4: "trends",
        5: "research",
        6: "learn",
    }
    task_name = schedule.get(dow, "research")

    # Check if we already ran this task today
    today = datetime.now().strftime("%Y%m%d")
    existing = glob.glob(os.path.join(THINK_DIR, f"note-{task_name}-{today}*.json"))
    if existing:
        # Fall back to research (always interesting)
        if task_name != "research":
            task_name = "research"
            existing2 = glob.glob(os.path.join(THINK_DIR, f"note-research-{today}*.json"))
            if existing2:
                print(f"  Already ran both {task_name} and research today — skipping")
                sys.exit(0)
        else:
            print(f"  Already ran {task_name} today — skipping")
            sys.exit(0)

print(f"  Task: {task_name}")
result = TASKS[task_name]()

if result:
    print(f"\n[DONE] Generated {result['type']} note: {result['title']}")
    print(f"  Content: {len(result['content'])} chars")
else:
    print(f"\n[DONE] No output generated for {task_name}")

# Clean up old notes (keep last 30 days)
cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
for f in glob.glob(os.path.join(THINK_DIR, "note-*.json")):
    fname = os.path.basename(f)
    # Extract date from filename: note-TYPE-YYYYMMDD-HHMM.json
    parts = fname.replace("note-", "").replace(".json", "").split("-")
    if len(parts) >= 2:
        date_part = parts[1] if len(parts[1]) == 8 else (parts[2] if len(parts) > 2 and len(parts[2]) == 8 else "")
        if date_part and date_part < cutoff:
            os.remove(f)
            print(f"  Cleaned: {fname}")

PYEOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle-think done"
