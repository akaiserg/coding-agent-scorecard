"""How issues and PRs are classified: bugs, fixes, bots, team members, automated closes; plus usage signals.

Shared by analyze.py and report.py so both read the data the same way.
"""
import json
import os
import re
from collections import Counter, defaultdict

from common import RAW, load_items, parse_changelog, ts
from config import AUTO_CLOSE_LABELS, MAINTAINER_ASSOCIATIONS, REPOS, SYNC_ACCOUNTS

FIX_TITLE = re.compile(r"^\s*(\[[^\]]*\]\s*)?(fix|fixes|fixed|bugfix|hotfix)\b", re.I)
BUG_TITLE = re.compile(r"\[\s*bug\s*\]|^bug\b|^bug:", re.I)
# For repos without type labels: bug wording in the title, unless it is tagged as a feature or docs request.
# Against the bug label of the labelled repos this is right most of the time (precision 0.68-0.88).
FEATURE_TITLE = re.compile(r"\[\s*(feature|feat|feature request|enhancement|docs?)\s*\]|^\s*(feat|feature|request|proposal|docs?)\b", re.I)
BUG_WORDS = re.compile(
    r"\[\s*bug|^\s*bug\b|\b(bug|error|errors|crash\w*|fail\w*|broken|break[s]?|not work\w*|doesn'?t work|does not work"
    r"|isn'?t working|not respect\w*|regression|hang\w*|freez\w*|stuck|exception|panic\w*|cannot|can'?t|unable|wrong"
    r"|incorrect\w*|missing|issue with|no longer|stops?|stopped|infinite|leak\w*|flicker\w*|corrupt\w*|lost|loses"
    r"|ignor\w*|invalid|timeout|timed out|unexpected\w*|500|429|40[0134])\b",
    re.I,
)


def labels_of(n):
    return [l["name"] for l in (n.get("labels") or {}).get("nodes", [])]


def login_of(n):
    return (n.get("author") or {}).get("login")


def is_bug(n, key):
    title = n["title"] or ""
    if not REPOS[key].get("bug_labels", True):
        return bool(BUG_WORDS.search(title)) and not FEATURE_TITLE.search(title)
    ls = [l.lower() for l in labels_of(n)]
    return "bug" in ls or any(l.startswith("bug /") for l in ls) or bool(BUG_TITLE.search(title))


def is_bot(n):
    a = n.get("author") or {}
    login = a.get("login") or ""
    return a.get("__typename") == "Bot" or login.endswith("[bot]") or login in SYNC_ACCOUNTS


def is_auto_closed(n):
    return bool(AUTO_CLOSE_LABELS & {l.lower() for l in labels_of(n)})


def is_fix_pr(p):
    return bool(FIX_TITLE.search(p["title"] or ""))


def load_team(key, items):
    """Anyone who has merged a PR has write access. Staff often hide their org membership, so
    authorAssociation alone would count them as outside contributors."""
    team = {(p.get("mergedBy") or {}).get("login") for p in items}
    legacy = os.path.join(RAW, f"{key}_mergers.jsonl")  # merged-by list for PRs fetched before mergedBy was a field
    if os.path.exists(legacy):
        team |= {json.loads(l)["merged_by"] for l in open(legacy)}
    team |= {login_of(n) for n in items if n["authorAssociation"] in MAINTAINER_ASSOCIATIONS}
    team.discard(None)
    return team


def is_external(n, team):
    return not is_bot(n) and login_of(n) not in team


def prepare(key, kind, as_of):
    """Items as they stood at `as_of`: later ones dropped, later closes and merges treated as not yet happened.

    Sets _c (created), _x (closed, or None if open), _m (merged), _reason and _bug on each item.
    """
    out = []
    for n in load_items(key, kind):
        c = ts(n["createdAt"])
        if c >= as_of:
            continue
        x = ts(n["closedAt"]) if n["state"] in ("CLOSED", "MERGED") else None
        m = ts(n.get("mergedAt")) if n["state"] == "MERGED" else None
        n["_c"] = c
        n["_x"] = x if x and x < as_of else None
        n["_m"] = m if m and m < as_of else None
        n["_reason"] = (n.get("stateReason") or "COMPLETED") if n["_x"] else None
        n["_bug"] = is_bug(n, key) if kind == "issues" else False
        out.append(n)
    return out


def releases(key, as_of):
    """Published versions as (time, is_prerelease). Changelog versions where GitHub releases are incomplete."""
    if REPOS[key].get("versions_from_changelog"):
        return [(ts(r["published"]), False) for r in parse_changelog(key) if r["published"] and ts(r["published"]) < as_of]
    out = []
    for line in open(os.path.join(RAW, f"{key}_releases.jsonl")):
        r = json.loads(line)
        if r["isDraft"]:
            continue
        d = ts(r["publishedAt"] or r["createdAt"])
        if d < as_of:
            out.append((d, r["isPrerelease"]))
    return out


def fixes(key, prs, as_of):
    """Times at which fixes shipped: CHANGELOG "Fixed" entries, or merged PRs titled as fixes."""
    if REPOS[key].get("fix_source") == "changelog":
        out = []
        for r in parse_changelog(key):
            if r["published"] and ts(r["published"]) < as_of:
                out += [ts(r["published"])] * r["fixed"]
        return out
    return [p["_m"] for p in prs if p["_m"] and is_fix_pr(p)]


def changelog_by_month(key, as_of):
    out = defaultdict(Counter)
    for r in parse_changelog(key):
        if not r["published"] or ts(r["published"]) >= as_of:
            continue
        k = r["published"][:7]
        out[k]["versions"] += 1
        for f in ("fixed", "added", "improved", "changed", "other"):
            out[k][f] += r[f]
    return {k: dict(v) for k, v in sorted(out.items())}


def npm_daily(key, as_of):
    """Daily npm downloads before `as_of`, as {date: count}. npm reports some days as zero during its
    stats outages and the latest days with a lag; those days are left out rather than counted as zero."""
    path = os.path.join(RAW, f"{key}_npm_downloads.json")
    if not REPOS[key].get("downloads_package") or not os.path.exists(path):
        return {}
    cutoff = as_of.date()
    return {d: n for d, n in ((ts(day + "T00:00:00Z").date(), n) for day, n in json.load(open(path)).items())
            if n > 0 and d < cutoff}


def npm_typical(daily, start, end):
    """Median daily downloads over the reported days in [start, end), or None if npm reported none.

    The median, because automated download storms can multiply a package's count for days at a time
    (Codex went from ~1M to 46M a day in early May 2026)."""
    vals = sorted(n for d, n in daily.items() if start.date() <= d < end.date())
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def star_snapshots(key):
    """(time, stars) for every run that recorded the repo's star count, oldest first."""
    path = os.path.join(RAW, "stars_history.jsonl")
    if not os.path.exists(path):
        return []
    rows = (json.loads(l) for l in open(path))
    return sorted((ts(r["at"]), r["stars"]) for r in rows if r["repo"] == key)


def stars_at(key, t, tolerance_days=3):
    """Stars recorded within `tolerance_days` of `t` (nearest snapshot), or None when no run was close enough."""
    near = [(abs((at - t).total_seconds()), n) for at, n in star_snapshots(key)]
    near = [x for x in near if x[0] <= tolerance_days * 86400]
    return min(near)[1] if near else None


def pr_policy(key):
    """The repo's pull request creation policy (e.g. "all", "collaborators_only"), or None if not fetched."""
    for line in open(os.path.join(RAW, "repo_info.jsonl")):
        r = json.loads(line)
        if r["nameWithOwner"] == REPOS[key]["repo"]:
            return r.get("prCreationPolicy")
    return None


def stars_now(key):
    """The latest recorded star count, which is what per-star ratios are measured against."""
    snaps = star_snapshots(key)
    if snaps:
        return snaps[-1][1]
    for line in open(os.path.join(RAW, "repo_info.jsonl")):
        r = json.loads(line)
        if r["nameWithOwner"] == REPOS[key]["repo"]:
            return r["stargazerCount"]
    return None
