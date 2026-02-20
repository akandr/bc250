#!/bin/bash
# repo-watch.sh — Monitor GitHub/GitLab repos for new issues and MRs
# Scores them against user profile interests, alerts via Signal for high-interest items.
#
# Usage:
#   repo-watch.sh --repo gstreamer        (check one repo)
#   repo-watch.sh --repo libcamera
#   repo-watch.sh --all                   (check all repos)
#
# Config: repo-feeds.json (repo definitions), profile.json (interest scoring)
# Location on bc250: /opt/netscan/repo-watch.sh
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
REPO_FEEDS="${SCRIPT_DIR}/repo-feeds.json"
PROFILE_JSON="${SCRIPT_DIR}/profile.json"
DATA_DIR="/opt/netscan/data"

usage() {
    echo "Usage: $0 --repo <repo-id> | --all"
    echo "Available repos (from repo-feeds.json):"
    python3 -c "import json; [print(f'  {k}: {v[\"name\"]} ({v[\"type\"]})') for k,v in json.load(open('$REPO_FEEDS')).items()]" 2>/dev/null
    exit 1
}

# Parse arguments
REPO_ID=""
RUN_ALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO_ID="$2"; shift 2 ;;
        --all)  RUN_ALL=true; shift ;;
        *)      usage ;;
    esac
done

if [[ "$RUN_ALL" == "true" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] repo-watch: checking ALL repos"
    REPO_IDS=$(python3 -c "import json; d=json.load(open('$REPO_FEEDS')); print(' '.join(k for k,v in d.items() if isinstance(v,dict)))")
    for rid in $REPO_IDS; do
        echo ""
        echo "═══════════════════════════════════════════"
        "$0" --repo "$rid"
        echo ""
        sleep 2
    done
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] repo-watch: all repos done"
    # After checking all repos, regenerate dashboard
    if [[ -f /opt/netscan/generate-html.py ]]; then
        python3 /opt/netscan/generate-html.py 2>/dev/null || true
        echo "  Dashboard regenerated"
    fi
    exit 0
fi

if [[ -z "$REPO_ID" ]]; then
    usage
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] repo-watch starting: repo=$REPO_ID"

python3 - "$REPO_ID" "$REPO_FEEDS" "$PROFILE_JSON" "$DATA_DIR" << 'PYEOF'
import sys, os, json, time, hashlib, re
import urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

REPO_ID = sys.argv[1]
REPO_FEEDS = sys.argv[2]
PROFILE_JSON = sys.argv[3]
DATA_DIR = sys.argv[4]

# ─── Load configs ───

with open(REPO_FEEDS) as f:
    all_repos = json.load(f)

if REPO_ID not in all_repos:
    print(f"FATAL: unknown repo '{REPO_ID}'. Available: {', '.join(all_repos.keys())}")
    sys.exit(1)

REPO = all_repos[REPO_ID]
REPO_NAME = REPO["name"]
REPO_EMOJI = REPO["emoji"]
REPO_TYPE = REPO["type"]

# Load profile for interest keywords
PROFILE = {}
if os.path.exists(PROFILE_JSON):
    with open(PROFILE_JSON) as f:
        PROFILE = json.load(f)

DASHBOARD_URL = PROFILE.get("dashboard_url", "http://192.168.3.151:8888")
SIGNAL_CFG = PROFILE.get("signal", {})
SIGNAL_RPC = SIGNAL_CFG.get("rpc", "http://127.0.0.1:8080/api/v1/rpc")
SIGNAL_FROM = SIGNAL_CFG.get("from", "+48532825716")
SIGNAL_TO = SIGNAL_CFG.get("to", "+48503326388")
MAX_ALERT_CHARS = SIGNAL_CFG.get("max_alert_chars", 500)

# Repo data directory
REPO_DIR = os.path.join(DATA_DIR, REPO["data_dir"])
os.makedirs(REPO_DIR, exist_ok=True)

# Relevance keywords from repo config + profile
RELEVANCE = REPO.get("relevance", {})
MIN_SCORE = REPO.get("min_score", 3)

# Profile-based keywords augment repo-specific ones
for kw in PROFILE.get("interest_keywords", {}).get("high", []):
    if kw not in RELEVANCE:
        RELEVANCE[kw] = 2
for kw in PROFILE.get("interest_keywords", {}).get("medium", []):
    if kw not in RELEVANCE:
        RELEVANCE[kw] = 1

USER_AGENT = f"netscan-bc250-repowatch/1.0 ({REPO_ID})"

# State file: tracks what we've already seen
STATE_FILE = os.path.join(REPO_DIR, "state.json")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_ids": [], "last_check": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

print(f"  Repo: {REPO_NAME} ({REPO_TYPE})")
print(f"  Data: {REPO_DIR}")

# ─── API helpers ───

def fetch_json(url, headers=None):
    """Fetch JSON from URL with retry."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except urllib.error.HTTPError as ex:
            if ex.code == 429:
                retry_after = int(ex.headers.get("Retry-After", 60))
                print(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            print(f"  HTTP {ex.code} fetching {url}: {ex.reason}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
        except Exception as ex:
            print(f"  Fetch attempt {attempt+1} failed: {ex}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    return None

def relevance_score(text):
    """Score text by relevance keywords."""
    low = text.lower()
    score = 0
    matched = []
    for kw, w in RELEVANCE.items():
        if kw.lower() in low:
            score += w
            if w > 0:
                matched.append(kw)
    return score, matched

def _signal_send(msg):
    """Send a single Signal message."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": SIGNAL_FROM,
                "recipient": [SIGNAL_TO],
                "message": msg
            },
            "id": f"repowatch-{REPO_ID}"
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as ex:
        print(f"  Signal send failed: {ex}")
        return False


# ─── GitHub fetcher ───

def fetch_github_items(since_dt):
    """Fetch new/updated issues and PRs from GitHub."""
    owner = REPO["owner"]
    repo = REPO["repo"]
    items = []

    for track_type in REPO.get("track", ["issues"]):
        endpoint = "issues" if track_type == "issues" else "pulls"
        # GitHub issues endpoint includes PRs unless filtered
        url = f"https://api.github.com/repos/{owner}/{repo}/{endpoint}"
        params = f"?state=open&sort=updated&direction=desc&per_page=50"
        if since_dt:
            params += f"&since={since_dt}"

        data = fetch_json(url + params)
        if not data:
            print(f"    Failed to fetch {track_type}")
            continue

        for item in data:
            # GitHub issues endpoint returns PRs too — filter
            is_pr = "pull_request" in item
            if endpoint == "issues" and is_pr:
                continue  # skip PRs in issues list, we get them separately

            items.append({
                "id": item["number"],
                "type": "pull_request" if is_pr else "issue",
                "title": item.get("title", ""),
                "body": (item.get("body") or "")[:2000],
                "state": item.get("state", "open"),
                "author": item.get("user", {}).get("login", "?"),
                "created": item.get("created_at", ""),
                "updated": item.get("updated_at", ""),
                "url": item.get("html_url", ""),
                "labels": [l.get("name", "") for l in item.get("labels", [])],
                "comments": item.get("comments", 0),
                "reactions": item.get("reactions", {}).get("total_count", 0),
            })

        print(f"    GitHub {endpoint}: {len([i for i in items if (i['type'] == 'pull_request') == (endpoint == 'pulls')])} items")

    return items


# ─── GitLab fetcher ───

def fetch_gitlab_items(since_dt):
    """Fetch new/updated issues and MRs from GitLab."""
    api_base = REPO["api_base"]
    project = REPO["project_path"].replace("/", "%2F")
    items = []

    for track_type in REPO.get("track", ["issues"]):
        if track_type == "merge_requests":
            endpoint = "merge_requests"
            item_type = "merge_request"
        else:
            endpoint = "issues"
            item_type = "issue"

        url = f"{api_base}/projects/{project}/{endpoint}"
        params = f"?state=opened&order_by=updated_at&sort=desc&per_page=50"
        if since_dt:
            params += f"&updated_after={since_dt}"

        data = fetch_json(url + params)
        if not data:
            print(f"    Failed to fetch {track_type}")
            continue

        for item in data:
            items.append({
                "id": item.get("iid", item.get("id", 0)),
                "type": item_type,
                "title": item.get("title", ""),
                "body": (item.get("description") or "")[:2000],
                "state": item.get("state", "opened"),
                "author": item.get("author", {}).get("username", "?"),
                "created": item.get("created_at", ""),
                "updated": item.get("updated_at", ""),
                "url": item.get("web_url", ""),
                "labels": item.get("labels", []),
                "comments": item.get("user_notes_count", 0),
                "reactions": item.get("upvotes", 0) + item.get("downvotes", 0),
            })

        print(f"    GitLab {endpoint}: {len([i for i in items if i['type'] == item_type])} items")

    return items


# ─── Patchwork fetcher ───

def fetch_patchwork_items(since_dt):
    """Fetch new patches (and optionally series) from a Patchwork instance."""
    api_base = REPO["api_base"]
    project_id = REPO.get("project_id", 1)
    items = []

    # Patchwork: skip 'since' — bc250 clock may differ from server;
    # rely on seen_ids for dedup instead.

    for track_type in REPO.get("track", ["patches"]):
        if track_type == "series":
            url = f"{api_base}/series/?project={project_id}&order=-date&per_page=50"

            data = fetch_json(url)
            if not data:
                print(f"    Failed to fetch series")
                continue

            for s in data:
                cover = s.get("cover_letter") or {}
                items.append({
                    "id": s["id"],
                    "type": "series",
                    "title": s.get("name", ""),
                    "body": (cover.get("name") or "")[:2000],
                    "state": "complete" if s.get("received_all") else "incomplete",
                    "author": s.get("submitter", {}).get("name", "?"),
                    "created": s.get("date", ""),
                    "updated": s.get("date", ""),
                    "url": s.get("web_url", ""),
                    "labels": [f"v{s.get('version', 1)}", f"{s.get('total', 0)} patches"],
                    "comments": 0,
                    "reactions": 0,
                })

            print(f"    Patchwork series: {len([i for i in items if i['type'] == 'series'])} items")

        else:  # patches
            url = f"{api_base}/patches/?project={project_id}&order=-date&per_page=50"

            data = fetch_json(url)
            if not data:
                print(f"    Failed to fetch patches")
                continue

            for p in data:

                state = p.get("state", "new")
                check = p.get("check", "pending")
                labels = [state]
                if check and check != "pending":
                    labels.append(f"check:{check}")

                series_list = p.get("series", [])
                if series_list:
                    labels.append(f"{len(series_list)} in series")

                items.append({
                    "id": p["id"],
                    "type": "patch",
                    "title": p.get("name", ""),
                    "body": "",
                    "state": state,
                    "author": p.get("submitter", {}).get("name", "?"),
                    "created": p.get("date", ""),
                    "updated": p.get("date", ""),
                    "url": p.get("web_url", ""),
                    "labels": labels,
                    "comments": 0,
                    "reactions": 0,
                })

            print(f"    Patchwork patches: {len([i for i in items if i['type'] == 'patch'])} items")

    return items


# ─── Main logic ───

state = load_state()
seen_ids = set(state.get("seen_ids", []))
last_check = state.get("last_check")

# Default: look back 48h on first run, then since last check
if last_check:
    since_dt = last_check
    print(f"  Since: {last_check}")
else:
    since_dt = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  First run — looking back 48h")

# Fetch items
if REPO_TYPE == "github":
    items = fetch_github_items(since_dt)
elif REPO_TYPE == "gitlab":
    items = fetch_gitlab_items(since_dt)
elif REPO_TYPE == "patchwork":
    items = fetch_patchwork_items(since_dt)
else:
    print(f"  Unknown repo type: {REPO_TYPE}")
    sys.exit(1)

if not items:
    print(f"  No new items found")
    state["last_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)
    sys.exit(0)

# Score and filter
new_interesting = []
new_other = []

for item in items:
    item_key = f"{REPO_ID}:{item['type']}:{item['id']}"

    # Score against relevance keywords
    full_text = f"{item['title']} {item['body']} {' '.join(item['labels'])}"
    score, keywords = relevance_score(full_text)

    # Boost for reactions/activity
    if item["reactions"] >= 5:
        score += 1
    if item["comments"] >= 10:
        score += 1

    item["score"] = round(score, 1)
    item["keywords"] = keywords
    item["is_new"] = item_key not in seen_ids

    if score >= MIN_SCORE:
        new_interesting.append(item)
    else:
        new_other.append(item)

    seen_ids.add(item_key)

# Sort by score descending
new_interesting.sort(key=lambda x: -x["score"])
new_other.sort(key=lambda x: -x["score"])

truly_new = [i for i in new_interesting if i["is_new"]]

print(f"  Total: {len(items)} items, {len(new_interesting)} interesting (≥{MIN_SCORE}), {len(truly_new)} NEW")

# Save results
dt_file = datetime.now().strftime("%Y%m%d")
results = {
    "repo_id": REPO_ID,
    "repo_name": REPO_NAME,
    "repo_type": REPO_TYPE,
    "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "since": since_dt,
    "total_items": len(items),
    "interesting": [
        {
            "id": i["id"], "type": i["type"], "title": i["title"],
            "state": i["state"], "author": i["author"],
            "created": i["created"], "updated": i["updated"],
            "url": i["url"], "labels": i["labels"],
            "score": i["score"], "keywords": i["keywords"],
            "comments": i["comments"], "reactions": i["reactions"],
            "is_new": i["is_new"],
            "body_preview": i["body"][:500],
        }
        for i in new_interesting[:30]
    ],
    "other_count": len(new_other),
}

results_path = os.path.join(REPO_DIR, f"watch-{dt_file}.json")
with open(results_path, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"  Saved: {results_path}")

# Also save/update latest.json for dashboard consumption
latest_path = os.path.join(REPO_DIR, "latest.json")
with open(latest_path, "w") as f:
    json.dump(results, f, indent=2, default=str)

# Update state
state["seen_ids"] = list(seen_ids)[-500:]  # keep last 500 IDs to avoid unlimited growth
state["last_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
save_state(state)

# ─── Signal alert for truly new interesting items ───

if truly_new:
    # Short alert style: summary + dashboard link
    type_counts = {}
    for i in truly_new:
        k = i["type"].replace("_", " ")
        type_counts[k] = type_counts.get(k, 0) + 1

    count_str = ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in type_counts.items())

    lines = [
        f"{REPO_EMOJI} {REPO_NAME}: {count_str} of interest",
        ""
    ]

    # Top 5 items, compact
    for i in truly_new[:5]:
        type_icon = "🔀" if "merge" in i["type"] or "pull" in i["type"] else ("📩" if "patch" in i["type"] else "🐛")
        lines.append(f"{type_icon} #{i['id']}: {i['title'][:80]}")
        if i["keywords"]:
            lines.append(f"   [{', '.join(i['keywords'][:4])}]")

    if len(truly_new) > 5:
        lines.append(f"   ...+{len(truly_new) - 5} more")

    lines.append(f"\n🔗 {DASHBOARD_URL}/issues.html")

    alert = "\n".join(lines)

    # Respect max_alert_chars
    if len(alert) > MAX_ALERT_CHARS:
        alert = alert[:MAX_ALERT_CHARS - 20].rsplit("\n", 1)[0] + f"\n\n🔗 {DASHBOARD_URL}/issues.html"

    if _signal_send(alert):
        print(f"  ✅ Signal alert sent ({len(alert)} chars, {len(truly_new)} new items)")
    else:
        print(f"  ⚠ Signal send failed")
else:
    print(f"  No new interesting items — no alert needed")

print(f"  Done: {REPO_NAME}")

PYEOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] repo-watch done: repo=$REPO_ID"
