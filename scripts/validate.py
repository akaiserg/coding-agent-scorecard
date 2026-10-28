#!/usr/bin/env python3
"""Cross-check the period numbers used by the reports against GitHub's own counts.

  validate.py [--as-of ISO] [--days N]

For the period ending at --as-of (default: last sync), recomputes from the downloaded data the numbers
report.py and social.py show, and compares them with GitHub search counts over the same timestamps:
new issues, closed issues, completed closes, merged PRs, and open issues at the start and end of the
period. Also checks that open at end = open at start + new - closed, and the star count.
Exits non-zero if any difference is above 1% (and above 5 items).
"""
import argparse
import json
import os
import sys
import time
from datetime import timedelta

from classify import load_team, npm_daily, prepare, fixes, releases, stars_now
from common import RAW, data_as_of, graphql, iso, now, rest, ts
from config import REPOS
from report import KEYS, name, period_stats

STARS = """query($owner:String!, $name:String!) { repository(owner:$owner, name:$name) { stargazerCount } }"""


def search(q):
    time.sleep(2.2)  # the search API allows 30 requests a minute
    return rest("search/issues", {"q": q, "per_page": 1})["total_count"]


def open_at(repo, t):
    """Issues open at time t per GitHub: created before t and either still open or closed after t."""
    return (search(f"repo:{repo} is:issue is:open created:<{iso(t)}")
            + search(f"repo:{repo} is:issue is:closed created:<{iso(t)} closed:>={iso(t)}"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="end of the period (default: last sync of every repo)")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    e = ts(args.as_of) if args.as_of else (data_as_of() or now())
    s = e - timedelta(days=args.days)
    window = f"{iso(s)}..{iso(e)}"
    print(f"period {iso(s)} .. {iso(e)}\n")
    print(f"{'repo':12} {'metric':26} {'ours':>9} {'GitHub':>9} {'diff':>7}")
    bad = 0

    def row(k, metric, ours, theirs):
        nonlocal bad
        diff = ours - theirs
        off = abs(diff) > 5 and abs(diff) > 0.01 * max(1, theirs)
        bad += off
        print(f"{k:12} {metric:26} {ours:>9,} {theirs:>9,} {diff:>+7,}{'  <-- check' if off else ''}")

    for k in KEYS:
        repo = REPOS[k]["repo"]
        issues, prs = prepare(k, "issues", e), prepare(k, "prs", e)
        c = period_stats(issues, prs, load_team(k, prs + issues), fixes(k, prs, e), releases(k, e), npm_daily(k, e), k, s, e)
        row(k, "new issues", c["opened"], search(f"repo:{repo} is:issue created:{window}"))
        row(k, "issues closed", c["closed"], search(f"repo:{repo} is:issue closed:{window}"))
        row(k, "closed as completed", c["completed"], search(f"repo:{repo} is:issue reason:completed closed:{window}"))
        row(k, "PRs merged", c["prs_merged"], search(f"repo:{repo} is:pr merged:{window}"))
        row(k, "open issues at start", c["backlog_start"], open_at(repo, s))
        row(k, "open issues at end", c["backlog_end"], open_at(repo, e))
        identity = c["backlog_start"] + c["opened"] - c["closed"]
        row(k, "start + new - closed", identity, c["backlog_end"])
        owner, nm = repo.split("/")
        row(k, "stars (last run vs now)", stars_now(k), graphql(STARS, {"owner": owner, "name": nm})["data"]["repository"]["stargazerCount"])
        print()
    print("all numbers within 1%" if not bad else f"{bad} numbers differ by more than 1%")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
