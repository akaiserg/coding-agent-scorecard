"""Shared helpers: GitHub API access, loading fetched items, sync state, CHANGELOG parsing."""
import fcntl
import glob
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

from config import REPOS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
UPDATES = os.path.join(RAW, "updates")
STATE_FILE = os.path.join(RAW, "sync_state.json")

_token = None


def token():
    global _token
    if _token is None:
        _token = os.environ.get("GH_TOKEN") or subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    return _token


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _request(req, label):
    """Send a request, retrying on rate limits (403/429), transient server errors and cut-off responses."""
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as e:
            wait = min(300, 15 * 2 ** attempt)
            if isinstance(e, urllib.error.HTTPError):
                if e.code not in (403, 429, 500, 502, 503, 504):
                    raise
                if e.headers.get("retry-after"):
                    wait = int(e.headers["retry-after"]) + 5
            log(f"{label}: {e}; retry in {wait}s")
            time.sleep(wait)
    sys.exit(f"{label}: giving up after repeated errors")


def graphql(query, variables, label="graphql"):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token()}", "Content-Type": "application/json"},
    )
    for attempt in range(5):
        data = _request(req, label)
        if not data.get("errors") or (data.get("data") or {}).get("repository"):
            return data
        log(f"{label}: {json.dumps(data['errors'])[:300]}; retry")
        time.sleep(15 * 2 ** attempt)
    sys.exit(f"{label}: GraphQL errors persisted")


def rest(path, params=None, accept="application/vnd.github+json", raw=False):
    url = f"https://api.github.com/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"bearer {token()}", "Accept": accept})
    if raw:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode()
    return _request(req, path)


def wait_for_rate_limit(rl, label):
    if rl and rl["remaining"] < 50:
        wait = max(10, (ts(rl["resetAt"]) - now()).total_seconds() + 10)
        log(f"{label}: rate limit low, sleeping {wait:.0f}s")
        time.sleep(wait)


# ---------- fetched items ----------

def base_files(key, kind):
    return sorted(glob.glob(os.path.join(RAW, f"{key}_{kind}_asc.jsonl")) + glob.glob(os.path.join(RAW, f"{key}_{kind}_desc.jsonl")))


def update_files(key, kind):
    # Stamped YYYYmmddTHHMMSSZ, so name order is time order.
    return sorted(glob.glob(os.path.join(UPDATES, f"{key}_{kind}_*.jsonl")))


def load_items(key, kind):
    """All issues or PRs of a repo: the full fetch, then each incremental update over it (latest wins)."""
    items = {}
    for path in base_files(key, kind) + update_files(key, kind):
        with open(path) as f:
            for line in f:
                n = json.loads(line)
                if n:  # the API returns null for items deleted or hidden mid-listing
                    items[n["number"]] = n
    return list(items.values())


# ---------- sync state ----------

@contextmanager
def state_lock():
    os.makedirs(RAW, exist_ok=True)
    with open(STATE_FILE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_state():
    if not os.path.exists(STATE_FILE):
        return {}
    return json.load(open(STATE_FILE))


def record_sync(key, kind, when):
    with state_lock():
        state = read_state()
        state.setdefault(key, {})[kind] = iso(when)
        json.dump(state, open(STATE_FILE, "w"), indent=1, sort_keys=True)


def data_as_of():
    """The moment every repo's issues and PRs are known to be current: the oldest last sync."""
    state = read_state()
    stamps = [ts(state.get(k, {}).get(kind)) for k in REPOS for kind in ("issues", "prs")]
    if not stamps or None in stamps:
        return None
    return min(stamps)


# ---------- CHANGELOG ----------

def parse_changelog(key):
    """Per-version bullet counts from a repo's CHANGELOG.md, dated by npm publish time."""
    md = os.path.join(RAW, f"{key}_CHANGELOG.md")
    npm = os.path.join(RAW, f"{key}_npm_time.json")
    if not (os.path.exists(md) and os.path.exists(npm)):
        return []
    published = json.load(open(npm))
    rows, version, counts = [], None, None
    for line in open(md).read().splitlines():
        m = re.match(r"^##\s+(\S+)", line)
        if m:
            if version:
                rows.append((version, counts))
            version = m.group(1)
            counts = {"fixed": 0, "added": 0, "improved": 0, "changed": 0, "other": 0}
            continue
        if version and re.match(r"^\s*[-*]\s+", line):
            word = re.sub(r"^\s*[-*]\s+", "", line).split(" ", 1)[0].lower().strip("*:`")
            if word.startswith("fix"):
                counts["fixed"] += 1
            elif word.startswith(("add", "new", "introduc")):
                counts["added"] += 1
            elif word.startswith(("improv", "faster", "reduc", "optimi")):
                counts["improved"] += 1
            elif word.startswith(("chang", "updat", "renam", "remov", "deprecat")):
                counts["changed"] += 1
            else:
                counts["other"] += 1
    if version:
        rows.append((version, counts))
    out, last = [], None
    for v, c in reversed(rows):  # oldest first, so an undated version inherits the previous date
        d = published.get(v) or last
        last = d
        out.append({"version": v, "published": d, **c})
    return list(reversed(out))
