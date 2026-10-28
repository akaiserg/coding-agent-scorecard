#!/usr/bin/env python3
"""Build dashboard/index.html from page.html, expanding the scorecard rows into marked cells."""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPOS = ["claude-code", "codex", "opencode"]

# (field, label, format, which value is better: "high", "low" or None for no ranking)
SCORECARD = [
    ("Volume", [
        ("stars", "GitHub stars", "kilo", None),
        ("issues_total", "Issues created since launch", "int", None),
        ("last90_opened_per_day", "New issues per day (last 90 days)", "dec1", None),
        ("last90_closed_per_day", "Issues closed per day (last 90 days)", "dec1", None),
    ]),
    ("Backlog", [
        ("issues_open", "Open issues today", "int", "low"),
        ("pct_open", "Share of all issues still open", "pct", "low"),
        ("last90_net", "90-day net (new minus closed)", "signed", "low"),
        ("open_median_age_days", "Median age of open issues, days", "dec0", "low"),
        ("pct_open_older_90d", "Open issues older than 90 days", "pct", "low"),
        ("pct_open_zero_comments", "Open issues with no comments", "pct", "low"),
    ]),
    ("How issues are closed", [
        ("pct_completed_of_closed", "Closes marked completed", "pct", "high"),
        ("pct_not_planned_of_closed", "Closes marked not planned", "pct", None),
        ("pct_duplicate_of_closed", "Closes marked duplicate", "pct", None),
        ("pct_auto_closed_of_closed", "Closes carrying an automation label", "pct", None),
    ]),
    ("Speed", [
        ("median_ttc_days", "Median days to close", "dec1", "low"),
        ("median_ttc_completed_days", "Median days to a completed close", "dec1", "low"),
        ("pct_closed_within_7d", "Issues closed within 7 days", "pct", "high"),
    ]),
    ("Bugs", [
        ("pct_bugs", "Share of issues that are bugs", "pct", None),
        ("bugs_open", "Open bugs", "int", "low"),
        ("bugs_pct_completed", "Bugs closed as completed", "pct", "high"),
        ("bugs_median_ttc_completed_days", "Median days to fix a bug", "dec1", "low"),
    ]),
    ("Fixes, PRs and releases", [
        ("fixes_last3m_per_month", "Fixes per month (last 3 full months)*", "dec0", "high"),
        ("last90_prs_opened", "PRs opened (last 90 days)", "int", None),
        ("last90_prs_merged", "PRs merged (last 90 days)", "int", None),
        ("prs_open", "Open PRs today", "int", "low"),
        ("pct_closed_prs_merged", "Closed PRs that were merged", "pct", None),
        ("prs_merged", "PRs merged since launch", "int", None),
        ("pct_merged_prs_external", "Merged PRs from outside the team", "pct", None),
        ("median_ext_pr_hours_to_merge", "Median hours to merge an outside PR", "dec1", "low"),
        ("pct_completed_linked_to_pr", "Completed issues closed by a linked PR", "pct", "high"),
        ("versions_stable_last3m", "Stable versions in the last 3 full months", "int", None),
    ]),
]


USAGE = [
    ("Audience", [
        ("stars", "GitHub stars", "compact", None),
        ("npm_daily_30d", "npm downloads on a typical day (last 30 days)", "compact", "high"),
        ("npm_change_30d", "npm downloads, change vs the 30 days before", "pctsigned", "high"),
    ]),
    ("Per 1,000 stars", [
        ("last90_opened_per_1k_stars", "New issues (last 90 days)", "dec0", None),
        ("open_issues_per_1k_stars", "Open issues today", "dec0", "low"),
        ("issues_per_1k_stars", "Issues since launch", "dec0", None),
        ("outside_prs_per_1k_stars", "PRs from outside the team since launch", "dec1", "high"),
    ]),
    ("Per npm download", [
        ("last90_opened_per_100k_downloads", "New issues per 100k downloads (last 90 days)", "dec1", None),
    ]),
]


def scorecard_rows(groups=SCORECARD):
    out = []
    for group, rows in groups:
        out.append(f'<tr class="hx-group"><th colspan="4" scope="colgroup">{html.escape(group)}</th></tr>')
        for field, label, fmt, better in rows:
            cells = "".join(
                f'<td data-source="summary" data-field="{field}" data-where="repo={r}">—</td>' for r in REPOS
            )
            attrs = f'data-metric="{field}" data-fmt="{fmt}"' + (f' data-better="{better}"' if better else "")
            out.append(f'<tr {attrs}><th scope="row">{html.escape(label)}</th>{cells}</tr>')
    return "\n".join(out)


page = open(os.path.join(HERE, "page.html")).read()
page = page.replace("<!--SCORECARD-->", scorecard_rows()).replace("<!--USAGE-->", scorecard_rows(USAGE))
open(os.path.join(HERE, "index.html"), "w").write(page)
print("index.html", len(page.encode()), "bytes")
