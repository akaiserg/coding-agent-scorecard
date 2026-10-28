#!/usr/bin/env python3
"""General report: how each repository's issues, pull requests and merges evolved month by month since
launch, and how big its issues and PRs are.

  overview.py [--as-of ISO] [--since YYYY-MM] [--out PATH]

Writes a self-contained HTML file, reports/harness-overview_<date>.html. Shares the look of report.py.
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import log10
from statistics import median

from classify import pr_policy, prepare, star_snapshots
from common import RAW, ROOT, data_as_of, now, ts
from config import REPOS
from report import CSS, INK3, KEYS, RULE, SURFACE, esc, fdate, fnum, fpct, name, nice_ticks, short

# Ordered size buckets: (label, lower bound inclusive, upper bound exclusive or None).
PR_SIZES = [("XS · under 10 lines", 0, 10), ("S · 10–99", 10, 100), ("M · 100–499", 100, 500),
            ("L · 500–999", 500, 1000), ("XL · 1,000+", 1000, None)]
COMMENT_SIZES = [("No comments", 0, 1), ("1–2", 1, 3), ("3–9", 3, 10), ("10–29", 10, 30), ("30+", 30, None)]
CLOSE_TIMES = [("Under a day", 0, 1), ("1–7 days", 1, 7), ("1–4 weeks", 7, 30), ("1–3 months", 30, 90), ("3+ months", 90, None)]
# One neutral hue, light to dark, so ordered buckets never read as a repository color.
RAMP = ["#e2e4e8", "#c0c4cc", "#959aa5", "#686e7a", "#3d424b"]
RAMP_TEXT = [INK3, "#3d424b", SURFACE, SURFACE, SURFACE]
MIN_PRS = 5
MIN_FIXES = 30  # linked fixes needed before a fix-size distribution is drawn


# ---------- data ----------

def month_start(d):
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc)


def next_month(d):
    return datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)


def mkey(d):
    return d.strftime("%Y-%m")


def mlabel(d):
    return f"{d.strftime('%b')} ’{d.strftime('%y')}"


def count_of(v):
    """A GraphQL `{totalCount}` or an older plain number; None if the field was not fetched."""
    return v.get("totalCount") if isinstance(v, dict) else v


def bucket(v, buckets):
    return next(i for i, (_, lo, hi) in enumerate(buckets) if v >= lo and (hi is None or v < hi))


def distribution(values, buckets):
    c = Counter(bucket(v, buckets) for v in values)
    return [c.get(i, 0) for i in range(len(buckets))]


def default_branch(key):
    path = os.path.join(RAW, f"{key}_main_commits.json")
    return json.load(open(path)) if os.path.exists(path) else {"branch": None, "months": {}}


def open_counts(items, ends):
    """How many items were open at each time in `ends` (created before it, not closed by then)."""
    created = sorted(n["_c"] for n in items)
    closed = sorted(n["_x"] for n in items if n["_x"])
    out, i, j = [], 0, 0
    for t in ends:
        while i < len(created) and created[i] < t:
            i += 1
        while j < len(closed) and closed[j] < t:
            j += 1
        out.append(i - j)
    return out


def repo_data(key, as_of, months):
    issues = prepare(key, "issues", as_of)
    prs = prepare(key, "prs", as_of)
    main = default_branch(key)
    first = month_start(min(n["_c"] for n in issues + prs))
    M = {mkey(m): Counter() for m in months}
    hours, lines = {k: [] for k in M}, {k: [] for k in M}
    for n in issues:
        if mkey(n["_c"]) in M:
            M[mkey(n["_c"])]["opened"] += 1
        if n["_x"] and mkey(n["_x"]) in M:
            M[mkey(n["_x"])]["closed"] += 1
            M[mkey(n["_x"])]["completed" if n["_reason"] == "COMPLETED" else "closed_other"] += 1
    size_known = [p for p in prs if p.get("additions") is not None]
    for p in prs:
        if mkey(p["_c"]) in M:
            M[mkey(p["_c"])]["prs_opened"] += 1
        if p["_m"] and mkey(p["_m"]) in M:
            mm = mkey(p["_m"])
            M[mm]["prs_merged"] += 1
            if p.get("baseRefName") == main["branch"]:
                M[mm]["prs_merged_default"] += 1
                hours[mm].append((p["_m"] - p["_c"]).total_seconds() / 3600)
                if p.get("additions") is not None:
                    lines[mm].append(p["additions"] + p["deletions"])
        elif p["_x"] and mkey(p["_x"]) in M:
            M[mkey(p["_x"])]["prs_closed_unmerged"] += 1
    ends = [min(next_month(m), as_of) for m in months]
    for m, n_open, pr_open in zip(months, open_counts(issues, ends), open_counts(prs, ends)):
        r = M[mkey(m)]
        live = m >= first
        r["issues_open_end"] = n_open if live else None
        r["prs_open_end"] = pr_open if live else None
        r["commits_default"] = main["months"].get(mkey(m)) if live else None
        # A median of a handful of PRs swings wildly, so months with fewer than MIN_PRS are left out.
        r["merge_hours"] = median(hours[mkey(m)]) if len(hours[mkey(m)]) >= MIN_PRS else None
        r["pr_lines"] = median(lines[mkey(m)]) if len(lines[mkey(m)]) >= MIN_PRS else None
        r["live"] = live
        r["prs_closed"] = r["prs_merged"] + r["prs_closed_unmerged"]

    # Sizes over the last 12 months.
    year = as_of - timedelta(days=365)
    merged_default = [p for p in size_known if p["_m"] and p["_m"] >= year and p.get("baseRefName") == main["branch"]]
    pr_lines = [p["additions"] + p["deletions"] for p in merged_default]
    # Issues at least 30 days old, so recent ones have had time to collect a discussion.
    settled = [n for n in issues if year <= n["_c"] < as_of - timedelta(days=30) and count_of(n.get("comments")) is not None]
    comments = [count_of(n["comments"]) for n in settled]
    completed = [n for n in issues if n["_x"] and n["_x"] >= year and n["_reason"] == "COMPLETED"]
    close_days = [(n["_x"] - n["_c"]).total_seconds() / 86400 for n in completed]
    fix_lines = {}
    for p in size_known:
        if p["_m"]:
            for ref in (p.get("closingIssuesReferences") or {}).get("nodes", []):
                fix_lines[ref["number"]] = fix_lines.get(ref["number"], 0) + p["additions"] + p["deletions"]
    fixed = [fix_lines[n["number"]] for n in completed if n["number"] in fix_lines]

    merged_all = [p for p in prs if p["_m"]]
    return {
        "monthly": M,
        "branch": main["branch"],
        "issues_total": len(issues),
        "issues_open": sum(1 for n in issues if not n["_x"]),
        "prs_total": len(prs),
        "prs_merged": len(merged_all),
        "prs_open": sum(1 for p in prs if not p["_x"]),
        "commits_12m": sum(v for m, v in main["months"].items() if m >= mkey(year)),
        "first": first,
        "pr_size_coverage": len(size_known) / len(prs) if prs else None,
        "pr_lines": pr_lines,
        "pr_sizes": distribution(pr_lines, PR_SIZES),
        "comment_coverage": len(settled) / max(1, sum(1 for n in issues if year <= n["_c"] < as_of - timedelta(days=30))),
        "comments": comments,
        "comment_sizes": distribution(comments, COMMENT_SIZES),
        "close_days": close_days,
        "close_sizes": distribution(close_days, CLOSE_TIMES),
        "fix_lines": fixed,
        "fix_sizes": distribution(fixed, PR_SIZES),
        "fix_coverage": len(fixed) / len(completed) if completed else None,
        "policy": pr_policy(key),
    }


# ---------- charts (inline SVG) ----------

def x_months(months, ml, width):
    step = width / len(months)
    return step, (lambda i: ml + step * (i + 0.5))


def month_ticks(months, X, y, every=3):
    """Labels on January, April, July and October."""
    return "".join(f'<text x="{X(i):.1f}" y="{y}" text-anchor="middle" class="ax">{mlabel(m)}</text>'
                   for i, m in enumerate(months) if m.month % every == 1)


def chart_pairs(months, data, f, shared=True, partial=False):
    """Small multiples, one panel per repo: per month a gray bar for new items and a bar of closed items
    split into the good outcome (solid) and the rest (pale)."""
    W, PH, ml, mr, mt, gap = 960, 150, 52, 12, 22, 34
    H = len(KEYS) * (PH + gap)
    step, X = x_months(months, ml, W - ml - mr)
    bw = max(2, min(16, step * 0.36))
    vmax_all = max(max(r[f["new"]], r[f["good"]] + r[f["other"]]) for k in KEYS for r in data[k]["monthly"].values()) or 1
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="New and closed {f["noun"]} per month">']
    for pi, k in enumerate(KEYS):
        top = pi * (PH + gap) + mt
        rows = [data[k]["monthly"][mkey(m)] for m in months]
        vmax = vmax_all if shared else (max(max(r[f["new"]], r[f["good"]] + r[f["other"]]) for r in rows) or 1)
        ticks = nice_ticks(0, vmax, 3)
        ymax = ticks[-1]
        Y = lambda v: top + (PH - mt - 18) * (1 - v / ymax)
        out.append(f'<text x="0" y="{top - 8}" class="lab"><tspan fill="{REPOS[k]["color"]}">●</tspan> {esc(name(k))}'
                   + ('' if shared else ' <tspan class="ax">(own scale)</tspan>') + '</text>')
        for t in ticks:
            out.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{RULE}"/>')
            out.append(f'<text x="{ml - 8}" y="{Y(t):.1f}" dy="0.32em" text-anchor="end" class="ax">{short(t)}</text>')
        color = REPOS[k]["color"]
        for i, (m, r) in enumerate(zip(months, rows)):
            if not r["live"]:
                continue
            fade = ' opacity="0.55"' if partial and i == len(months) - 1 else ""
            x = X(i)
            nv, gv, ov = r[f["new"]], r[f["good"]], r[f["other"]]
            tip = (f"{esc(name(k))} · {mlabel(m)}{' (to date)' if fade else ''}: {nv:,} new, {gv + ov:,} closed "
                   f"({gv:,} {f['good_word']}, {ov:,} {f['other_word']})")
            out.append(f'<g{fade}><title>{tip}</title>')
            out.append(f'<rect x="{x - bw - 1:.1f}" y="{Y(nv):.1f}" width="{bw:.1f}" height="{max(0, Y(0) - Y(nv)):.1f}" rx="2" fill="{INK3}" fill-opacity="0.55"/>')
            hg, ho = Y(0) - Y(gv), Y(gv) - Y(gv + ov)
            out.append(f'<rect x="{x + 1:.1f}" y="{Y(gv):.1f}" width="{bw:.1f}" height="{max(0, hg):.1f}" rx="2" fill="{color}"/>')
            if ov:
                out.append(f'<rect x="{x + 1:.1f}" y="{Y(gv + ov):.1f}" width="{bw:.1f}" height="{max(0, ho - (2 if gv else 0)):.1f}" rx="2" fill="{color}" fill-opacity="0.32"/>')
            out.append(f'<rect x="{x - bw - 3:.1f}" y="{top - 4}" width="{2 * bw + 6:.1f}" height="{PH - mt - 14}" fill="transparent"/></g>')
        out.append(month_ticks(months, X, top + PH - mt + 2))
    out.append("</svg>")
    return "".join(out)


def chart_lines(months, series, fmt=lambda v: f"{v:,.0f}", log=False, floor=None, partial=False, tick_fmt=None, log_ticks=None):
    """One line per repo across months, labelled at its last point. Months without a value break the line."""
    W, H, ml, mr, mt, mb = 960, 280, 56, 170, 14, 28
    step, X = x_months(months, ml, W - ml - mr)
    vals = [v for s in series.values() for v in s if v is not None]
    if log:
        lo = floor or min(v for v in vals if v > 0)
        cand = log_ticks or [10 ** e for e in range(int(log10(lo) // 1), int(-(-log10(max(vals)) // 1)) + 1)]
        top = next((t for t in cand if t >= max(vals)), cand[-1])
        ticks = [t for t in cand if lo <= t <= top]
        a, b = log10(lo), log10(top)
        Y = lambda v: mt + (H - mt - mb) * (1 - (log10(max(v, lo)) - a) / max(1e-9, b - a))
    else:
        ticks = nice_ticks(0, max(vals) or 1)
        Y = lambda v: mt + (H - mt - mb) * (1 - v / ticks[-1])
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img">']
    for t in ticks:
        out.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{RULE}"/>')
        out.append(f'<text x="{ml - 8}" y="{Y(t):.1f}" dy="0.32em" text-anchor="end" class="ax">{(tick_fmt or short)(t)}</text>')
    out.append(month_ticks(months, X, H - 6))
    labels = []
    for k, s in series.items():
        color = REPOS[k]["color"]
        pts = [(i, v) for i, v in enumerate(s) if v is not None and (not log or v > 0 or floor)]
        if not pts:
            continue
        solid = [p for p in pts if not (partial and p[0] == len(months) - 1)]
        path = " ".join(f"{'L' if j and solid[j - 1][0] == i - 1 else 'M'}{X(i):.1f},{Y(v):.1f}" for j, (i, v) in enumerate(solid))
        out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        for j, (i, v) in enumerate(solid):  # a month with no neighbours would draw nothing, so mark it
            if not any(o in (i - 1, i + 1) for o, _ in solid):
                out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.5" fill="{color}"/>')
        if partial and len(solid) < len(pts) and solid and solid[-1][0] == pts[-1][0] - 1:
            (i0, v0), (i1, v1) = solid[-1], pts[-1]
            out.append(f'<path d="M{X(i0):.1f},{Y(v0):.1f} L{X(i1):.1f},{Y(v1):.1f}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="4 4"/>')
        for i, v in pts:
            out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="6" fill="transparent"><title>{esc(name(k))} · {mlabel(months[i])}: {fmt(v)}</title></circle>')
        i, v = pts[-1]
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>')
        labels.append([Y(v), k, v])
    labels.sort()
    for j in range(1, len(labels)):
        labels[j][0] = max(labels[j][0], labels[j - 1][0] + 18)
    for y, k, v in labels:
        out.append(f'<text x="{W - mr + 12}" y="{y:.1f}" dy="0.32em" class="lab"><tspan fill="{REPOS[k]["color"]}">●</tspan> {esc(name(k))} <tspan class="labv">{fmt(v)}</tspan></text>')
    out.append("</svg>")
    return "".join(out)


def chart_buckets(rows, buckets, unit, who=lambda k: (REPOS[k]["color"], name(k))):
    """Horizontal 100% bars, one per row (a repo unless `who` maps keys to a color and label), split into ordered buckets."""
    W, row, gap, ml, mr = 960, 28, 16, 120, 190
    H = len(rows) * (row + gap) + 34
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img">']
    x = ml
    for i, (label, _, _) in enumerate(buckets):  # legend
        out.append(f'<rect x="{x}" y="2" width="12" height="12" rx="3" fill="{RAMP[i]}"/>'
                   f'<text x="{x + 18}" y="8" dy="0.32em" class="ax">{esc(label)}</text>')
        x += 30 + 7.2 * len(label)
    y = 30
    for k, counts, note in rows:
        total = sum(counts)
        color, label = who(k)
        out.append(f'<text x="0" y="{y + row / 2}" dy="0.32em" class="lab"><tspan fill="{color}">●</tspan> {esc(label)}</text>')
        if not total:
            out.append(f'<text x="{ml}" y="{y + row / 2}" dy="0.32em" class="ax">{esc(note)}</text>')
            y += row + gap
            continue
        x = ml
        for i, c in enumerate(counts):
            w = (W - ml - mr) * c / total
            if w <= 0:
                continue
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(0.5, w - 2):.1f}" height="{row}" rx="3" fill="{RAMP[i]}">'
                       f'<title>{esc(label)} · {esc(buckets[i][0])}: {c:,} {unit} ({c / total:.0%})</title></rect>')
            if w > 38:
                out.append(f'<text x="{x + w / 2 - 1:.1f}" y="{y + row / 2}" dy="0.32em" text-anchor="middle" class="ax" style="fill:{RAMP_TEXT[i]}">{c / total:.0%}</text>')
            x += w
        out.append(f'<text x="{W - mr + 10}" y="{y + row / 2}" dy="0.32em" class="labv">{esc(note)}</text>')
        y += row + gap
    out.append("</svg>")
    return "".join(out)


# ---------- page ----------

ISSUES = {"new": "opened", "good": "completed", "other": "closed_other", "noun": "issues",
          "good_word": "completed", "other_word": "not planned or duplicate"}
PRS = {"new": "prs_opened", "good": "prs_merged", "other": "prs_closed_unmerged", "noun": "PRs",
       "good_word": "merged", "other_word": "closed without merging"}


def key_points(data, months, partial):
    last3 = [mkey(m) for m in (months[:-1] if partial else months)[-3:]]  # the last three full months
    avg = lambda k, f: sum(data[k]["monthly"][m][f] for m in last3) / 3
    pts = []
    order = sorted(KEYS, key=lambda k: -avg(k, "opened"))
    pts.append("<b>Issues.</b> Over the last three full months, new issues a month: " + "; ".join(
        f"{name(k)} {avg(k, 'opened'):,.0f} in, {avg(k, 'closed'):,.0f} closed" for k in order) + ". "
        + " ".join(f"{name(k)}'s open issues went from {data[k]['monthly'][mkey(months[-13])]['issues_open_end']:,} a year ago to {data[k]['issues_open']:,}."
                   for k in KEYS if len(months) > 13 and data[k]["monthly"][mkey(months[-13])]["issues_open_end"] is not None))
    order = sorted(KEYS, key=lambda k: -avg(k, "prs_opened"))
    pts.append("<b>Pull requests.</b> PRs opened a month: " + "; ".join(
        f"{name(k)} {avg(k, 'prs_opened'):,.0f}, {avg(k, 'prs_merged') / max(1, avg(k, 'prs_closed')):.0%} of closed ones merged" for k in order)
        + "." + ("".join(f" {' and '.join(closed)} only {'accepts' if len(closed) == 1 else 'accept'} PRs from collaborators, a repository setting."
                         for closed in [[name(k) for k in KEYS if data[k]["policy"] == "collaborators_only"]] if closed)))
    pts.append("<b>Merges into the main branch.</b> Commits landing on the default branch a month: " + "; ".join(
        f"{name(k)} {avg(k, 'commits_default'):,.0f} (<code>{esc(data[k]['branch'])}</code>)" for k in sorted(KEYS, key=lambda k: -avg(k, "commits_default")))
        + ". Median time from opening a PR to merging it there, last full month: " + "; ".join(
        f"{name(k)} {fmt_hours(data[k]['monthly'][last3[-1]]['merge_hours'])}" for k in KEYS) + ".")
    sized = [k for k in KEYS if data[k]["pr_lines"]]
    if sized:
        pts.append("<b>Size.</b> A typical merged PR changes " + "; ".join(
            f"{fnum(median(data[k]['pr_lines']))} lines in {name(k)}" for k in sized)
            + " (median, last 12 months). A typical issue gets " + "; ".join(
            f"{fnum(median(data[k]['comments']))} comments in {name(k)}" for k in KEYS if data[k]["comments"]) + ".")
    return pts


def fmt_hours(v):
    if v is None:
        return "–"
    if v < 1 / 60:
        return "under 1 min"
    if v < 1:
        return f"{v * 60:,.0f} min"
    return f"{v:,.1f} h" if v < 48 else f"{v / 24:,.1f} days"


def render(data, months, as_of, partial):
    sw = lambda k: f'<span class="sw" style="background:{REPOS[k]["color"]}"></span>'
    legend = '<div class="legend">' + "".join(f'<span>{sw(k)}{esc(name(k))}</span>' for k in KEYS) + "</div>"
    span = f"{mlabel(months[0])} to {mlabel(months[-1])}" + (" (last month to date)" if partial else "")
    p = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Agent Harness GitHub Overview</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>{CSS}
.tbl table.months {{ min-width: 760px; }}
.tbl table.months th, .tbl table.months td {{ padding: 6px 8px; font-size: 13.5px; }}
.tbl table.months thead tr:first-child th {{ text-align: center; border-bottom: none; }}
td.neg {{ color: var(--ink); }}
</style></head><body><main class="wrap">
<p class="eyebrow">GitHub overview · AI coding agent harnesses</p>
<h1>{" vs ".join(esc(name(k)) for k in KEYS)}: the long view</h1>
<p class="period">Month by month, {span}. Data as of {fdate(as_of)}, {as_of.strftime('%H:%M')} UTC.</p>
<div class="cards">"""]
    for k in KEYS:
        d = data[k]
        p.append(f"""<section class="card"><h3>{sw(k)}{esc(name(k))}</h3><div class="kpis">
<div class="kpi"><div class="v">{short(d['stars'])}</div><div class="k">GitHub stars</div></div>
<div class="kpi"><div class="v">{d['issues_open']:,}</div><div class="k">open of {d['issues_total']:,} issues</div></div>
<div class="kpi"><div class="v">{d['prs_merged']:,}</div><div class="k">merged of {d['prs_total']:,} PRs ({d['prs_open']:,} open)</div></div>
<div class="kpi"><div class="v">{d['commits_12m']:,}</div><div class="k">commits to <code>{esc(d['branch'])}</code>, last 12 months</div></div>
</div></section>""")
    p.append('</div><h2>Key points</h2><ul class="take">' + "".join(f"<li>{t}</li>" for t in key_points(data, months, partial)) + "</ul>")

    snaps = {k: star_snapshots(k) for k in KEYS}
    days = sorted({t.date() for s in snaps.values() for t, _ in s})
    p.append(f"""<h2>Stars</h2><p class="sub">GitHub no longer publishes when each star was given, for any repository, so star history before this project
cannot be rebuilt from GitHub. Every run of <code>scripts/run.sh</code> records the count; {"the table shows each day recorded." if len(days) > 1 else f"recording started on {fdate(datetime.combine(days[0], datetime.min.time())) if days else 'the next run'}, and growth appears here once more days are recorded."}</p>""")
    if len(days) > 1:
        p.append('<div class="tbl"><table><thead><tr><th>Day</th>' + "".join(f"<th>{sw(k)} {esc(name(k))}</th>" for k in KEYS) + "</tr></thead><tbody>")
        for day in days[-12:]:
            cells = []
            for k in KEYS:
                vals = [n for t, n in snaps[k] if t.date() == day]
                cells.append(f"<td>{vals[-1]:,}</td>" if vals else "<td>–</td>")
            p.append(f"<tr><td>{day.strftime('%d %b %Y')}</td>{''.join(cells)}</tr>")
        p.append("</tbody></table></div>")

    series = lambda f: {k: [data[k]["monthly"][mkey(m)][f] if data[k]["monthly"][mkey(m)]["live"] else None for m in months] for k in KEYS}
    p.append(f"""<h2>Issues</h2>
<div class="fig"><h3>New and closed issues per month</h3><p>Gray: issues opened. Colored: issues closed, solid if completed, pale if not planned or duplicate. Same scale in every panel{"; the last month is to date and drawn faded" if partial else ""}.</p>{chart_pairs(months, data, ISSUES, shared=True, partial=partial)}</div>
<div class="fig"><h3>Open issues at each month end</h3><p>Created before the month ended and not yet closed on that date.</p>{legend}{chart_lines(months, series("issues_open_end"), partial=partial)}</div>""")

    recent = months[-12:]
    p.append('<div class="fig"><h3>Issues opened and closed, month by month</h3><p>Last 12 months. Net is opened minus closed: positive means the backlog grew.</p><div class="tbl"><table class="months"><thead><tr><th></th>'
             + "".join(f'<th colspan="3">{sw(k)} {esc(name(k))}</th>' for k in KEYS) + '</tr><tr><th>Month</th>'
             + "<th>Opened</th><th>Closed</th><th>Net</th>" * len(KEYS) + "</tr></thead><tbody>")
    for m in reversed(recent):
        cells = []
        for k in KEYS:
            r = data[k]["monthly"][mkey(m)]
            net = r["opened"] - r["closed"]
            cells.append(f"<td>{r['opened']:,}</td><td>{r['closed']:,}</td><td>{'+' if net > 0 else '−' if net < 0 else ''}{abs(net):,}</td>")
        p.append(f"<tr><td>{mlabel(m)}{' (to date)' if partial and m == months[-1] else ''}</td>{''.join(cells)}</tr>")
    p.append("</tbody></table></div></div>")

    p.append(f"""<h2>Pull requests</h2>
<div class="fig"><h3>Opened and closed PRs per month</h3><p>Gray: PRs opened. Colored: PRs closed, solid if merged, pale if closed without merging. Each panel has its own scale, because Claude Code's public repository gets few PRs.</p>{chart_pairs(months, data, PRS, shared=False, partial=partial)}</div>
<div class="fig"><h3>Open PRs at each month end</h3><p>Created before the month ended and not yet merged or closed on that date.</p>{legend}{chart_lines(months, series("prs_open_end"), partial=partial)}</div>""")

    branches = ", ".join(f"<code>{esc(data[k]['branch'])}</code> for {esc(name(k))}" for k in KEYS)
    p.append(f"""<h2>Merges into the main branch</h2><p class="sub">The default branch is {branches}. Commits counts everything that landed there; with squash merges each merged PR is one commit, and Codex's are synced from an internal repository.</p>
<div class="fig"><h3>Commits landing on the default branch per month</h3>{legend}{chart_lines(months, series("commits_default"), partial=partial)}</div>
<div class="fig"><h3>Median time from opening a PR to merging it into the default branch</h3><p>By month of merge; months with fewer than {MIN_PRS} merged PRs are left out. Log scale: each gridline is ten times the one below. Codex's synced PRs merge within minutes of being opened.</p>{legend}{chart_lines(months, series("merge_hours"), fmt=fmt_hours, log=True, floor=1 / 60, log_ticks=[1 / 60, 1 / 6, 1, 10, 100, 1000, 10000], partial=partial, tick_fmt=lambda v: f"{v * 60:,.0f} min" if v < 1 else f"{v:,.0f} h")}</div>""")

    cov = min((data[k]["pr_size_coverage"] or 0) for k in KEYS)

    def fix_row(k):
        n = len(data[k]["fix_lines"])
        if n < MIN_FIXES:
            return k, [0] * len(PR_SIZES), f"only {n} linked fixes, too few to show" if n else "no linked fixes"
        return k, data[k]["fix_sizes"], f"median {fnum(median(data[k]['fix_lines']))} lines · {n:,} issues"
    fix_cov = ", ".join(f"{'under 1%' if 0 < (data[k]['fix_coverage'] or 0) < 0.01 else fpct(data[k]['fix_coverage'])} of {esc(name(k))}'s" for k in KEYS)
    p.append(f"""<h2>How big are issues and PRs</h2><p class="sub">GitHub has no size field for issues, so size is measured three ways: how much discussion an issue draws, how long it takes to resolve, and how many lines the PR that fixed it changed. PR size is lines added plus lines deleted. All over the last 12 months.</p>
<div class="fig"><h3>PR size: lines changed per merged PR</h3><p>PRs merged into the default branch.</p>{chart_buckets([(k, data[k]['pr_sizes'], f"median {fnum(median(data[k]['pr_lines']))} lines · {len(data[k]['pr_lines']):,} PRs" if data[k]['pr_lines'] else 'no size data yet') for k in KEYS], PR_SIZES, 'PRs')}</div>
<div class="fig"><h3>Median lines changed per merged PR, by month</h3><p>Months with fewer than {MIN_PRS} merged PRs are left out.</p>{legend}{chart_lines(months, series("pr_lines"), partial=partial)}</div>
<div class="fig"><h3>Issue size by discussion: comments per issue</h3><p>Issues opened 1 to 12 months ago, so each has had at least a month to collect comments.</p>{chart_buckets([(k, data[k]['comment_sizes'], f"median {fnum(median(data[k]['comments']))} · {len(data[k]['comments']):,} issues" if data[k]['comments'] else 'no comment data yet') for k in KEYS], COMMENT_SIZES, 'issues')}</div>
<div class="fig"><h3>Issue size by time: days from opening to a completed close</h3><p>Issues closed as completed.</p>{chart_buckets([(k, data[k]['close_sizes'], f"median {fnum(median(data[k]['close_days']), 1)} days · {len(data[k]['close_days']):,} issues" if data[k]['close_days'] else 'none') for k in KEYS], CLOSE_TIMES, 'issues')}</div>
<div class="fig"><h3>Issue size by fix: lines changed by the PR that closed it</h3><p>Only completed issues that a merged PR declares it closes: {fix_cov} completed issues. Claude Code's source is not public, so its fixes can rarely be sized.</p>{chart_buckets([fix_row(k) for k in KEYS], PR_SIZES, 'issues')}</div>""")

    p.append(f"""<section class="notes"><b>How to read this</b><ul>
<li>Every issue and pull request of {", ".join(esc(REPOS[k]['repo']) for k in KEYS)} is read from the GitHub API with <code>gh</code>. Months are calendar months in UTC.</li>
<li><b>Closed</b> issues are split by GitHub's close reason; <b>completed</b> means resolved. PRs are either merged or closed without merging.</li>
<li><b>Commits on the default branch</b> come from the branch's git history, counted by GitHub per month.</li>
<li><b>Sizes</b> {"are known for every PR" if cov >= 0.995 else f"need fields added on {fdate(datetime(2026, 10, 27, tzinfo=timezone.utc))}; they cover {fpct(cov)} of PRs so far, and older records fill in on the next run"}. Comment counts are as of the last fetch.</li>
<li><b>Stars</b>: GitHub reports only the current total. Public event archives were checked as a source of history but are incomplete for these repositories.</li>
</ul></section></main></body></html>""")
    return "".join(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="ISO time the data is read at (default: last sync of every repo)")
    ap.add_argument("--since", help="first month YYYY-MM (default: the first month any repository existed)")
    ap.add_argument("--out", help="output path (default reports/harness-overview_<date>.html)")
    args = ap.parse_args()
    as_of = ts(args.as_of) if args.as_of else (data_as_of() or now())

    first = min(month_start(min(n["_c"] for n in prepare(k, "issues", as_of))) for k in KEYS)
    if args.since:
        first = datetime.strptime(args.since, "%Y-%m").replace(tzinfo=timezone.utc)
    months, m = [], first
    while m < as_of:
        months.append(m)
        m = next_month(m)
    partial = next_month(months[-1]) > as_of

    data = {}
    for k in KEYS:
        data[k] = repo_data(k, as_of, months)
        snaps = star_snapshots(k)
        data[k]["stars"] = snaps[-1][1] if snaps else 0
        print(f"{k}: {data[k]['issues_total']:,} issues, {data[k]['prs_total']:,} PRs, "
              f"PR sizes known for {fpct(data[k]['pr_size_coverage'])}, default branch {data[k]['branch']}")

    out = args.out or os.path.join(ROOT, "reports", f"harness-overview_{as_of.strftime('%Y-%m-%d')}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(render(data, months, as_of, partial))
    print(f"overview -> {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
