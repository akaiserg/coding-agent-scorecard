#!/usr/bin/env python3
"""Turn data/metrics.json and the fetched extras into flat row files for the claude.ai dashboard.

Writes data/dashboard/{summary,monthly,cc_changelog,top_open,open_labels}.json. Shares are 0-1; npm
figures are the median day over the days npm reported.
"""
import json
import os

from common import RAW, ROOT
from config import META_LABELS, REPOS

OUT = os.path.join(ROOT, "data", "dashboard")
BUG_METHOD = {True: "bug label or [BUG] title", False: "title wording (estimate)"}
FIX_METHOD = {"changelog": "CHANGELOG Fixed entries", "prs": "merged PRs titled fix"}


def frac(v):
    return round(v / 100, 4) if v is not None else None


def share(a, b):
    return round(a / b, 4) if b else None


def main():
    metrics = json.load(open(os.path.join(ROOT, "data", "metrics.json")))
    info = {json.loads(l)["nameWithOwner"]: json.loads(l) for l in open(os.path.join(RAW, "repo_info.jsonl"))}
    extras = {json.loads(l)["repo"]: json.loads(l) for l in open(os.path.join(RAW, "search_extras.jsonl"))}
    as_of = metrics["as_of"]
    as_of_month = as_of[:7]

    summary, monthly, top_open, open_labels, changelog = [], [], [], [], []
    for i, (key, cfg) in enumerate(REPOS.items()):
        o = metrics["repos"][key]
        ri, ex = info[cfg["repo"]], extras[cfg["repo"]]
        reasons = o["close_reasons"]
        summary.append({
            "repo": key,
            "name": cfg["repo"],
            "order": i,
            "snapshot": as_of[:10],
            "stars": ri["stargazerCount"],
            "forks": ri["forkCount"],
            "language": (ri.get("primaryLanguage") or {}).get("name"),
            "issues_total": o["issues_total"],
            "issues_open": o["issues_open"],
            "issues_closed": o["issues_closed"],
            "pct_open": frac(o["pct_open"]),
            "closed_completed": reasons.get("COMPLETED", 0),
            "closed_not_planned": reasons.get("NOT_PLANNED", 0),
            "closed_duplicate": reasons.get("DUPLICATE", 0),
            "pct_completed_of_closed": frac(o["pct_completed_of_closed"]),
            "pct_not_planned_of_closed": frac(o["pct_not_planned_of_closed"]),
            "pct_duplicate_of_closed": frac(o["pct_duplicate_of_closed"]),
            "pct_auto_closed_of_closed": frac(o["pct_auto_closed_of_closed"]),
            "pct_issues_completed": frac(o["pct_issues_completed"]),
            "median_ttc_days": o["median_ttc_days"],
            "p90_ttc_days": o["p90_ttc_days"],
            "median_ttc_completed_days": o["median_ttc_completed_days"],
            "pct_closed_within_1d": frac(o["pct_closed_within_1d"]),
            "pct_closed_within_7d": frac(o["pct_closed_within_7d"]),
            "pct_closed_within_30d": frac(o["pct_closed_within_30d"]),
            "open_median_age_days": o["open_median_age_days"],
            "open_older_than_90d": o["open_older_than_90d"],
            "pct_open_older_90d": frac(o["pct_open_older_90d"]),
            "open_zero_comments": ex["open_zero_comments"],
            "pct_open_zero_comments": share(ex["open_zero_comments"], ri["openIssues"]["totalCount"]),
            "open_gt10_comments": ex["open_gt10_comments"],
            "bug_method": BUG_METHOD[cfg.get("bug_labels", True)],
            "bugs_total": o["bugs_total"],
            "pct_bugs": frac(o["pct_bugs"]),
            "bugs_open": o["bugs_open"],
            "bugs_pct_completed": frac(o["bugs_pct_completed"]),
            "bugs_median_ttc_completed_days": o["bugs_median_ttc_completed_days"],
            "last90_opened": o["last90_opened"],
            "last90_closed": o["last90_closed"],
            "last90_net": o["last90_net"],
            "last90_opened_per_day": o["last90_opened_per_day"],
            "last90_closed_per_day": o["last90_closed_per_day"],
            "prs_total": o["prs_total"],
            "prs_merged": o["prs_merged"],
            "prs_open": o["prs_open"],
            "prs_external_merged": o["prs_external_merged"],
            "pct_merged_prs_external": frac(o["pct_merged_prs_external"]),
            "pct_merged_prs_bot": frac(o["pct_merged_prs_bot"]),
            "median_pr_hours_to_merge": o["median_pr_hours_to_merge"],
            "median_ext_pr_hours_to_merge": o["median_ext_pr_hours_to_merge"],
            "team_size": o["team_size"],
            "fix_method": FIX_METHOD[cfg["fix_source"]],
            "fixes_total": o["fixes_total"],
            "fixes_last3m_per_month": o["fixes_last3m_per_month"],
            "issues_closed_by_merged_pr": o["issues_closed_by_merged_pr"],
            "pct_completed_linked_to_pr": frac(o["pct_completed_linked_to_pr"]),
            "versions_stable": o["versions_stable"],
            "versions_pre": o["versions_pre"],
            "versions_stable_last3m": o["versions_stable_last3m"],
            "latest_release": (ri.get("latestRelease") or {}).get("tagName"),
            "prs_closed_unmerged": o["prs_closed_unmerged"],
            "pct_closed_prs_merged": frac(o["pct_closed_prs_merged"]),
            "last90_prs_opened": o["last90_prs_opened"],
            "last90_prs_merged": o["last90_prs_merged"],
            "issues_per_1k_stars": o["issues_per_1k_stars"],
            "open_issues_per_1k_stars": o["open_issues_per_1k_stars"],
            "last90_opened_per_1k_stars": o["last90_opened_per_1k_stars"],
            "outside_prs_per_1k_stars": o["outside_prs_per_1k_stars"],
            "npm_package": cfg.get("downloads_package"),
            "pr_policy": ri.get("prCreationPolicy"),
            "npm_daily_30d": o["npm_daily_30d"],
            "npm_change_30d": frac(o["npm_change_30d"]),
            "last90_opened_per_100k_downloads": o["last90_opened_per_100k_downloads"],
        })

        for r in metrics["monthly"][key]:
            g = lambda f: r.get(f, 0)
            npm_daily = r.get("npm_daily_median")
            monthly.append({
                "repo": key,
                "month": r["month"],
                "partial": r["month"] == as_of_month,
                "opened": int(g("opened")),
                "closed": int(g("closed")),
                "net": int(g("opened") - g("closed")),
                "closed_completed": int(g("closed_completed")),
                "closed_not_planned": int(g("closed_not_planned")),
                "closed_duplicate": int(g("closed_duplicate")),
                "closed_auto": int(g("closed_auto")),
                "pct_completed_of_closed": share(g("closed_completed"), g("closed")),
                "backlog_end": int(g("backlog_end")),
                "bug_backlog_end": int(g("bug_backlog_end")),
                "bugs_opened": int(g("bugs_opened")),
                "bugs_closed": int(g("bugs_closed")),
                "close_ratio": r.get("close_ratio"),
                "median_ttc_days": r.get("median_ttc_days"),
                "median_bug_ttc_days": r.get("median_bug_ttc_days"),
                "cohort_pct_closed_7d": share(g("cohort_closed_7d"), g("opened")),
                "cohort_pct_open_now": share(g("opened") - g("cohort_closed_now"), g("opened")),
                "prs_opened": int(g("prs_opened")),
                "prs_merged": int(g("prs_merged")),
                "prs_merged_external": int(g("prs_merged_external")),
                "pct_merged_external": share(g("prs_merged_external"), g("prs_merged")),
                "median_pr_hours_to_merge": r.get("median_pr_hours_to_merge"),
                "median_ext_pr_hours_to_merge": r.get("median_ext_pr_hours_to_merge"),
                "fixes": int(g("fixes")),
                "versions_stable": int(g("versions_stable")),
                "versions_pre": int(g("versions_pre")),
                "prs_opened_external": int(g("prs_opened_external")),
                "prs_closed_unmerged": int(g("prs_closed_unmerged")),
                "prs_open_end": int(g("prs_open_end")),
                "pct_prs_merged": share(g("prs_merged"), g("prs_merged") + g("prs_closed_unmerged")),
                "npm_daily": round(npm_daily) if npm_daily else None,
                "issues_per_100k_downloads": round(g("opened") / (npm_daily * g("days")) * 1e5, 2) if npm_daily else None,
            })

        for rank, t in enumerate(ex["top_open_by_reactions"][:8], 1):
            top_open.append({"id": f"{key}#{t['number']}", "repo": key, "rank": rank, "number": t["number"],
                             "title": t["title"], "reactions": t["reactions"], "comments": t["comments"],
                             "created": t["created"]})

        topic = [(l, c) for l, c in o["top_open_labels"] if l.lower() not in META_LABELS][:8]
        open_labels += [{"id": f"{key}:{l}", "repo": key, "label": l, "open_issues": c,
                         "pct_of_open": share(c, o["issues_open"])} for l, c in topic]

        for m, v in metrics["changelog_monthly"].get(key, {}).items():
            changelog.append({"repo": key, "month": m, "partial": m == as_of_month,
                              **{f: v.get(f, 0) for f in ("versions", "fixed", "added", "improved", "changed", "other")}})

    os.makedirs(OUT, exist_ok=True)
    for name, rows in [("summary", summary), ("monthly", monthly), ("cc_changelog", changelog),
                       ("top_open", top_open), ("open_labels", open_labels)]:
        json.dump(rows, open(os.path.join(OUT, f"{name}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        print(f"data/dashboard/{name}.json: {len(rows)} rows")


if __name__ == "__main__":
    main()
