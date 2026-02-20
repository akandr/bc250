#!/bin/bash
# idle-think.sh — Background LLM thinking during idle time
# Generates research notes, weekly summaries, trend analysis, and cross-feed insights
# by reviewing recent digests and repo issues.
#
# Usage:
#   idle-think.sh                  (pick next task from rotation)
#   idle-think.sh --task weekly    (force specific task)
#   idle-think.sh --task trends
#   idle-think.sh --task crossfeed
#   idle-think.sh --task research
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
DIGEST_FEEDS="${SCRIPT_DIR}/digest-feeds.json"
REPO_FEEDS="${SCRIPT_DIR}/repo-feeds.json"

mkdir -p "$THINK_DIR"

TASK=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --task) TASK="$2"; shift 2 ;;
        *)      echo "Usage: $0 [--task weekly|trends|crossfeed|research]"; exit 1 ;;
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

python3 - "$TASK" "$THINK_DIR" "$DATA_DIR" "$PROFILE_JSON" "$DIGEST_FEEDS" "$REPO_FEEDS" << 'PYEOF'
import sys, os, json, glob, time, hashlib
import urllib.request
from datetime import datetime, timedelta, timezone

TASK_ARG = sys.argv[1]
THINK_DIR = sys.argv[2]
DATA_DIR = sys.argv[3]
PROFILE_JSON = sys.argv[4]
DIGEST_FEEDS_PATH = sys.argv[5]
REPO_FEEDS_PATH = sys.argv[6]

# ─── Load configs ───

PROFILE = {}
if os.path.exists(PROFILE_JSON):
    with open(PROFILE_JSON) as f:
        PROFILE = json.load(f)

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

    system = f"""You are a research assistant for an embedded Linux / multimedia developer.
You produce a weekly research intelligence briefing. Be technical, concise, and insightful.
Focus on actionable insights and emerging trends relevant to the developer's work.

Developer's interests:
{user_interests}"""

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
        # Short Signal ping
        signal_send(f"📋 Weekly briefing ready\n🔗 {DASHBOARD_URL}/notes.html")
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

    system = f"""You are a technical trend analyst for Linux kernel and multimedia development.
Identify patterns, emerging work areas, and notable shifts in development activity.

Developer's interests:
{user_interests}"""

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

    system = f"""You are a technical research assistant specializing in Linux kernel and multimedia.
Write a focused research note that helps a developer understand a topic in depth.

Developer's context:
{user_interests}

Hardware:
{hardware}"""

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


# ─── Task selection ───

TASKS = {
    "weekly": task_weekly,
    "trends": task_trends,
    "crossfeed": task_crossfeed,
    "research": task_research,
}

if TASK_ARG and TASK_ARG in TASKS:
    task_name = TASK_ARG
else:
    # Auto-rotate: pick task based on day of week and existing notes
    dow = datetime.now().weekday()  # 0=Mon, 6=Sun
    if dow == 0:  # Monday
        task_name = "weekly"
    elif dow in (2, 4):  # Wed, Fri
        task_name = "trends"
    elif dow in (1, 5):  # Tue, Sat
        task_name = "crossfeed"
    else:  # Thu, Sun
        task_name = "research"

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
