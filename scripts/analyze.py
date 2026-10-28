#!/usr/bin/env python3
"""Compute overall and monthly issue, PR, fix, release and usage metrics for every configured repo.

  analyze.py [--as-of ISO]

--as-of defaults to the last time every repo was synced. Writes data/metrics.json and
data/monthly_KEY.csv.
"""
import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from classify import (changelog_by_month, fixes, is_auto_closed, is_bot, is_external, is_fix_pr, labels_of,
                      load_team, npm_daily, npm_typical, prepare, releases, stars_now)
from common import ROOT, data_as_of, iso, now, ts
from config import MAINTAINER_ASSOCIATIONS, REPOS


def month(d):
    return d.strftime("%Y-%m")


def months_between(start, end):
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_end(key, as_of):
    y, m = map(int, key.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return min(datetime(y, m, 1, tzinfo=timezone.utc), as_of)


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def share(a, b):
    return round(100 * a / b, 1) if b else None


def days(a, b):
    return (b - a).total_seconds() / 86400


def per(a, b, scale):
    return round(a / b * scale, 2) if a is not None and b else None


def analyze_repo(key, as_of):
    issues = prepare(key, "issues", as_of)
    prs = prepare(key, "prs", as_of)
    team = load_team(key, prs + issues)
    mkeys = months_between(min(n["_c"] for n in issues), as_of)

    # ---------- issues by month ----------
    M = {k: defaultdict(float) for k in mkeys}
    ttc_by_close_month = defaultdict(list)
    bug_ttc_by_close_month = defaultdict(list)
    for n in issues:
        mc = month(n["_c"])
        M[mc]["opened"] += 1
        if n["_bug"]:
            M[mc]["bugs_opened"] += 1
        if not n["_x"]:
            continue
        mx = month(n["_x"])
        reason = n["_reason"].lower()
        d = days(n["_c"], n["_x"])
        M[mx]["closed"] += 1
        M[mx][f"closed_{reason}"] += 1
        if is_auto_closed(n):
            M[mx]["closed_auto"] += 1
        ttc_by_close_month[mx].append(d)
        if n["_bug"]:
            M[mx]["bugs_closed"] += 1
            if reason == "completed":
                bug_ttc_by_close_month[mx].append(d)
        # cohort view: what happened to the issues opened in month mc
        M[mc][f"cohort_{reason}"] += 1
        M[mc]["cohort_closed_now"] += 1
        for limit in (1, 7, 30):
            if d <= limit:
                M[mc][f"cohort_closed_{limit}d"] += 1

    # open backlog at each month end
    events = sorted([(n["_c"], 1, n["_bug"]) for n in issues] + [(n["_x"], -1, n["_bug"]) for n in issues if n["_x"]],
                    key=lambda e: e[0])
    i, open_now, open_bugs = 0, 0, 0
    for k in mkeys:
        end = month_end(k, as_of)
        while i < len(events) and events[i][0] < end:
            open_now += events[i][1]
            open_bugs += events[i][1] if events[i][2] else 0
            i += 1
        M[k]["backlog_end"] = open_now
        M[k]["bug_backlog_end"] = open_bugs

    for k in mkeys:
        r = M[k]
        r["median_ttc_days"] = pct(ttc_by_close_month[k], 0.5)
        r["p90_ttc_days"] = pct(ttc_by_close_month[k], 0.9)
        r["median_bug_ttc_days"] = pct(bug_ttc_by_close_month[k], 0.5)
        r["close_ratio"] = round(r["closed"] / r["opened"], 3) if r["opened"] else None

    # ---------- PRs by month ----------
    pr_ttm, pr_ttm_ext = defaultdict(list), defaultdict(list)
    issues_closed_by_pr = set()
    for p in prs:
        mc = month(p["_c"])
        M[mc]["prs_opened"] += 1
        if is_external(p, team):
            M[mc]["prs_opened_external"] += 1
        if p["_x"] and not p["_m"]:
            M[month(p["_x"])]["prs_closed_unmerged"] += 1
            if is_external(p, team):
                M[month(p["_x"])]["prs_closed_unmerged_external"] += 1
        if not p["_m"]:
            continue
        mm = month(p["_m"])
        hours = days(p["_c"], p["_m"]) * 24
        M[mm]["prs_merged"] += 1
        pr_ttm[mm].append(hours)
        if is_fix_pr(p):
            M[mm]["fix_prs_merged"] += 1
        if is_external(p, team):
            M[mm]["prs_merged_external"] += 1
            pr_ttm_ext[mm].append(hours)
        if is_bot(p):
            M[mm]["prs_merged_bot"] += 1
        refs = [r["number"] for r in (p.get("closingIssuesReferences") or {}).get("nodes", [])]
        if refs:
            M[mm]["prs_merged_closing_issues"] += 1
            issues_closed_by_pr.update(refs)
    for k in mkeys:
        M[k]["median_pr_hours_to_merge"] = pct(pr_ttm[k], 0.5)
        M[k]["median_ext_pr_hours_to_merge"] = pct(pr_ttm_ext[k], 0.5)

    # open PRs at each month end
    events = sorted([p["_c"] for p in prs]), sorted(p["_x"] for p in prs if p["_x"])
    for k in mkeys:
        end = month_end(k, as_of)
        M[k]["prs_open_end"] = sum(1 for t in events[0] if t < end) - sum(1 for t in events[1] if t < end)

    # ---------- usage: npm downloads ----------
    npm = npm_daily(key, as_of)
    for k in mkeys:
        start = datetime.strptime(k, "%Y-%m").replace(tzinfo=timezone.utc)
        M[k]["days"] = round(days(start, month_end(k, as_of)), 2)
        typical = npm_typical(npm, start, month_end(k, as_of))
        if typical:
            M[k]["npm_daily_median"] = typical
            M[k]["npm_days"] = sum(1 for d in npm if start.date() <= d < month_end(k, as_of).date())

    # ---------- fixes and versions ----------
    for d in fixes(key, prs, as_of):
        if month(d) in M:
            M[month(d)]["fixes"] += 1
    for d, pre in releases(key, as_of):
        if month(d) in M:
            M[month(d)]["versions_pre" if pre else "versions_stable"] += 1

    # ---------- overall ----------
    closed = [n for n in issues if n["_x"]]
    open_issues = [n for n in issues if not n["_x"]]
    reasons = Counter(n["_reason"] for n in closed)
    ttc_all = [days(n["_c"], n["_x"]) for n in closed]
    ttc_completed = [days(n["_c"], n["_x"]) for n in closed if n["_reason"] == "COMPLETED"]
    bugs = [n for n in issues if n["_bug"]]
    bugs_closed = [n for n in bugs if n["_x"]]
    bugs_completed = [n for n in bugs_closed if n["_reason"] == "COMPLETED"]
    open_age = [days(n["_c"], as_of) for n in open_issues]
    last90 = as_of - timedelta(days=90)
    rec_opened = [n for n in issues if n["_c"] >= last90]
    rec_closed = [n for n in closed if n["_x"] >= last90]
    merged = [p for p in prs if p["_m"]]
    ext_prs = [p for p in prs if is_external(p, team)]
    ext_merged = [p for p in ext_prs if p["_m"]]
    last3 = [k for k in mkeys if k < month(as_of)][-3:]
    stars = stars_now(key)
    npm30, npm_prev30, npm90 = (npm_typical(npm, as_of - timedelta(days=a), as_of - timedelta(days=b)) for a, b in ((30, 0), (60, 30), (90, 0)))
    pr_closed = [p for p in prs if p["_x"]]

    overall = {
        "repo": REPOS[key]["repo"],
        "first_issue": iso(min(n["_c"] for n in issues)),
        "issues_total": len(issues),
        "issues_open": len(open_issues),
        "issues_closed": len(closed),
        "pct_open": share(len(open_issues), len(issues)),
        "close_reasons": dict(reasons),
        "pct_completed_of_closed": share(reasons.get("COMPLETED", 0), len(closed)),
        "pct_not_planned_of_closed": share(reasons.get("NOT_PLANNED", 0), len(closed)),
        "pct_duplicate_of_closed": share(reasons.get("DUPLICATE", 0), len(closed)),
        "pct_auto_closed_of_closed": share(sum(1 for n in closed if is_auto_closed(n)), len(closed)),
        "pct_issues_completed": share(reasons.get("COMPLETED", 0), len(issues)),
        "median_ttc_days": pct(ttc_all, 0.5),
        "p90_ttc_days": pct(ttc_all, 0.9),
        "median_ttc_completed_days": pct(ttc_completed, 0.5),
        "pct_closed_within_1d": share(sum(1 for d in ttc_all if d <= 1), len(issues)),
        "pct_closed_within_7d": share(sum(1 for d in ttc_all if d <= 7), len(issues)),
        "pct_closed_within_30d": share(sum(1 for d in ttc_all if d <= 30), len(issues)),
        "open_median_age_days": pct(open_age, 0.5),
        "open_older_than_90d": sum(1 for a in open_age if a > 90),
        "pct_open_older_90d": share(sum(1 for a in open_age if a > 90), len(open_issues)),
        "bugs_total": len(bugs),
        "pct_bugs": share(len(bugs), len(issues)),
        "bugs_open": len(bugs) - len(bugs_closed),
        "bugs_pct_completed": share(len(bugs_completed), len(bugs)),
        "bugs_median_ttc_completed_days": pct([days(n["_c"], n["_x"]) for n in bugs_completed], 0.5),
        "last90_opened": len(rec_opened),
        "last90_closed": len(rec_closed),
        "last90_net": len(rec_opened) - len(rec_closed),
        "last90_opened_per_day": round(len(rec_opened) / 90, 1),
        "last90_closed_per_day": round(len(rec_closed) / 90, 1),
        "pct_issues_by_maintainers": share(sum(1 for n in issues if n["authorAssociation"] in MAINTAINER_ASSOCIATIONS), len(issues)),
        "top_open_labels": Counter(l for n in open_issues for l in labels_of(n)).most_common(40),
        "prs_total": len(prs),
        "prs_merged": len(merged),
        "prs_open": sum(1 for p in prs if not p["_x"]),
        "prs_external_total": len(ext_prs),
        "prs_external_merged": len(ext_merged),
        "pct_external_prs_merged": share(len(ext_merged), len(ext_prs)),
        "pct_merged_prs_external": share(len(ext_merged), len(merged)),
        "pct_merged_prs_bot": share(sum(1 for p in merged if is_bot(p)), len(merged)),
        "median_pr_hours_to_merge": pct([days(p["_c"], p["_m"]) * 24 for p in merged], 0.5),
        "median_ext_pr_hours_to_merge": pct([days(p["_c"], p["_m"]) * 24 for p in ext_merged], 0.5),
        "team_size": len(team),
        "fixes_total": int(sum(M[k]["fixes"] for k in mkeys)),
        "fixes_last3m_per_month": round(sum(M[k]["fixes"] for k in last3) / 3, 1),
        "merged_prs_closing_issues": sum(1 for p in merged if (p.get("closingIssuesReferences") or {}).get("nodes")),
        "issues_closed_by_merged_pr": sum(1 for n in issues if n["number"] in issues_closed_by_pr),
        "pct_completed_linked_to_pr": share(sum(1 for n in closed if n["_reason"] == "COMPLETED" and n["number"] in issues_closed_by_pr),
                                            reasons.get("COMPLETED", 0)),
        "distinct_pr_merge_authors": len({(p.get("author") or {}).get("login") for p in merged}),
        "versions_stable": int(sum(M[k]["versions_stable"] for k in mkeys)),
        "versions_pre": int(sum(M[k]["versions_pre"] for k in mkeys)),
        "versions_stable_last3m": int(sum(M[k]["versions_stable"] for k in last3)),
        "last_full_months": last3,
        "prs_closed_unmerged": len(pr_closed) - len(merged),
        "pct_closed_prs_merged": share(len(merged), len(pr_closed)),
        "last90_prs_opened": sum(1 for p in prs if p["_c"] >= last90),
        "last90_prs_merged": sum(1 for p in merged if p["_m"] >= last90),
        # Usage. Stars are the latest recorded count (GitHub does not say when each star was given);
        # npm figures are the median day, over the days npm reported.
        "stars": stars,
        "issues_per_1k_stars": per(len(issues), stars, 1000),
        "open_issues_per_1k_stars": per(len(open_issues), stars, 1000),
        "last90_opened_per_1k_stars": per(len(rec_opened), stars, 1000),
        "outside_prs_per_1k_stars": per(len(ext_prs), stars, 1000),
        "npm_daily_30d": round(npm30) if npm30 else None,
        "npm_daily_prev30d": round(npm_prev30) if npm_prev30 else None,
        "npm_change_30d": share(npm30 - npm_prev30, npm_prev30) if npm30 and npm_prev30 else None,
        "last90_opened_per_100k_downloads": per(len(rec_opened), npm90 * 90 if npm90 else None, 100000),
    }

    monthly = []
    for k in mkeys:
        row = {"month": k}
        row.update({f: (round(v, 2) if isinstance(v, float) else v) for f, v in M[k].items()})
        monthly.append(row)
    return overall, monthly


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="ISO time to compute metrics at (default: last sync of every repo)")
    args = ap.parse_args()
    as_of = ts(args.as_of) if args.as_of else (data_as_of() or now())

    result = {"as_of": iso(as_of), "repos": {}, "monthly": {}, "changelog_monthly": {}}
    for key in REPOS:
        overall, monthly = analyze_repo(key, as_of)
        result["repos"][key] = overall
        result["monthly"][key] = monthly
        result["changelog_monthly"][key] = changelog_by_month(key, as_of)
        fields = sorted({f for r in monthly for f in r} - {"month"})
        with open(os.path.join(ROOT, "data", f"monthly_{key}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["month"] + fields)
            w.writeheader()
            w.writerows(monthly)
        print(f"{key}: {overall['issues_total']} issues, {overall['prs_total']} PRs, {len(monthly)} months")
    json.dump(result, open(os.path.join(ROOT, "data", "metrics.json"), "w"), indent=1, default=str)
    print(f"metrics as of {iso(as_of)} -> data/metrics.json")


if __name__ == "__main__":
    main()
