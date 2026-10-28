#!/usr/bin/env python3
"""Pull requests that carry each agent's signature across public GitHub: how many there are, how often and how
fast they merge, how big they are, and how quickly they fix the issues they link.

  agents.py [--days N] [--settle N] [--sample N] [--no-fetch | --reuse-days N] [--out PATH]

The cohort is PRs created in the N days (default 30) that end at midnight UTC --settle days (default 7) ago, so
every PR has had at least that long to be reviewed. How many PRs carry each signature comes from GitHub search. Outcomes,
times and sizes come from PRs read one by one: every PR when a signature has at most FULL_BELOW in the cohort,
otherwise the first PRs of randomly placed short time slices, each weighted by how many PRs its slice holds, so
the sample stands for the whole cohort.

Writes data/raw/agents/<date>.json (reused by --no-fetch) and reports/harness-agents_<date>.html. Each signature
is saved as soon as it is read, so an interrupted run picks up where it stopped when run again the same day.
"""
import argparse
import glob
import json
import math
import os
import random
import re
import sys
import time
from datetime import timedelta

from classify import FIX_TITLE
from common import RAW, ROOT, graphql, iso, log, now, rest, ts
from config import REPOS
from overview import PR_SIZES, bucket, chart_buckets, fmt_hours
from report import CSS, esc, fdate, fnum, fpct, name, short

OUT_DIR = os.path.join(RAW, "agents")
FULL_BELOW = 4000  # read every PR of a signature with at most this many in the cohort
PER_SLICE = 25  # PRs read from each random slice
LINES_CAP = 5000  # added lines counted per PR in totals, so lockfiles and vendored code don't swamp them
MIN_LINKED = 30  # PRs needed before an issue-to-fix median is shown
OTHER = "other"
AGENTS = ["claude-code", "codex", "opencode"]
GRAY = "#8a8f98"
VENDORS = "-org:anthropics -org:openai -org:anomalyco -org:sst"
CLAUDE_LINE = re.compile(r"generated with\W+claude code", re.I)
OPENCODE_LINE = re.compile(r"generated with\W+opencode", re.I)
TTM = [("Under 10 min", 0, 1 / 6), ("10–60 min", 1 / 6, 1), ("1–24 hours", 1, 24), ("1–7 days", 24, 168), ("Over a week", 168, None)]

# Each agent's PRs are split into strata that do not overlap. A quoted phrase in search matches its words anywhere
# in the PR, so every PR read is also checked for the signature itself; the first stratum also keeps PRs on the
# agent's branch, which the second stratum's NOT would otherwise drop. The vendors' own organizations are left out.
STRATA = {
    "claude-code": [
        ("“Generated with Claude Code” in the description", f'"Generated with Claude Code" {VENDORS}',
         lambda p: CLAUDE_LINE.search(p["body"] or "") or p["headRefName"].startswith("claude/")),
        ("claude/ branch without that line", f'head:claude/ NOT "Generated with Claude Code" {VENDORS}',
         lambda p: p["headRefName"].startswith("claude/")),
    ],
    "codex": [
        ("codex/ branch (Codex cloud)", f"head:codex/ {VENDORS}", lambda p: p["headRefName"].startswith("codex/")),
    ],
    "opencode": [
        ("“Generated with opencode” in the description", f'"Generated with opencode" {VENDORS}',
         lambda p: OPENCODE_LINE.search(p["body"] or "") or p["headRefName"].startswith("opencode/")),
        ("opencode/ branch without that line", f'head:opencode/ NOT "Generated with opencode" {VENDORS}',
         lambda p: p["headRefName"].startswith("opencode/")),
    ],
    OTHER: [
        ("none of these signatures; Copilot and Cursor branches and dependency bots left out",
         'NOT "Generated with Claude Code" NOT "Generated with opencode" -head:codex/ -head:claude/ -head:opencode/ '
         "-head:copilot/ -head:cursor/ -author:app/dependabot -author:app/renovate -author:app/github-actions",
         lambda p: True),
    ],
}

SEARCH = """query($q: String!, $n: Int!, $after: String) {
  search(query: $q, type: ISSUE, first: $n, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { id } }
  }
}"""

DETAILS = """query($ids: [ID!]!) {
  nodes(ids: $ids) { ... on PullRequest {
    url createdAt mergedAt closedAt isDraft additions deletions changedFiles title headRefName body
    author { login } mergedBy { login } repository { stargazerCount }
    closingIssuesReferences(first: 3) { nodes { createdAt } }
  } }
}"""


def label(k):
    return name(k) if k in REPOS else "Unsigned PRs"


def color(k):
    return REPOS[k]["color"] if k in REPOS else GRAY


# ---------- fetching ----------

def search_page(q, n, after=None):
    """One page of search results, with each PR's details fetched in a second request: asking search for the
    descriptions too makes GitHub time out far more often."""
    time.sleep(1)  # search has its own secondary rate limits
    page = graphql(SEARCH, {"q": q, "n": n, "after": after}, "agent search")["data"]["search"]
    ids = [p["id"] for p in page["nodes"] if p]
    page["nodes"] = []
    for i in range(0, len(ids), 50):
        page["nodes"] += graphql(DETAILS, {"ids": ids[i:i + 50]}, "agent PRs")["data"]["nodes"]
    return page


def count(q):
    time.sleep(2.2)  # the REST search API allows 30 requests a minute
    return rest("search/issues", {"q": q, "per_page": 1})["total_count"]


def slim(p, check, weight):
    """What the report needs from a PR; the description is read only to check the signature."""
    return {"url": p["url"], "created": p["createdAt"], "merged": p["mergedAt"], "closed": p["closedAt"],
            "draft": p["isDraft"], "add": p["additions"], "del": p["deletions"], "files": p["changedFiles"],
            "fix": bool(FIX_TITLE.search(p["title"] or "")), "author": (p["author"] or {}).get("login"),
            "merger": (p["mergedBy"] or {}).get("login"), "stars": p["repository"]["stargazerCount"],
            "issues": [i["createdAt"] for i in p["closingIssuesReferences"]["nodes"]],
            "sig": bool(check(p)), "w": weight}


def read_all(q, s, e, check):
    """Every PR created in [s, e]; windows over search's 1,000-result cap are split in two."""
    query = f"{q} is:pr sort:created-asc created:{iso(s)}..{iso(e)}"
    page = search_page(query, 100)
    if page["issueCount"] > 1000 and e - s > timedelta(minutes=2):
        mid = s + (e - s) / 2
        return read_all(q, s, mid, check) + read_all(q, mid + timedelta(seconds=1), e, check)
    out = [slim(n, check, 1.0) for n in page["nodes"] if n]
    while page["pageInfo"]["hasNextPage"]:
        page = search_page(query, 100, page["pageInfo"]["endCursor"])
        out += [slim(n, check, 1.0) for n in page["nodes"] if n]
    return out


def read_sample(q, s, e, check, total, target, rng):
    """The first PRs of randomly placed slices, each weighted by how many PRs its slice holds, scaled so the
    weights add up to the stratum's total."""
    length = max(timedelta(minutes=1), (e - s) * PER_SLICE / total)
    out = []
    for _ in range(math.ceil(target / PER_SLICE)):
        a = s + (e - s - length) * rng.random()
        page = search_page(f"{q} is:pr sort:created-asc created:{iso(a)}..{iso(a + length)}", PER_SLICE)
        nodes = [n for n in page["nodes"] if n]
        out += [slim(n, check, page["issueCount"] / len(nodes)) for n in nodes]
    scale = total / sum(p["w"] for p in out) if out else 0
    for p in out:
        p["w"] *= scale
    return out


def fetch(days, settle, target):
    e = now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=settle)
    s = e - timedelta(days=days)
    window = f"created:{iso(s)}..{iso(e)}"
    parts = os.path.join(OUT_DIR, "partial")
    os.makedirs(parts, exist_ok=True)
    strata, prs = [], []
    for agent, defs in STRATA.items():
        for text, q, check in defs:
            part = os.path.join(parts, f"{e:%Y-%m-%d}_{days}d_{len(strata)}.json")
            saved = json.load(open(part)) if os.path.exists(part) else None
            if saved and saved[0]["query"] == q and saved[0]["target"] == target:
                st, got = saved
                log(f"{label(agent)}, {text}: reusing the {len(got):,} PRs read at {st['read_at']}")
            else:
                total = count(f"{q} is:pr {window}")
                merged = count(f"{q} is:pr is:merged {window}")
                mode = "all" if total <= FULL_BELOW else "sample"
                log(f"{label(agent)}, {text}: {total:,} PRs; reading {'all of them' if mode == 'all' else f'a sample of {target:,}'}")
                rng = random.Random(f"{iso(s)} {q}")
                got = read_all(q, s, e, check) if mode == "all" else read_sample(q, s, e, check, total, target, rng)
                seen = set()
                got = [p for p in got if not (p["url"] in seen or seen.add(p["url"]))]
                for p in got:
                    p["agent"], p["stratum"] = agent, len(strata)
                st = {"agent": agent, "label": text, "query": q, "count": total, "merged_count": merged,
                      "mode": mode, "read": len(got), "target": target, "read_at": iso(now())}
                with open(part, "w") as f:
                    json.dump([st, got], f)
            strata.append(st)
            prs += got
    as_of = now()
    data = {"as_of": iso(as_of), "start": iso(s), "end": iso(e), "days": days, "settle": settle, "strata": strata, "prs": prs}
    path = os.path.join(OUT_DIR, f"{as_of:%Y-%m-%d}.json")
    with open(path, "w") as f:
        json.dump(data, f)
    for f in glob.glob(os.path.join(parts, f"{e:%Y-%m-%d}_{days}d_*.json")):
        os.remove(f)
    log(f"agent PRs -> {os.path.relpath(path, ROOT)}")
    return data


def latest():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*.json")))
    return json.load(open(files[-1])) if files else None


# ---------- statistics (every PR carries a weight: how many PRs of the cohort it stands for) ----------

def hours(p):
    return (ts(p["merged"]) - ts(p["created"])).total_seconds() / 3600


def wsum(rows):
    return sum(p["w"] for p in rows)


def share(rows, cond):
    total = wsum(rows)
    return wsum([p for p in rows if cond(p)]) / total if total else None


def wmedian(pairs):
    """Median of (value, weight) pairs."""
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return None


def wdist(rows, f, buckets):
    out = [0.0] * len(buckets)
    for p in rows:
        out[bucket(f(p), buckets)] += p["w"]
    return [round(v) for v in out]


def stats(rows, settle):
    ok = [p for p in rows if p["sig"]]
    ready = [p for p in ok if p["merged"] or not p["draft"]]  # a PR still in draft never asked for review
    merged = [p for p in ready if p["merged"]]
    fixes = [p for p in merged if p["fix"] or p["issues"]]
    linked = [p for p in merged if p["issues"]]
    big = [p for p in ready if p["stars"] >= 10]
    to_fix = [((ts(p["merged"]) - min(ts(t) for t in p["issues"])).total_seconds() / 86400, p["w"]) for p in linked]
    to_fix = [(d, w) for d, w in to_fix if d >= 0]
    med = lambda rows, f: wmedian([(f(p), p["w"]) for p in rows]) if rows else None
    lines = lambda p: p["add"] + p["del"]
    return {
        "n": len(ok), "prs": wsum(ok), "precision": wsum(ok) / wsum(rows) if rows else None,
        "drafts": share(ok, lambda p: p["draft"] and not p["merged"]),
        "merged": share(ready, lambda p: bool(p["merged"])),
        "merged_settle": share(ready, lambda p: bool(p["merged"]) and hours(p) <= settle * 24),
        "closed_unmerged": share(ready, lambda p: bool(p["closed"]) and not p["merged"]),
        "still_open": share(ready, lambda p: not p["closed"]),
        "merged_prs": wsum(merged),
        "hours": med(merged, hours), "within_hour": share(merged, lambda p: hours(p) <= 1),
        "lines": med(merged, lines), "added": med(merged, lambda p: p["add"]), "files": med(merged, lambda p: p["files"]),
        "lines_added": sum(min(p["add"], LINES_CAP) * p["w"] for p in merged),
        "fix_share": share(merged, lambda p: p["fix"] or bool(p["issues"])), "fix_hours": med(fixes, hours),
        "linked_share": share(merged, lambda p: bool(p["issues"])),
        "issue_days": wmedian(to_fix) if len(to_fix) >= MIN_LINKED else None, "issue_n": len(to_fix),
        "self_merged": share(merged, lambda p: bool(p["merger"]) and p["merger"] == p["author"]),
        "big_share": share(ready, lambda p: p["stars"] >= 10),
        "big_merged": share(big, lambda p: bool(p["merged"])), "big_hours": med([p for p in big if p["merged"]], hours),
        "ttm_dist": wdist(merged, hours, TTM), "size_dist": wdist(merged, lines, PR_SIZES),
    }


def checks(data):
    """Per stratum: does the sample agree with GitHub's own counts?"""
    out = []
    for i, st in enumerate(data["strata"]):
        got = [p for p in data["prs"] if p["stratum"] == i]
        mine = share(got, lambda p: bool(p["merged"]))
        theirs = st["merged_count"] / st["count"] if st["count"] else None
        margin = 1.96 * math.sqrt(mine * (1 - mine) / len(got)) if st["mode"] == "sample" and got else 0
        ok = mine is None or theirs is None or abs(mine - theirs) <= margin + 0.01
        if st["mode"] == "all":
            ok = ok and abs(st["read"] - st["count"]) <= max(5, 0.01 * st["count"])
        out.append({**st, "mine": mine, "theirs": theirs, "margin": margin, "ok": ok,
                    "confirmed": share(got, lambda p: p["sig"])})
    return out


# ---------- page ----------

def key_points(st, days, settle):
    """The bottom line and the key points, written from the numbers so they stay true on every run."""
    A = [k for k in AGENTS if st[k]["n"]]
    o = st[OTHER]
    by = lambda f, rev=False: sorted([k for k in A if st[k][f] is not None], key=lambda k: -st[k][f] if rev else st[k][f])
    L = lambda k: esc(label(k))
    to_fix = lambda k: fmt_hours(st[k]["issue_days"] * 24) if st[k]["issue_days"] is not None else "–"
    vol, acc, big, fast, fast_big, fixed = by("prs", True), by("merged", True), by("lines", True), by("hours"), by("big_hours"), by("issue_days")

    bottom = (f"{L(big[0])} writes the biggest PRs (median {fnum(st[big[0]]['lines'])} lines changed) and "
              f"{L(acc[0])} gets the largest share merged ({fpct(st[acc[0]]['merged'])}).")
    if fixed:
        lo, hi = st[fixed[0]]["issue_days"], st[fixed[-1]]["issue_days"]
        span = to_fix(fixed[0]) if len(fixed) == 1 else f"{fmt_hours(lo * 24)} to {fmt_hours(hi * 24)}"
        bottom += (f" Fixes are fast with all of them: a median of {span} from an issue being opened to its fix being merged"
                   + (f", against {to_fix(OTHER)} for unsigned PRs." if o["issue_days"] is not None else "."))
    bottom += " Total volume cannot be compared fairly, because each tool leaves a different trace on GitHub."

    pts = [
        f"<b>Visible volume.</b> In {days} days, " + ", ".join(f"{L(k)} {short(st[k]['prs'])}" for k in vol)
        + " PRs carried a signature, and the merged ones added about " + ", ".join(f"{short(st[k]['lines_added'])} lines ({L(k)})" for k in vol)
        + f", counting at most {LINES_CAP:,} per PR. Each tool leaves a different trace, so this ranks how visible each tool is more than how much it is used.",
        f"<b>Code per PR.</b> The median merged PR changes " + ", ".join(f"{fnum(st[k]['lines'])} lines with {L(k)}" for k in big)
        + f", against {fnum(o['lines'])} for unsigned PRs.",
        f"<b>Acceptance.</b> Of PRs not left in draft, " + ", ".join(f"{L(k)} {fpct(st[k]['merged'])}" for k in acc)
        + f" were merged (unsigned PRs {fpct(o['merged'])}). In repositories with 10+ stars: "
        + ", ".join(f"{L(k)} {fpct(st[k]['big_merged'])}" for k in by("big_merged", True)) + f" (unsigned {fpct(o['big_merged'])}).",
        f"<b>Speed.</b> Median time from opening to merge: " + ", ".join(f"{L(k)} {fmt_hours(st[k]['hours'])}" for k in fast)
        + f"; unsigned PRs {fmt_hours(o['hours'])}. Most PRs are merged by the account that opened them ("
        + ", ".join(f"{L(k)} {fpct(st[k]['self_merged'])}" for k in A)
        + "), so these times mostly measure how quickly people accept their own agent's work. In repositories with 10+ stars: "
        + ", ".join(f"{L(k)} {fmt_hours(st[k]['big_hours'])}" for k in fast_big) + f" (unsigned {fmt_hours(o['big_hours'])}).",
    ]
    fix = (f"<b>Fixing problems.</b> Fixes are " + ", ".join(f"{fpct(st[k]['fix_share'])} of {L(k)}'s merged PRs" for k in A)
           + ". Median time to merge a fix: " + ", ".join(f"{L(k)} {fmt_hours(st[k]['fix_hours'])}" for k in by("fix_hours")) + ".")
    if fixed:
        fix += (" Where a PR names the issue it closes, the median time from the issue being opened to the fix being merged is "
                + ", ".join(f"{to_fix(k)} for {L(k)} ({st[k]['issue_n']:,} PRs)" for k in fixed)
                + (f"; {to_fix(OTHER)} for unsigned PRs." if o["issue_days"] is not None else ".")
                + f" {L(by('linked_share', True)[0])} PRs name the issue most often ({fpct(st[by('linked_share', True)[0]]['linked_share'])}).")
    pts.append(fix)
    return bottom, pts


def render(data, st, chk, out_name):
    as_of, s, e = ts(data["as_of"]), ts(data["start"]), ts(data["end"])
    days, settle = data["days"], data["settle"]
    bottom, pts = key_points(st, days, settle)
    sw = lambda k: f'<span class="sw" style="background:{color(k)}"></span>'
    keys = AGENTS + [OTHER]
    p = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Agent PRs Across GitHub</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>{CSS}
.cards {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); }}
.kpis {{ grid-template-columns: 1fr; gap: 10px; }}
.kpi .v {{ font-size: 24px; }}
.ext {{ display: grid; gap: 10px; margin-top: 12px; }}
.ext .src {{ border: 1px solid var(--rule); border-radius: 10px; padding: 12px 16px; }}
.ext .src b {{ font-size: 15px; }}
.ext .src p {{ margin: 4px 0 0; color: var(--ink2); font-size: 13.5px; }}
td.ok {{ color: var(--ink); }} td.no {{ color: #d03b3b; font-weight: 600; }}
</style></head><body><main class="wrap">
<p class="eyebrow">Across public GitHub · pull requests written by AI coding agents</p>
<h1>Which agent ships more code and fixes problems faster?</h1>
<p class="period">PRs created {fdate(s)} – {fdate(e - timedelta(seconds=1))}, status as of {fdate(as_of)}: every PR had at least {settle} days to be reviewed.</p>
<p class="bottom"><b>Bottom line</b>{bottom}</p>
<div class="cards">"""]
    for k in keys:
        x = st[k]
        p.append(f"""<section class="card"><h3>{sw(k)}{esc(label(k))}</h3><div class="kpis">
<div class="kpi"><div class="v">{short(x['prs'])}</div><div class="k">{'signed PRs' if k != OTHER else 'PRs'} opened in {days} days</div></div>
<div class="kpi"><div class="v">{fpct(x['merged'])}</div><div class="k">merged, of PRs not left in draft</div></div>
<div class="kpi"><div class="v">{fmt_hours(x['hours'])}</div><div class="k">median time to merge</div></div>
<div class="kpi"><div class="v">{fnum(x['lines'])}</div><div class="k">median lines changed per merged PR</div></div>
</div></section>""")
    p.append('</div><h2>Key points</h2><ul class="take">' + "".join(f"<li>{t}</li>" for t in pts) + "</ul>")

    who = lambda k: (color(k), label(k))
    p.append(f"""<h2>Speed and size</h2>
<div class="fig"><h3>Time from opening a PR to merging it</h3><p>Merged PRs of the cohort. Estimated PR counts are in the tooltips.</p>{chart_buckets([(k, st[k]['ttm_dist'], f"median {fmt_hours(st[k]['hours'])}") for k in keys], TTM, 'PRs (est.)', who)}</div>
<div class="fig"><h3>Lines changed per merged PR</h3><p>Lines added plus lines deleted.</p>{chart_buckets([(k, st[k]['size_dist'], f"median {fnum(st[k]['lines'])} lines") for k in keys], PR_SIZES, 'PRs (est.)', who)}</div>""")

    rows = [
        ("Volume (visible signatures only)", None, None),
        ("PRs opened", "prs", short), ("PRs merged", "merged_prs", short),
        (f"Lines added by merged PRs (at most {LINES_CAP:,} per PR)", "lines_added", short),
        ("Acceptance (PRs not left in draft)", None, None),
        ("Merged so far", "merged", fpct), (f"Merged within {settle} days", "merged_settle", fpct),
        ("Closed without merging", "closed_unmerged", fpct), ("Still open", "still_open", fpct),
        ("Left in draft (share of all PRs)", "drafts", fpct),
        ("Speed", None, None),
        ("Median time to merge", "hours", fmt_hours), ("Merged within an hour", "within_hour", fpct),
        ("Size of merged PRs", None, None),
        ("Median lines changed", "lines", fnum), ("Median lines added", "added", fnum), ("Median files changed", "files", fnum),
        ("Fixing problems", None, None),
        ("Merged PRs that are fixes (title says fix, or closes an issue)", "fix_share", fpct),
        ("Median time to merge a fix", "fix_hours", fmt_hours),
        ("Merged PRs that name the issue they close", "linked_share", fpct),
        (f"Median time from issue opened to fix merged (shown with {MIN_LINKED}+ PRs)", "issue_days", lambda v: fmt_hours(v * 24)),
        ("PRs behind that median", "issue_n", fnum),
        ("Where and who", None, None),
        ("PRs in repositories with 10+ stars", "big_share", fpct),
        ("Merged, repositories with 10+ stars", "big_merged", fpct),
        ("Median time to merge, repositories with 10+ stars", "big_hours", fmt_hours),
        ("Merged by the account that opened the PR", "self_merged", fpct),
        ("Sample", None, None),
        ("PRs read one by one", "n", fnum), ("Signature confirmed in the PR", "precision", fpct),
    ]
    p.append('<h2>All the numbers</h2><div class="tbl"><table><thead><tr><th></th>'
             + "".join(f"<th>{sw(k)} {esc(label(k))}</th>" for k in keys) + "</tr></thead><tbody>")
    for text, f, fmt in rows:
        if f is None:
            p.append(f'<tr class="grp"><td colspan="{len(keys) + 1}">{esc(text)}</td></tr>')
            continue
        p.append(f"<tr><td>{esc(text)}</td>" + "".join(f'<td><span class="val">{fmt(st[k][f]) if st[k][f] is not None else "–"}</span></td>' for k in keys) + "</tr>")
    p.append("</tbody></table></div>")

    p.append('<h2>What other sources say</h2><p class="sub">Checked on 27 Oct 2026. Benchmarks test the tool and the model together; the studies count agent PRs on GitHub with their own methods.</p><div class="ext">')
    for title, what, links in EXTERNAL:
        p.append(f'<div class="src"><b>{esc(title)}</b><p>{what}</p><p>' + " · ".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in links) + "</p></div>")
    p.append("</div>")

    p.append('<h2>How the numbers were checked</h2><p class="sub">For each signature, the share of PRs merged in what was read is compared with GitHub search counts over the same dates. Sampled signatures should agree within the margin shown; signatures read in full should match exactly.</p>'
             '<div class="tbl"><table><thead><tr><th>Signature</th><th>PRs (search)</th><th>Read</th><th>Signature confirmed</th><th>Merged, search</th><th>Merged, read</th><th>Agrees</th></tr></thead><tbody>')
    for c in chk:
        read = f"{c['read']:,} (all)" if c["mode"] == "all" else f"{c['read']:,} (sample)"
        margin = f" ± {fpct(c['margin'], 1)}" if c["mode"] == "sample" else ""
        p.append(f"<tr><td>{sw(c['agent'])} {esc(label(c['agent']))}: {esc(c['label'])}</td><td>{c['count']:,}</td><td>{read}</td>"
                 f"<td>{fpct(c['confirmed'])}</td><td>{fpct(c['theirs'], 1)}</td><td>{fpct(c['mine'], 1)}{margin}</td>"
                 f"<td class=\"{'ok' if c['ok'] else 'no'}\">{'Yes' if c['ok'] else 'No'}</td></tr>")
    p.append("</tbody></table></div>")

    queries = "".join(f"<li>{sw(c['agent'])} {esc(label(c['agent']))}: <code>{esc(c['query'])}</code></li>" for c in chk)
    p.append(f"""<section class="notes"><b>How to read this</b><ul>
<li><b>Signatures.</b> A PR counts for an agent when it carries that agent's trace. Claude Code adds “Generated with Claude Code” to the PRs it writes unless the user turns it off, and its web and GitHub Action versions name branches <code>claude/…</code>. Codex is visible only for PRs from Codex cloud, on <code>codex/…</code> branches; PRs made with the local Codex CLI carry no mark. OpenCode PRs are counted by its description line or <code>opencode/…</code> branches. PRs without a trace are invisible, so every count is a floor and the volumes are not market shares.</li>
<li><b>Searches used</b>, each with <code>is:pr</code> and the creation dates:<ul>{queries}</ul> Repositories of Anthropic, OpenAI and Anomaly/SST are left out of the agent searches. Search matches words loosely, so each PR read is also checked for the exact line or branch; PRs without it are dropped.</li>
<li><b>Unsigned PRs</b> are a reference: public PRs with none of these signatures, leaving out Copilot and Cursor branches and dependency bots. Because search matches a phrase's words anywhere, PRs that merely mention these agents in similar words are left out too. Some unsigned PRs are still written with AI help.</li>
<li><b>Sampling.</b> Signatures with up to {FULL_BELOW:,} PRs are read in full. Larger ones are read as the first {PER_SLICE} PRs of randomly placed time slices; each PR is weighted by how many PRs its slice held, so medians and shares stand for the whole cohort.</li>
<li><b>Merged</b> counts PRs merged by the time of reading. A merge is not proof of quality: many agent PRs are merged by the person who asked the agent for them, with no second reviewer.</li>
<li><b>Fixes</b> are merged PRs whose title starts with “fix” or that name an issue they close. Issue-to-fix time uses only PRs that name the issue, the first one if several.</li>
<li>Only public repositories are visible. Data: GitHub search and GraphQL APIs, read with <code>gh</code>. Rebuild with <code>scripts/agents.py</code>. File: {esc(out_name)}.</li>
</ul></section></main></body></html>""")
    return "".join(p)


EXTERNAL = [
    ("Terminal-Bench 4.0 (benchmark)",
     "Scores pairs of agent and model on real terminal tasks. Third-party readings of the October 2026 board disagree: "
     "Claude Code with Claude Opus 5.5 at 64.9% against Codex with GPT-6.1 Sol at 58.2%, or the two close to level (57.9% and 58.2%) with other model pairings. "
     "OpenCode is rarely listed because it runs any model. The same model can score over 15 points apart in different tools.",
     [("Leaderboard", "https://www.tbench.ai/leaderboard"), ("Morph", "https://www.morphllm.com/comparisons/codex-vs-claude-code"),
      ("Alongside", "https://www.alongside.team/blog/best-ai-coding-agent-harness-2026"),
      ("Arena: HarnessTax", "https://arena.ai/blog/coding-agents-harness-tax")]),
    ("Amplifying: coding agents, autonomy and trust",
     "Agent PRs in public repositories, mid-2026. Merged: Claude Code 91.6%, Codex 86.8%. Median time to merge, over 268 merged PRs "
     "in repositories with 10+ stars or owned by an organization: Claude Code 38 min, Codex 8 min. PRs that touch tests: Codex 69.2%, Claude Code 47.5%. "
     "OpenCode is not covered.",
     [("amplifying.ai", "https://amplifying.ai/coding-agents/autonomy.md")]),
    ("PR Arena",
     "Live count of agent PRs by branch prefix or bot account. Codex: 6.96M merged of 7.73M PRs ready for review (90.1%). "
     "Claude Code and OpenCode are not tracked.",
     [("prarena.ai", "https://prarena.ai/")]),
    ("Academic studies on the AIDev dataset (2026)",
     "Merge rates depend on the sample: Claude Code 59% to 84%, Codex 73% to 83%. Agent PRs are smaller and more localized than human ones. "
     "One study notes Claude Code is used in larger, more complex repositories, which affects the comparison. OpenCode is not covered.",
     [("How AI coding agents modify code", "https://arxiv.org/html/2601.17581"),
      ("How do AI coding agents contribute", "https://arxiv.org/pdf/2607.21832"),
      ("On the use of agentic coding", "https://arxiv.org/pdf/2509.14745")]),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="length of the cohort in days (default 30)")
    ap.add_argument("--settle", type=int, default=7, help="days between the cohort's end and now (default 7)")
    ap.add_argument("--sample", type=int, default=1000, help="PRs to read per large signature (default 1000)")
    ap.add_argument("--no-fetch", action="store_true", help="rebuild the page from the last sample on disk")
    ap.add_argument("--reuse-days", type=float, help="reuse the last sample if it is younger than this many days")
    ap.add_argument("--out", help="output HTML path (default reports/harness-agents_<date>.html)")
    args = ap.parse_args()
    data = latest()
    if args.reuse_days and data and now() - ts(data["as_of"]) < timedelta(days=args.reuse_days):
        print(f"reusing the agent PR sample of {fdate(ts(data['as_of']))}; it is refreshed every {args.reuse_days:g} days")
    elif not args.no_fetch:
        data = fetch(args.days, args.settle, args.sample)
    if not data:
        print("no agent PR sample on disk yet; run scripts/agents.py without --no-fetch")
        return
    st = {k: stats([p for p in data["prs"] if p["agent"] == k], data["settle"]) for k in AGENTS + [OTHER]}
    chk = checks(data)
    out = args.out or os.path.join(ROOT, "reports", f"harness-agents_{ts(data['as_of']):%Y-%m-%d}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(render(data, st, chk, os.path.basename(out)))
    print(f"agents -> {os.path.relpath(out, ROOT)}")
    for c in chk:
        print(f"  {'ok   ' if c['ok'] else 'CHECK'} {label(c['agent']):12} {c['label'][:52]:52} search {c['count']:>10,}  "
              f"merged {fpct(c['theirs'], 1):>6} vs read {fpct(c['mine'], 1):>6}  confirmed {fpct(c['confirmed'])}")
    if not all(c["ok"] for c in chk):
        sys.exit(1)


if __name__ == "__main__":
    main()
