#!/usr/bin/env python3
"""Executive report: one period of GitHub issue, PR, delivery and usage health, compared with the period before.

  report.py [--as-of ISO] [--days N | --month YYYY-MM] [--out PATH]

By default the period is the 30 days up to the last sync, compared with the 30 days before it.
--month reports one calendar month against the month before; a month still in progress is compared
with the same number of days of the month before. Writes a self-contained HTML file under reports/.
"""
import argparse
import html
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

from classify import (fixes, is_auto_closed, is_external, load_team, npm_daily, npm_typical, pr_policy, prepare,
                      releases, stars_at, stars_now)
from common import ROOT, data_as_of, iso, now, ts
from config import REPOS

KEYS = list(REPOS)
GOOD, BAD = "#0ca30c", "#d03b3b"
INK, INK2, INK3, RULE, SURFACE, PANEL = "#16181d", "#4a4d55", "#767a83", "#e3e4e8", "#ffffff", "#f6f6f8"


# ---------- period metrics ----------

def days_between(a, b):
    return (b - a).total_seconds() / 86400


def open_at(issues, t, bugs_only=False):
    return sum(1 for n in issues if n["_c"] < t and (not n["_x"] or n["_x"] >= t) and (n["_bug"] or not bugs_only))


def period_stats(issues, prs, team, fix_times, versions, npm, key, s, e):
    opened = [n for n in issues if s <= n["_c"] < e]
    closed = [n for n in issues if n["_x"] and s <= n["_x"] < e]
    reasons = Counter(n["_reason"] for n in closed)
    completed = [n for n in closed if n["_reason"] == "COMPLETED"]
    # Issues old enough to have had a full first week inside the data.
    cohort = [n for n in issues if s - timedelta(days=7) <= n["_c"] < e - timedelta(days=7)]
    bugs_opened = [n for n in opened if n["_bug"]]
    bugs_fixed = [n for n in completed if n["_bug"]]
    merged = [p for p in prs if p["_m"] and s <= p["_m"] < e]
    external = [p for p in merged if is_external(p, team)]
    pr_opened = [p for p in prs if s <= p["_c"] < e]
    pr_unmerged = [p for p in prs if p["_x"] and not p["_m"] and s <= p["_x"] < e]
    ext_opened = [p for p in pr_opened if is_external(p, team)]
    ext_unmerged = [p for p in pr_unmerged if is_external(p, team)]
    npm_day = npm_typical(npm, s, e)
    stars = stars_now(key)
    stars_s, stars_e = stars_at(key, s), stars_at(key, e)
    backlog_start, backlog_end = open_at(issues, s), open_at(issues, e)
    span = days_between(s, e)

    def ratio(a, b, scale=1):
        return a / b * scale if b else None

    return {
        "opened": len(opened),
        "opened_per_day": len(opened) / span,
        "closed": len(closed),
        "completed": len(completed),
        "closed_other": len(closed) - len(completed),
        "not_planned": reasons.get("NOT_PLANNED", 0),
        "duplicate": reasons.get("DUPLICATE", 0),
        "net": len(opened) - len(closed),
        "backlog_start": backlog_start,
        "backlog_end": backlog_end,
        "backlog_growth": ratio(backlog_end - backlog_start, backlog_start),
        "closed_per_new": ratio(len(closed), len(opened)),
        "resolved_per_100": ratio(len(completed), len(opened), 100),
        "pct_completed": ratio(len(completed), len(closed)),
        "pct_auto": ratio(sum(1 for n in closed if is_auto_closed(n)), len(closed)),
        "first_week": ratio(sum(1 for n in cohort if n["_x"] and days_between(n["_c"], n["_x"]) <= 7), len(cohort)),
        "days_to_resolve": median([days_between(n["_c"], n["_x"]) for n in completed]) if completed else None,
        "bugs_opened": len(bugs_opened),
        "bugs_fixed": len(bugs_fixed),
        "bugs_fixed_per_100": ratio(len(bugs_fixed), len(bugs_opened), 100),
        "bug_backlog_end": open_at(issues, e, bugs_only=True),
        "fixes": sum(1 for t in fix_times if s <= t < e),
        "prs_merged": len(merged),
        "prs_external": len(external),
        "pct_external": ratio(len(external), len(merged)),
        "ext_merge_hours": median([days_between(p["_c"], p["_m"]) * 24 for p in external]) if external else None,
        "versions_stable": sum(1 for t, pre in versions if s <= t < e and not pre),
        "versions_pre": sum(1 for t, pre in versions if s <= t < e and pre),
        "prs_opened": len(pr_opened),
        "prs_closed_unmerged": len(pr_unmerged),
        "prs_closed": len(merged) + len(pr_unmerged),
        "pr_merge_rate": ratio(len(merged), len(merged) + len(pr_unmerged)),
        "prs_open_start": open_at(prs, s),
        "prs_open_end": open_at(prs, e),
        "merge_hours": median([days_between(p["_c"], p["_m"]) * 24 for p in merged]) if merged else None,
        "ext_opened": len(ext_opened),
        "ext_merge_rate": ratio(len(external), len(external) + len(ext_unmerged)),
        # Usage. Stars are today's count unless a run recorded them near the period's edges.
        "npm_day": npm_day,
        "stars": stars,
        "stars_gained": stars_e - stars_s if stars_s is not None and stars_e is not None else None,
        "issues_per_1k_stars": ratio(len(opened), stars, 1000),
        "issues_per_100k_dl": ratio(len(opened), npm_day * span if npm_day else None, 100000),
        "ext_per_1k_stars": ratio(len(ext_opened), stars, 1000),
    }


def rolling_npm(npm, start, end, window=7):
    """Median of the last `window` reported days, for each reported day in [start, end)."""
    days = sorted(d for d in npm if start.date() <= d < end.date())
    out = []
    for d in days:
        vals = sorted(npm[x] for x in npm if d - timedelta(days=window) < x <= d)
        out.append((datetime(d.year, d.month, d.day, tzinfo=timezone.utc), vals[len(vals) // 2]))
    return out


def daily_backlog(issues, start, end):
    events = sorted([(n["_c"], 1) for n in issues] + [(n["_x"], -1) for n in issues if n["_x"]])
    out, i, level = [], 0, 0
    day = start
    while day <= end:
        while i < len(events) and events[i][0] < day:
            level += events[i][1]
            i += 1
        out.append((day, level))
        day = min(day + timedelta(days=1), end) if day < end else end + timedelta(days=1)
    return out


# ---------- formatting ----------

def esc(s):
    return html.escape(str(s))


def fnum(v, digits=0):
    if v is None:
        return "–"
    return f"{v:,.{digits}f}"


def fpct(v, digits=0):
    return "–" if v is None else f"{v * 100:.{digits}f}%".replace("-", "−")


def fsigned(v):
    return "–" if v is None else ("+" if v > 0 else "−" if v < 0 else "±") + f"{abs(v):,.0f}"


def fdate(d):
    return f"{d.day} {d.strftime('%b %Y')}"


def fday(d):
    return f"{d.day} {d.strftime('%b')}"


def delta_html(cur, prev, better, kind="pct"):
    """Change against the previous period: arrow colored by whether it is good, with the word too."""
    if cur is None or prev is None:
        return '<span class="d">–</span>'
    if kind == "pts":
        diff = (cur - prev) * 100
        text = f"{abs(diff):.0f} pts"
        if round(abs(diff)) == 0:
            return '<span class="d">no change</span>'
    else:
        if prev == 0:
            return '<span class="d">new</span>' if cur else '<span class="d">no change</span>'
        diff = (cur - prev) / abs(prev)
        # Past doubling a multiple reads better than a four-digit percentage.
        text = f"{cur / prev:.1f}×" if diff >= 1 and prev > 0 else f"{abs(diff) * 100:.0f}%"
        if round(abs(diff) * 100) == 0:
            return '<span class="d">no change</span>'
    up = diff > 0
    arrow = "▲" if up else "▼"
    if better is None:
        return f'<span class="d"><span class="arr">{arrow}</span> {text}</span>'
    good = up == (better == "up")
    color, word = (GOOD, "better") if good else (BAD, "worse")
    return f'<span class="d"><span class="arr" style="color:{color}">{arrow}</span> {text} <span class="w">{word}</span></span>'


def nice_ticks(lo, hi, count=5):
    span = hi - lo or 1
    raw = span / count
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = int(lo // step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(t)
        t += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def short(v):
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.1f}B".replace(".0B", "B")
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.1f}M".replace(".0M", "M")
    return f"{v / 1000:,.0f}k" if abs(v) >= 10000 else f"{v / 1000:,.1f}k" if abs(v) >= 1000 else f"{v:,.0f}"


def fcompact(v):
    if v is None:
        return "–"
    return f"{v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"{v / 1000:,.0f}k" if abs(v) >= 10000 else f"{v:,.0f}"


# ---------- charts (inline SVG) ----------

def chart_backlog(series, window_start, end, what="open", vfmt=lambda v: f"{v:,}"):
    W, H, ml, mr, mt, mb = 960, 300, 52, 170, 16, 30
    series = {k: v for k, v in series.items() if v}
    x0, x1 = min(v[0][0] for v in series.values()), max(end, max(v[-1][0] for v in series.values()))
    vmax = max(v for s in series.values() for _, v in s)
    ticks = nice_ticks(0, vmax)
    ymax = ticks[-1]
    X = lambda d: ml + (W - ml - mr) * days_between(x0, d) / max(1, days_between(x0, x1))
    Y = lambda v: mt + (H - mt - mb) * (1 - v / ymax)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{esc(what)} per day">']
    out.append(f'<rect x="{X(window_start):.1f}" y="{mt}" width="{X(end) - X(window_start):.1f}" height="{H - mt - mb}" fill="{PANEL}"/>')
    out.append(f'<text x="{X(window_start) + 6:.1f}" y="{mt + 14}" class="ax">This period</text>')
    for t in ticks:
        out.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{RULE}"/>')
        out.append(f'<text x="{ml - 8}" y="{Y(t):.1f}" dy="0.32em" text-anchor="end" class="ax">{short(t)}</text>')
    d = x0
    while d <= x1:
        if d.day in (1, 15):
            out.append(f'<text x="{X(d):.1f}" y="{H - 8}" text-anchor="middle" class="ax">{fday(d)}</text>')
        d += timedelta(days=1)
    labels = []
    for k in series:
        pts = series[k]
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(t):.1f},{Y(v):.1f}" for i, (t, v) in enumerate(pts))
        color = REPOS[k]["color"]
        out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        for t, v in pts:  # hover targets
            out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="5" fill="transparent"><title>{esc(REPOS[k]["name"])} · {fday(t)}: {vfmt(v)} {esc(what)}</title></circle>')
        t, v = pts[-1]
        out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="4" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>')
        labels.append([Y(v), k, v])
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 18)
    for y, k, v in labels:
        out.append(f'<text x="{W - mr + 12}" y="{y:.1f}" dy="0.32em" class="lab"><tspan fill="{REPOS[k]["color"]}">●</tspan> {esc(REPOS[k]["name"])} <tspan class="labv">{vfmt(v)}</tspan></text>')
    out.append("</svg>")
    return "".join(out)


ISSUE_FLOW = {"new": "opened", "closed": "closed", "good": "completed", "other": "closed_other", "rate": "pct_completed",
              "noun": "issues", "good_title": "Resolved (closed as completed)",
              "other_title": "Closed without a fix (not planned or duplicate)", "rate_word": "resolved"}
PR_FLOW = {"new": "prs_opened", "closed": "prs_closed", "good": "prs_merged", "other": "prs_closed_unmerged", "rate": "pr_merge_rate",
           "noun": "PRs", "good_title": "Merged", "other_title": "Closed without merging", "rate_word": "merged"}


def chart_flow(cur, f=ISSUE_FLOW):
    """Per repo: new items, and closed items split into the good outcome and the rest."""
    W, row, gap, ml, mr = 960, 26, 34, 120, 70
    H = len(KEYS) * (2 * row + 6 + gap) - gap + 30
    vmax = max(max(cur[k][f["new"]], cur[k][f["closed"]]) for k in KEYS) or 1
    X = lambda v: (W - ml - mr) * v / vmax
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="New and closed {f["noun"]} this period">']
    y = 4
    for k in KEYS:
        c, color = cur[k], REPOS[k]["color"]
        out.append(f'<text x="0" y="{y + row + 3}" dy="0.32em" class="lab">{esc(REPOS[k]["name"])}</text>')
        out.append(f'<text x="{ml - 10}" y="{y + row / 2}" dy="0.32em" text-anchor="end" class="ax">New</text>')
        out.append(f'<rect x="{ml}" y="{y}" width="{max(1, X(c[f["new"]])):.1f}" height="{row}" rx="4" fill="{INK3}"><title>{esc(REPOS[k]["name"])} new {f["noun"]}: {c[f["new"]]:,}</title></rect>')
        out.append(f'<text x="{ml + X(c[f["new"]]) + 8:.1f}" y="{y + row / 2}" dy="0.32em" class="labv">{c[f["new"]]:,}</text>')
        y2 = y + row + 6
        out.append(f'<text x="{ml - 10}" y="{y2 + row / 2}" dy="0.32em" text-anchor="end" class="ax">Closed</text>')
        wr, wo = X(c[f["good"]]), X(c[f["other"]])
        out.append(f'<rect x="{ml}" y="{y2}" width="{max(0, wr - (2 if wo > 0 else 0)):.1f}" height="{row}" rx="4" fill="{color}"><title>{f["good_title"]}: {c[f["good"]]:,}</title></rect>')
        out.append(f'<rect x="{ml + wr:.1f}" y="{y2}" width="{wo:.1f}" height="{row}" rx="4" fill="{color}" fill-opacity="0.32"><title>{f["other_title"]}: {c[f["other"]]:,}</title></rect>')
        out.append(f'<text x="{ml + wr + wo + 8:.1f}" y="{y2 + row / 2}" dy="0.32em" class="labv">{c[f["closed"]]:,}'
                   f' <tspan class="ax">({fpct(c[f["rate"]])} {f["rate_word"]})</tspan></text>')
        y += 2 * row + 6 + gap
    out.append("</svg>")
    return "".join(out)


def chart_fixes(cur, prev):
    W, H, ml, mt, mb = 960, 230, 52, 16, 40
    vmax = max(max(cur[k]["fixes"], prev[k]["fixes"]) for k in KEYS) or 1
    ticks = nice_ticks(0, vmax, 4)
    ymax = ticks[-1]
    Y = lambda v: mt + (H - mt - mb) * (1 - v / ymax)
    band = (W - ml) / len(KEYS)
    bw = min(56, band / 4)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Fixes shipped, this period and the previous one">']
    for t in ticks:
        out.append(f'<line x1="{ml}" x2="{W}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{RULE}"/>')
        out.append(f'<text x="{ml - 8}" y="{Y(t):.1f}" dy="0.32em" text-anchor="end" class="ax">{short(t)}</text>')
    for i, k in enumerate(KEYS):
        cx, color = ml + band * (i + 0.5), REPOS[k]["color"]
        for j, (v, label) in enumerate([(prev[k]["fixes"], "Previous"), (cur[k]["fixes"], "This period")]):
            x = cx - bw - 3 + j * (bw + 6)
            h = Y(0) - Y(v)
            style = f'fill="{color}"' if j else f'fill="{SURFACE}" stroke="{color}" stroke-width="1.5"'
            out.append(f'<rect x="{x:.1f}" y="{Y(v):.1f}" width="{bw:.1f}" height="{max(0, h):.1f}" rx="4" {style}><title>{esc(REPOS[k]["name"])} · {label}: {v:,}</title></rect>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{Y(v) - 6:.1f}" text-anchor="middle" class="labv">{v:,}</text>')
        out.append(f'<text x="{cx:.1f}" y="{H - 18}" text-anchor="middle" class="lab">{esc(REPOS[k]["name"])}</text>')
        method = "CHANGELOG “Fixed” entries" if REPOS[k]["fix_source"] == "changelog" else "merged PRs titled “fix”"
        out.append(f'<text x="{cx:.1f}" y="{H - 3}" text-anchor="middle" class="ax">{method}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------- narrative ----------

def leader(cur, metric, high=True):
    vals = {k: cur[k][metric] for k in KEYS if cur[k][metric] is not None}
    if not vals:
        return None, vals
    pick = max(vals, key=vals.get) if high else min(vals, key=vals.get)
    return pick, vals


def name(k):
    return REPOS[k]["name"]


def bottom_line(cur):
    order = sorted(KEYS, key=lambda k: cur[k]["backlog_growth"] if cur[k]["backlog_growth"] is not None else 0)
    best, worst = order[0], order[-1]
    b, w = cur[best], cur[worst]
    if b["backlog_growth"] is not None and b["backlog_growth"] < 0:
        first = f"{name(best)} is the only one shrinking its open backlog ({fpct(b['backlog_growth'])} to {b['backlog_end']:,})" \
            if sum(1 for k in KEYS if (cur[k]["backlog_growth"] or 0) < 0) == 1 else \
            f"{name(best)} cut its open backlog the most ({fpct(b['backlog_growth'])} to {b['backlog_end']:,})"
    else:
        first = f"{name(best)} kept backlog growth lowest ({fpct(b['backlog_growth'])}, now {b['backlog_end']:,} open)"
    second = f"{name(worst)}'s backlog grew {fpct(w['backlog_growth'])} to {w['backlog_end']:,} open issues, " \
             f"closing {fnum(w['closed_per_new'], 2)} issues for every new one."
    return f"{first}. {second}"


def takeaways(cur, prev):
    out = []
    k, vals = leader(cur, "opened_per_day")
    out.append(f"<b>Demand.</b> {name(k)} takes in the most issues, {fnum(vals[k], 0)} a day; "
               + ", ".join(f"{name(o)} {fnum(cur[o]['opened_per_day'], 0)}" for o in KEYS if o != k) + ".")
    k, vals = leader(cur, "resolved_per_100")
    lo = min(vals, key=vals.get)
    out.append(f"<b>Resolution.</b> For every 100 new issues, {name(k)} resolved {fnum(vals[k])} as completed; "
               f"{name(lo)} resolved {fnum(vals[lo])}. "
               + " ".join(f"{fpct(cur[o]['pct_auto'])} of {name(o)}'s closes were stale or auto-close sweeps."
                          for o in KEYS if (cur[o]["pct_auto"] or 0) >= 0.25))
    k, vals = leader(cur, "first_week")
    lo = min(vals, key=vals.get)
    out.append(f"<b>Speed.</b> {name(k)} closed {fpct(vals[k])} of new issues within a week, {name(lo)} {fpct(vals[lo])}.")
    for o in KEYS:
        c, p = cur[o], prev[o]
        # Closing far more than arrives while resolve times jump means old issues are being cleared.
        if (c["closed_per_new"] or 0) >= 1.2 and c["days_to_resolve"] and p["days_to_resolve"] and c["days_to_resolve"] >= 3 * p["days_to_resolve"]:
            out.append(f"<b>Clean-up.</b> {name(o)} closed {c['closed']:,} issues against {c['opened']:,} new, many of them old: "
                       f"the median completed issue was {fnum(c['days_to_resolve'], 0)} days old when closed, up from {fnum(p['days_to_resolve'], 1)}. "
                       f"Its slower resolve time this period reflects backlog clearing, not slower work.")
    k, vals = leader(cur, "fixes")
    parts = []
    for o in KEYS:
        p = prev[o]["fixes"]
        change = f" ({'+' if cur[o]['fixes'] >= p else '−'}{abs(cur[o]['fixes'] - p) / p * 100:.0f}% vs previous)" if p else ""
        parts.append(f"{name(o)} {cur[o]['fixes']:,}{change}")
    out.append(f"<b>Delivery.</b> Fixes shipped: " + "; ".join(parts) + ". Counts use different sources per repo (see notes).")
    order = sorted(KEYS, key=lambda o: -cur[o]["prs_merged"])
    out.append("<b>Pull requests.</b> Merged: " + "; ".join(
        f"{name(o)} {cur[o]['prs_merged']:,} of {cur[o]['prs_closed']:,} closed ({fpct(cur[o]['pr_merge_rate'])})" for o in order)
        + ". Open at period end: " + ", ".join(f"{name(o)} {cur[o]['prs_open_end']:,}" for o in order) + ".")
    k, vals = leader(cur, "pct_external")
    out.append(f"<b>Community.</b> {fpct(vals[k])} of {name(k)}'s merged PRs came from outside the team; "
               + ", ".join(f"{name(o)} {fpct(cur[o]['pct_external'])}" for o in KEYS if o != k) + ". Outside PRs opened: "
               + ", ".join(f"{name(o)} {cur[o]['ext_opened']:,}" for o in KEYS) + "."
               + "".join(f" {name(o)} only accepts PRs from collaborators (a repository setting)." for o in KEYS if pr_policy(o) == "collaborators_only"))
    if any(cur[o]["npm_day"] for o in KEYS):
        order = sorted((o for o in KEYS if cur[o]["npm_day"]), key=lambda o: -cur[o]["npm_day"])
        def change(o):
            p = prev[o]["npm_day"]
            return f" ({'+' if cur[o]['npm_day'] >= p else '−'}{abs(cur[o]['npm_day'] - p) / p * 100:.0f}%)" if p else ""
        hi, lo = leader(cur, "issues_per_1k_stars")[0], leader(cur, "issues_per_1k_stars", high=False)[0]
        out.append("<b>Usage.</b> npm downloads on a typical day: " + "; ".join(f"{name(o)} {fcompact(cur[o]['npm_day'])}{change(o)}" for o in order)
                   + f". Relative to audience, {name(hi)} drew {fnum(cur[hi]['issues_per_1k_stars'])} new issues per 1,000 GitHub stars "
                   f"and {name(lo)} {fnum(cur[lo]['issues_per_1k_stars'])}.")
    return out


AREAS = [
    ("Backlog control", "backlog_growth", False, "Change in open backlog over the period", lambda v: fpct(v, 1)),
    ("Throughput", "closed_per_new", True, "Issues closed per new issue", lambda v: fnum(v, 2)),
    ("Resolution", "resolved_per_100", True, "Issues resolved as completed per 100 new", lambda v: fnum(v)),
    ("Speed", "first_week", True, "New issues closed within 7 days", fpct),
    ("Bug fixing", "bugs_fixed_per_100", True, "Bugs fixed per 100 new bug reports", lambda v: fnum(v)),
    ("Community", "pct_external", True, "Merged PRs from outside the team", fpct),
    ("Outside PR speed", "ext_merge_hours", False, "Median hours to merge an outside PR", lambda v: fnum(v, 1) + " h"),
    ("Usage", "npm_day", True, "npm downloads on a typical day", fcompact),
    ("Audience", "stars", True, "GitHub stars today", fcompact),
]


def watch_items(cur, prev):
    items = []
    for k in KEYS:
        c, p = cur[k], prev[k]
        if (c["backlog_growth"] or 0) > 0.10:
            items.append((k, f"Open backlog up {fpct(c['backlog_growth'])} in the period, to {c['backlog_end']:,}."))
        if c["pct_completed"] is not None and c["pct_completed"] < 0.25:
            items.append((k, f"Only {fpct(c['pct_completed'])} of closed issues were resolved as completed."))
        if (c["pct_auto"] or 0) >= 0.30:
            items.append((k, f"{fpct(c['pct_auto'])} of closes came from stale or auto-close automation."))
        if c["first_week"] is not None and p["first_week"] is not None and c["first_week"] - p["first_week"] <= -0.05:
            items.append((k, f"First-week close rate fell {abs(c['first_week'] - p['first_week']) * 100:.0f} pts to {fpct(c['first_week'])}."))
        if c["pct_external"] is not None and p["pct_external"] is not None and c["pct_external"] - p["pct_external"] <= -0.05:
            items.append((k, f"Community share of merged PRs fell {abs(c['pct_external'] - p['pct_external']) * 100:.0f} pts to {fpct(c['pct_external'])}."))
        if c["prs_open_start"] and (c["prs_open_end"] - c["prs_open_start"]) / c["prs_open_start"] > 0.20:
            items.append((k, f"Open PRs up {(c['prs_open_end'] - c['prs_open_start']) / c['prs_open_start'] * 100:.0f}% in the period, to {c['prs_open_end']:,}."))
        if c["npm_day"] and p["npm_day"] and c["npm_day"] / p["npm_day"] - 1 <= -0.15:
            items.append((k, f"npm downloads on a typical day fell {(1 - c['npm_day'] / p['npm_day']) * 100:.0f}% to {fcompact(c['npm_day'])}."))
        if c["bug_backlog_end"] and c["bugs_opened"] and (c["bugs_fixed_per_100"] or 0) < 25:
            items.append((k, f"{c['bugs_fixed']:,} bugs fixed against {c['bugs_opened']:,} reported; {c['bug_backlog_end']:,} open."))
    return items


# ---------- page ----------

SCORECARD = [
    ("Issue flow", [
        ("New issues", "opened", fnum, None, "pct"),
        ("Closed issues", "closed", fnum, "up", "pct"),
        ("Closed per new issue", "closed_per_new", lambda v: fnum(v, 2), "up", "pct"),
        ("Open backlog at period end", "backlog_end", fnum, "down", "pct"),
        ("Backlog change in the period", "net", fsigned, "down", None),
    ]),
    ("Resolution quality", [
        ("Resolved (completed) per 100 new", "resolved_per_100", fnum, "up", "pct"),
        ("Closes that were completed", "pct_completed", fpct, "up", "pts"),
        ("Closes by stale or auto-close automation", "pct_auto", fpct, None, "pts"),
        ("Median days to a completed close", "days_to_resolve", lambda v: fnum(v, 1), "down", "pct"),
        ("New issues closed within 7 days", "first_week", fpct, "up", "pts"),
    ]),
    ("Bugs", [
        ("Bug reports", "bugs_opened", fnum, None, "pct"),
        ("Bugs fixed (closed as completed)", "bugs_fixed", fnum, "up", "pct"),
        ("Open bugs at period end", "bug_backlog_end", fnum, "down", "pct"),
    ]),
    ("Delivery", [
        ("Fixes shipped*", "fixes", fnum, "up", "pct"),
        ("Stable versions released", "versions_stable", fnum, None, "pct"),
    ]),
    ("Pull requests", [
        ("PRs opened", "prs_opened", fnum, None, "pct"),
        ("PRs merged", "prs_merged", fnum, None, "pct"),
        ("PRs closed without merging", "prs_closed_unmerged", fnum, None, "pct"),
        ("Closed PRs that were merged", "pr_merge_rate", fpct, None, "pts"),
        ("Open PRs at period end", "prs_open_end", fnum, "down", "pct"),
        ("Median hours to merge a PR", "merge_hours", lambda v: fnum(v, 1), "down", "pct"),
    ]),
    ("Community", [
        ("PRs opened from outside the team", "ext_opened", fnum, "up", "pct"),
        ("Merged PRs from outside the team", "pct_external", fpct, "up", "pts"),
        ("Closed outside PRs that were merged", "ext_merge_rate", fpct, "up", "pts"),
        ("Median hours to merge an outside PR", "ext_merge_hours", lambda v: fnum(v, 1), "down", "pct"),
    ]),
    ("Usage and audience", [
        ("npm downloads on a typical day", "npm_day", fcompact, "up", "pct"),
        ("GitHub stars (today)", "stars", fcompact, None, None),
        ("Stars gained in the period", "stars_gained", fsigned, "up", "pct"),
        ("New issues per 1,000 stars", "issues_per_1k_stars", lambda v: fnum(v, 1), None, "pct"),
        ("New issues per 100k npm downloads", "issues_per_100k_dl", lambda v: fnum(v, 1), None, "pct"),
        ("Outside PRs opened per 1,000 stars", "ext_per_1k_stars", lambda v: fnum(v, 2), "up", "pct"),
    ]),
]

CSS = f"""
:root {{ color-scheme: light; --ink:{INK}; --ink2:{INK2}; --ink3:{INK3}; --rule:{RULE}; --panel:{PANEL}; --surface:{SURFACE}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--ink); font: 15px/1.55 "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif; }}
.wrap {{ max-width: 1040px; margin: 0 auto; padding-block: 40px 56px; padding-left: max(20px, env(safe-area-inset-left, 0px)); padding-right: max(20px, env(safe-area-inset-right, 0px)); }}
.eyebrow {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink3); margin: 0; }}
h1 {{ font-family: "Source Serif 4", Georgia, "Times New Roman", serif; font-weight: 600; font-size: clamp(28px, 4vw, 40px); line-height: 1.15; margin: 6px 0 4px; text-wrap: balance; }}
.period {{ color: var(--ink2); margin: 0; }}
h2 {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 600; font-size: 22px; margin: 44px 0 6px; }}
.sub {{ color: var(--ink2); margin: 0 0 16px; max-width: 72ch; }}
.bottom {{ margin: 28px 0 0; padding: 20px 24px; background: var(--panel); border-radius: 10px; font-family: "Source Serif 4", Georgia, serif; font-size: 20px; line-height: 1.45; }}
.bottom b {{ font-family: "IBM Plex Sans", system-ui, sans-serif; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink3); display: block; margin-bottom: 6px; font-weight: 500; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 290px), 1fr)); gap: 16px; margin-top: 24px; }}
.card {{ border: 1px solid var(--rule); border-radius: 10px; padding: 18px 20px; min-width: 0; }}
.card h3 {{ margin: 0 0 12px; font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }}
.sw {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; flex: none; }}
.kpis {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; }}
.kpi .v {{ font-family: "Source Serif 4", Georgia, serif; font-size: 28px; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.kpi .k {{ font-size: 12.5px; color: var(--ink2); }}
.d {{ font-size: 12.5px; color: var(--ink2); white-space: nowrap; font-variant-numeric: tabular-nums; }}
.d .w {{ color: var(--ink3); }}
ul.take {{ padding-left: 18px; margin: 0; display: grid; gap: 8px; max-width: 80ch; }}
.fig {{ border: 1px solid var(--rule); border-radius: 10px; padding: 18px 20px 12px; margin-top: 16px; }}
.fig h3 {{ margin: 0; font-size: 16px; font-weight: 600; }}
.fig p {{ margin: 2px 0 10px; color: var(--ink2); font-size: 13.5px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink2); margin-bottom: 6px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
svg {{ display: block; overflow: visible; }}
svg text {{ font-family: "IBM Plex Sans", system-ui, sans-serif; font-variant-numeric: tabular-nums; }}
svg .ax {{ font-size: 12px; fill: var(--ink3); }}
svg .lab {{ font-size: 13px; fill: var(--ink); font-weight: 500; }}
svg .labv {{ font-size: 13px; fill: var(--ink2); }}
.tbl {{ overflow-x: auto; margin-top: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; min-width: 640px; }}
th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--rule); text-align: right; vertical-align: top; }}
th:first-child, td:first-child {{ text-align: left; }}
thead th {{ font-size: 13px; font-weight: 600; color: var(--ink); }}
tr.grp td {{ font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink3); padding-top: 18px; border-bottom-color: var(--ink3); }}
td .val {{ display: block; font-size: 15px; }}
td.lead .val {{ font-weight: 600; }}
.areas {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 12px; margin-top: 12px; }}
.area {{ border: 1px solid var(--rule); border-radius: 10px; padding: 14px 16px; }}
.area .t {{ font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink3); }}
.area .who {{ font-size: 18px; font-weight: 600; display: flex; align-items: center; gap: 8px; margin: 2px 0; }}
.area .ev {{ font-size: 13px; color: var(--ink2); }}
.watch {{ list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 8px; }}
.watch li {{ display: flex; gap: 10px; align-items: baseline; padding: 10px 14px; border: 1px solid var(--rule); border-left: 3px solid {BAD}; border-radius: 6px; }}
.watch .who {{ font-weight: 600; white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; }}
.notes {{ margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--rule); color: var(--ink2); font-size: 13px; }}
.notes ul {{ padding-left: 18px; margin: 6px 0 0; display: grid; gap: 4px; }}
@media print {{ .wrap {{ padding: 0; }} .fig, .card, .area, .bottom {{ break-inside: avoid; }} h2 {{ break-after: avoid; }} }}
"""


def render(cur, prev, backlog, pr_backlog, npm_series, s, e, ps, pe, as_of, label):
    sw = lambda k: f'<span class="sw" style="background:{REPOS[k]["color"]}"></span>'
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Agent Harness GitHub Report · {esc(label)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>{CSS}</style></head><body><main class="wrap">
<p class="eyebrow">GitHub health report · AI coding agent harnesses</p>
<h1>{" vs ".join(esc(name(k)) for k in KEYS)}</h1>
<p class="period">{esc(label)}: {fdate(s)} – {fdate(e - timedelta(seconds=1))}, compared with {fdate(ps)} – {fdate(pe - timedelta(seconds=1))}.
Data as of {fdate(as_of)}, {as_of.strftime('%H:%M')} UTC.</p>
<p class="bottom"><b>Bottom line</b>{esc(bottom_line(cur))}</p>
<div class="cards">"""]
    for k in KEYS:
        c, p = cur[k], prev[k]
        parts.append(f"""<section class="card"><h3>{sw(k)}{esc(name(k))}</h3><div class="kpis">
<div class="kpi"><div class="v">{fnum(c['opened'])}</div><div class="k">new issues</div>{delta_html(c['opened'], p['opened'], None)}</div>
<div class="kpi"><div class="v">{fnum(c['closed'])}</div><div class="k">closed issues</div>{delta_html(c['closed'], p['closed'], 'up')}</div>
<div class="kpi"><div class="v">{fnum(c['backlog_end'])}</div><div class="k">open at period end</div>{delta_html(c['backlog_end'], p['backlog_end'], 'down')}</div>
<div class="kpi"><div class="v">{fnum(c['resolved_per_100'])}</div><div class="k">resolved per 100 new</div>{delta_html(c['resolved_per_100'], p['resolved_per_100'], 'up')}</div>
</div></section>""")
    parts.append("</div>")

    parts.append('<h2>What changed</h2><ul class="take">' + "".join(f"<li>{t}</li>" for t in takeaways(cur, prev)) + "</ul>")

    legend = '<div class="legend">' + "".join(f'<span>{sw(k)}{esc(name(k))}</span>' for k in KEYS) + "</div>"
    parts.append(f"""<h2>Trend and flow</h2>
<div class="fig"><h3>Open issues, day by day</h3><p>Issues created and not yet closed on each day. The shaded band is the reporting period.</p>{legend}{chart_backlog(backlog, s, e)}</div>
<div class="fig"><h3>Issues in and out during the period</h3><p>Closed issues split into resolved (closed as completed, solid) and closed without a fix (not planned or duplicate, pale).</p>{chart_flow(cur)}</div>
<div class="fig"><h3>Fixes shipped</h3><p>Outlined: previous period. Solid: this period. Claude Code's source is closed, so its fixes come from its CHANGELOG.</p>{chart_fixes(cur, prev)}</div>""")

    parts.append(f"""<h2>Pull requests</h2><p class="sub">Every pull request, by anyone. Codex's PRs are mostly synced from its internal repository by one account, so they merge almost immediately and rarely come from outside.</p>
<div class="fig"><h3>PRs in and out during the period</h3><p>Closed PRs split into merged (solid) and closed without merging (pale).</p>{chart_flow(cur, PR_FLOW)}</div>
<div class="fig"><h3>Open PRs, day by day</h3><p>Pull requests created and not yet merged or closed on each day. The shaded band is the reporting period.</p>{legend}{chart_backlog(pr_backlog, s, e, "open PRs")}</div>""")

    stars_note = ("Stars gained are measured between runs of the pipeline close to each period edge."
                  if any(cur[k]["stars_gained"] is not None for k in KEYS) else
                  "GitHub gives only today's star count, not when each star was given; each run now records it, so stars gained per period appear once runs cover a full period.")
    parts.append(f"""<h2>Usage and audience</h2><p class="sub">Two imperfect signals of how widely each tool is used. GitHub stars measure developer attention. npm downloads count installs of each CLI's npm package: native installers, Homebrew and release binaries are missed, and CI pipelines count on every run. Trends within a repository are more reliable than levels across them. {esc(stars_note)}</p>
<div class="cards">""" + "".join(f"""<section class="card"><h3>{sw(k)}{esc(name(k))}</h3><div class="kpis">
<div class="kpi"><div class="v">{fcompact(cur[k]['npm_day'])}</div><div class="k">npm downloads on a typical day</div>{delta_html(cur[k]['npm_day'], prev[k]['npm_day'], 'up')}</div>
<div class="kpi"><div class="v">{fcompact(cur[k]['stars'])}</div><div class="k">GitHub stars today</div><span class="d">{"+" + format(cur[k]['stars_gained'], ",") + " in the period" if cur[k]['stars_gained'] is not None else "gains not yet measured"}</span></div>
<div class="kpi"><div class="v">{fnum(cur[k]['issues_per_1k_stars'], 1)}</div><div class="k">new issues per 1,000 stars</div>{delta_html(cur[k]['issues_per_1k_stars'], prev[k]['issues_per_1k_stars'], None)}</div>
<div class="kpi"><div class="v">{fnum(cur[k]['issues_per_100k_dl'], 1)}</div><div class="k">new issues per 100k downloads</div>{delta_html(cur[k]['issues_per_100k_dl'], prev[k]['issues_per_100k_dl'], None)}</div>
</div></section>""" for k in KEYS) + "</div>")
    if any(npm_series.values()):
        parts.append(f"""<div class="fig"><h3>npm downloads per day</h3><p>Median of the last 7 reported days, which smooths the weekly cycle and ignores npm's missing days and automated download storms. npm publishes each day with a lag of a day or two.</p>{legend}{chart_backlog(npm_series, s, e, "npm downloads", fcompact)}</div>""")

    parts.append('<h2>Who leads each area</h2><p class="sub">The best value among the three for this period.</p><div class="areas">')
    for title, metric, high, desc, f in AREAS:
        k, vals = leader(cur, metric, high)
        if not k:
            continue
        others = " · ".join(f"{esc(name(o))} {f(vals[o])}" for o in KEYS if o != k and o in vals)
        parts.append(f'<div class="area"><div class="t">{esc(title)}</div><div class="who">{sw(k)}{esc(name(k))} <span class="labv">{f(vals[k])}</span></div>'
                     f'<div class="ev">{esc(desc)}. {others}.</div></div>')
    parts.append("</div>")

    watch = watch_items(cur, prev)
    if watch:
        parts.append('<h2>Watch list</h2><p class="sub">Signals that crossed a threshold this period.</p><ul class="watch">')
        parts += [f'<li><span class="who">{sw(k)}{esc(name(k))}</span><span>{esc(t)}</span></li>' for k, t in watch]
        parts.append("</ul>")

    parts.append(f'<h2>Scorecard</h2><p class="sub">This period, with the change against the previous one. The leader in each row with a clear direction is in bold.</p>'
                 f'<div class="tbl"><table><thead><tr><th>Metric</th>' + "".join(f"<th>{sw(k)} {esc(name(k))}</th>" for k in KEYS) + "</tr></thead><tbody>")
    for group, rows in SCORECARD:
        parts.append(f'<tr class="grp"><td colspan="{len(KEYS) + 1}">{esc(group)}</td></tr>')
        for label_, metric, f, better, kind in rows:
            vals = {k: cur[k][metric] for k in KEYS if cur[k][metric] is not None}
            best = (max if better == "up" else min)(vals.values()) if better and vals else None
            cells = []
            for k in KEYS:
                v = cur[k][metric]
                lead = ' class="lead"' if best is not None and v == best else ""
                dlt = delta_html(v, prev[k][metric], better, kind) if kind else ""
                cells.append(f"<td{lead}><span class=\"val\">{f(v)}</span>{dlt}</td>")
            parts.append(f"<tr><td>{esc(label_)}</td>{''.join(cells)}</tr>")
    parts.append("</tbody></table></div>")

    parts.append(f"""<section class="notes"><b>How to read this</b><ul>
<li>Every issue and pull request of {", ".join(esc(REPOS[k]['repo']) for k in KEYS)} is read from the GitHub API with <code>gh</code>. An issue counts as open on a day if it was created before that day and not yet closed.</li>
<li><b>Resolved</b> means closed with GitHub's reason "completed". "Not planned" includes issues closed for inactivity; Claude Code and OpenCode run stale and auto-close automation, Codex does not.</li>
<li><b>Bugs</b>: bug label or [BUG] title in Claude Code and Codex. OpenCode does not label issue types, so its bugs are estimated from title wording.</li>
<li><b>*Fixes shipped</b>: Claude Code publishes no source, so its CHANGELOG "Fixed" entries are counted, dated by npm release. For Codex and OpenCode, merged PRs whose title starts with "fix". Codex PRs are synced from an internal repository with descriptive titles that rarely say "fix", so its count is a floor.</li>
<li><b>Outside the team</b>: PR authors who have never merged a PR and are not marked member, collaborator or owner; bots and Codex's sync account are excluded.{"".join(" " + esc(name(k)) + "'s repository only lets collaborators open PRs." for k in KEYS if pr_policy(k) == "collaborators_only")}</li>
<li><b>Versions</b>: GitHub releases for Codex and OpenCode; for Claude Code, versions published to npm (its GitHub releases start in Dec 2025).</li>
<li><b>Pull requests</b>: a PR is open on a day if it was created before that day and not yet merged or closed. "Closed PRs that were merged" is merged over merged plus closed without merging, in the period.</li>
<li><b>Usage</b>: npm downloads of {", ".join("<code>" + esc(REPOS[k]["downloads_package"]) + "</code>" for k in KEYS if REPOS[k].get("downloads_package"))}, on the median day of each period (days npm did not report are left out). Per-star ratios use today's star count.</li>
</ul></section></main></body></html>""")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="ISO time the data is read at (default: last sync of every repo)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=30, help="period length in days, ending at --as-of (default 30)")
    group.add_argument("--month", help="calendar month YYYY-MM instead of a rolling period")
    ap.add_argument("--out", help="output path (default reports/harness-report_<period>.html)")
    args = ap.parse_args()
    as_of = ts(args.as_of) if args.as_of else (data_as_of() or now())

    if args.month:
        s = datetime.strptime(args.month, "%Y-%m").replace(tzinfo=timezone.utc)
        nxt = (s + timedelta(days=32)).replace(day=1)
        e = min(nxt, as_of)
        prev_start = (s - timedelta(days=1)).replace(day=1)
        ps, pe = prev_start, (s if e == nxt else prev_start + (e - s))  # a month in progress is compared like for like
        label = s.strftime("%B %Y") + (" (month to date)" if e < nxt else "")
        slug = args.month + ("-to-date" if e < nxt else "")
    else:
        e = as_of
        s = e - timedelta(days=args.days)
        ps, pe = s - timedelta(days=args.days), s
        label = f"Last {args.days} days"
        slug = f"{e.strftime('%Y-%m-%d')}_{args.days}d"

    cur, prev, backlog, pr_backlog, npm_series = {}, {}, {}, {}, {}
    for k in KEYS:
        issues = prepare(k, "issues", as_of)
        prs = prepare(k, "prs", as_of)
        team = load_team(k, prs + issues)
        fx = fixes(k, prs, as_of)
        vers = releases(k, as_of)
        npm = npm_daily(k, as_of)
        cur[k] = period_stats(issues, prs, team, fx, vers, npm, k, s, e)
        prev[k] = period_stats(issues, prs, team, fx, vers, npm, k, ps, pe)
        lookback = max(90, 3 * (e - s).days)
        start = (e - timedelta(days=lookback)).replace(hour=0, minute=0, second=0)
        backlog[k] = daily_backlog(issues, start, e)
        pr_backlog[k] = daily_backlog(prs, start, e)
        npm_series[k] = rolling_npm(npm, start, e)
        print(f"{k}: {cur[k]['opened']:,} new, {cur[k]['closed']:,} closed, {cur[k]['backlog_end']:,} open at {iso(e)}")

    out = args.out or os.path.join(ROOT, "reports", f"harness-report_{slug}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(render(cur, prev, backlog, pr_backlog, npm_series, s, e, ps, pe, as_of, label))
    print(f"report -> {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
