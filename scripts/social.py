#!/usr/bin/env python3
"""One-image summary for a social post (LinkedIn): two findings, each with the chart that shows it, and a strip
of reference numbers.

  social.py [--as-of ISO] [--days N] [--out PATH]

1. Open issues day by day over 90 days, with each backlog's change over the last N days.
2. Issues received against issues closed over the last N days, with the closes split into resolved and not resolved.
Then GitHub stars and merged PRs per repo.

Writes reports/harness-social_<date>.html at 1080x1350 (4:5) and, when Chrome or Chromium is installed,
renders it to a PNG beside it at twice that resolution.
"""
import argparse
import os
import shutil
import subprocess
from datetime import timedelta

from classify import fixes, load_team, npm_daily, prepare, releases, stars_now
from common import ROOT, data_as_of, now, ts
from config import REPOS
from report import BAD, GOOD, INK, INK2, INK3, RULE, SURFACE, KEYS, daily_backlog, esc, fdate, fday, name, nice_ticks, period_stats

W, H = 1080, 1350
CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium", "chromium-browser"]
NEW = "#b9bec7"  # new issues: neutral, so color stays with the repo's closes
PALE = 0.32  # opacity of closes that were not completed
KEY = "Gray: issues received. Color: issues closed; solid = resolved, pale = duplicate or not planned."


def k_short(v):
    return f"{v / 1000:.1f}k".replace(".0k", "k") if v >= 1000 else f"{v:,.0f}"


def change(c, days):
    """Change in open issues over the period (the shaded band), as a count and a share, with an arrow in its status
    color and a word, so it never relies on color alone."""
    g, d = c["backlog_growth"], c["backlog_end"] - c["backlog_start"]
    if g is None:
        return ""
    return (f'<tspan fill="{GOOD if d < 0 else BAD}">{"▼" if d < 0 else "▲"}</tspan> '
            f'{abs(d):,} {"fewer" if d < 0 else "more"} ({"−" if d < 0 else "+"}{abs(g):.0%})')


def chart_open_issues(series, cur, window_start, end, days, h=300):
    """Open issues per day, one line per repo, labelled at the end with the level and its change."""
    w, ml, mr, mt, mb = 984, 64, 268, 10, 38
    x0 = min(s[0][0] for s in series.values())
    vmax = max(v for s in series.values() for _, v in s)
    ticks = nice_ticks(0, vmax, 4)
    X = lambda d: ml + (w - ml - mr) * (d - x0).total_seconds() / max(1, (end - x0).total_seconds())
    Y = lambda v: mt + (h - mt - mb) * (1 - v / ticks[-1])
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
           f'<rect x="{X(window_start):.1f}" y="{mt}" width="{X(end) - X(window_start):.1f}" height="{h - mt - mb}" fill="#f3f4f6"/>',
           f'<text x="{X(window_start) + 10:.1f}" y="{mt + 24}" class="ax">Last {days} days</text>']
    for t in ticks:
        out.append(f'<line x1="{ml}" x2="{w - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{RULE}" stroke-width="1.5"/>')
        out.append(f'<text x="{ml - 12}" y="{Y(t):.1f}" dy="0.35em" text-anchor="end" class="ax">{k_short(t)}</text>')
    d = x0
    while d <= end:
        if d.day == 1:
            out.append(f'<text x="{X(d):.1f}" y="{h - 6}" text-anchor="middle" class="ax">{fday(d)}</text>')
        d += timedelta(days=1)
    labels = []
    for k, pts in series.items():
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(t):.1f},{Y(v):.1f}" for i, (t, v) in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{REPOS[k]["color"]}" stroke-width="4.5" stroke-linejoin="round" stroke-linecap="round"/>')
        t, v = pts[-1]
        out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="8" fill="{REPOS[k]["color"]}" stroke="{SURFACE}" stroke-width="3"/>')
        labels.append([Y(v), k, v])
    labels.sort()
    for i in range(1, len(labels)):  # two-line labels: keep them apart
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 56)
    for y, k, v in labels:
        x = w - mr + 22
        out.append(f'<text x="{x}" y="{y - 12:.1f}" dy="0.35em" class="end">{esc(name(k))} <tspan class="endv">{k_short(v)}</tspan></text>'
                   f'<text x="{x}" y="{y + 15:.1f}" dy="0.35em" class="chg">{change(cur[k], days)}</text>')
    out.append("</svg>")
    return "".join(out)


def chart_in_out(cur, keys=KEYS):
    """Per repo, a bar of new issues over a bar of closed ones; closes are solid if completed, pale otherwise."""
    w, ml, mr, bar, gap, group = 984, 190, 330, 26, 6, 30
    top = max(max(cur[k]["opened"], cur[k]["closed"]) for k in keys)
    X = lambda v: (w - ml - mr) * v / top
    h = len(keys) * (2 * bar + gap + group) - group
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">']
    y = 0
    for k in keys:
        c, col = cur[k], REPOS[k]["color"]
        done, other = c["completed"], c["closed"] - c["completed"]
        net = c["backlog_end"] - c["backlog_start"]  # received minus closed: the change in open issues
        out.append(f'<text x="0" y="{y + bar + gap / 2 - 13:.1f}" dy="0.35em" class="lab">{esc(name(k))}</text>'
                   f'<text x="0" y="{y + bar + gap / 2 + 15:.1f}" dy="0.35em" class="chg">'
                   f'<tspan fill="{GOOD if net < 0 else BAD}">{"▼" if net < 0 else "▲"}</tspan> {abs(net):,} {"fewer" if net < 0 else "more"} open</text>')
        out.append(f'<rect x="{ml}" y="{y}" width="{max(2, X(c["opened"])):.1f}" height="{bar}" rx="4" fill="{NEW}"/>'
                   f'<text x="{ml + X(c["opened"]) + 12:.1f}" y="{y + bar / 2}" dy="0.35em" class="val"><tspan class="b">{c["opened"]:,}</tspan> received</text>')
        y2 = y + bar + gap
        if done:
            out.append(f'<rect x="{ml}" y="{y2}" width="{max(2, X(done) - 2):.1f}" height="{bar}" rx="4" fill="{col}"/>')
        if other:
            out.append(f'<rect x="{ml + X(done):.1f}" y="{y2}" width="{max(2, X(other)):.1f}" height="{bar}" rx="4" fill="{col}" fill-opacity="{PALE}"/>')
        out.append(f'<text x="{ml + X(c["closed"]) + 12:.1f}" y="{y2 + bar / 2}" dy="0.35em" class="val">'
                   f'<tspan class="b">{c["closed"]:,}</tspan> closed · <tspan class="b">{done:,}</tspan> resolved</text>')
        y += 2 * bar + gap + group
    out.append("</svg>")
    return "".join(out)


def findings(cur):
    """The two takeaways, written from the numbers so they stay true on every run."""
    growth = {k: cur[k]["backlog_growth"] for k in KEYS if cur[k]["backlog_growth"] is not None}
    best, worst = min(growth, key=growth.get), max(growth, key=growth.get)
    b = cur[best]
    if growth[best] < 0 and b["closed"] > b["opened"]:
        first = (f"{name(best)} closed more issues than it received ({b['closed']:,} vs {b['opened']:,}) "
                 f"and cut its open backlog {abs(growth[best]):.0%}")
    else:
        first = f"{name(best)}’s open issues changed least ({growth[best]:+.0%})"
    first += f". {name(worst)}’s grew {growth[worst]:.0%}, to {cur[worst]['backlog_end']:,}."
    share = {k: cur[k]["completed"] / cur[k]["closed"] for k in KEYS if cur[k]["closed"]}
    lo, hi = min(share, key=share.get), max(share, key=share.get)
    second = (f"Closed isn’t resolved. Only {share[lo]:.0%} of {name(lo)}’s closed issues were resolved; "
              f"the rest were duplicates or not planned. At {name(hi)} it was {share[hi]:.0%}.")
    return first, second


def render(cur, stars, backlog, s, e, as_of, days):
    first, second = findings(cur)
    heads = "".join(f'<div class="h"><span class="sw" style="background:{REPOS[k]["color"]}"></span>{esc(name(k))}</div>' for k in KEYS)
    star_cells = "".join(f'<div class="n">★ {k_short(stars[k])}</div>' for k in KEYS)
    pr_cells = "".join(f'<div class="n">{cur[k]["prs_merged"]:,}'
                       + ('<span class="note">public repo only; the product code is private</span>' if REPOS[k].get("source_public") is False else "")
                       + "</div>" for k in KEYS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Agent Harnesses on GitHub</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: {SURFACE}; }}
body {{ color: {INK}; font: 24px/1.35 "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; padding: 48px 48px 36px; display: flex; flex-direction: column; }}
.eyebrow {{ font-size: 21px; letter-spacing: .1em; text-transform: uppercase; color: {INK3}; }}
h1 {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 600; font-size: 56px; line-height: 1.08; margin-top: 8px; }}
.finds {{ margin-top: 24px; display: grid; gap: 10px; }}
.find {{ display: grid; grid-template-columns: 44px 1fr; align-items: start; padding: 14px 20px 14px 14px; background: #f3f4f6; border-radius: 14px;
        font-family: "Source Serif 4", Georgia, serif; font-size: 26px; line-height: 1.3; }}
.num {{ width: 32px; height: 32px; border-radius: 50%; background: {INK}; color: {SURFACE}; font: 600 19px/32px "IBM Plex Sans", sans-serif; text-align: center; margin-top: 2px; }}
h2 {{ font-size: 25px; font-weight: 600; margin: 26px 0 4px; display: flex; align-items: center; gap: 12px; }}
h2 .num {{ margin: 0; flex: none; }}
.sub {{ font-size: 19px; color: {INK2}; margin: 0 0 12px 44px; }}
svg text {{ font-family: "IBM Plex Sans", system-ui, sans-serif; }}
svg .ax {{ font-size: 20px; fill: {INK3}; }}
svg .end {{ font-size: 23px; font-weight: 600; fill: {INK}; }}
svg .endv {{ font-weight: 400; fill: {INK2}; }}
svg .chg {{ font-size: 19px; fill: {INK2}; }}
svg .lab {{ font-size: 24px; font-weight: 600; fill: {INK}; }}
svg .val {{ font-size: 21px; fill: {INK2}; }}
svg .val .b {{ font-weight: 600; fill: {INK}; }}
.strip {{ margin-top: 26px; border-top: 2px solid {INK}; }}
.strip .r {{ display: grid; grid-template-columns: 230px repeat({len(KEYS)}, 1fr); column-gap: 20px; padding: 10px 0; border-bottom: 1px solid {RULE}; align-items: start; }}
.strip .k {{ font-size: 21px; color: {INK2}; padding-top: 3px; }}
.strip .h {{ font-size: 22px; font-weight: 600; display: flex; align-items: center; gap: 9px; }}
.sw {{ width: 16px; height: 16px; border-radius: 4px; display: inline-block; }}
.strip .n {{ font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.15; }}
.strip .note {{ display: block; font-size: 17px; font-weight: 400; color: {INK2}; line-height: 1.25; margin-top: 2px; }}
.foot {{ margin-top: auto; font-size: 17px; line-height: 1.4; color: {INK3}; }}
</style></head><body>
<p class="eyebrow">AI coding agents on GitHub · {fdate(s)} – {fdate(e)}</p>
<h1>{" vs ".join(esc(name(k)) for k in KEYS)}</h1>
<div class="finds"><div class="find"><span class="num">1</span><span>{esc(first)}</span></div>
<div class="find"><span class="num">2</span><span>{esc(second)}</span></div></div>
<h2><span class="num">1</span>Open issues, day by day</h2>
{chart_open_issues(backlog, cur, s, e, days)}
<h2><span class="num">2</span>Issues received vs. closed, last {days} days</h2>
<p class="sub">{KEY}</p>
{chart_in_out(cur)}
<div class="strip"><div class="r"><div></div>{heads}</div>
<div class="r"><div class="k">GitHub stars</div>{star_cells}</div>
<div class="r"><div class="k">PRs merged, {days} days</div>{pr_cells}</div></div>
<p class="foot">Source: every issue and pull request of the three repositories, read from the GitHub API and checked against GitHub’s own counts. Data as of {fdate(as_of)}. Most Codex PRs are mirrored from an internal repository.</p>
</body></html>"""


def to_png(html_path):
    chrome = next((c for c in CHROME if os.path.exists(c) or shutil.which(c)), None)
    if not chrome:
        print("no Chrome or Chromium found; open the HTML and screenshot it at 1080x1350")
        return None
    png = os.path.splitext(html_path)[0] + ".png"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
                    "--force-device-scale-factor=2", "--virtual-time-budget=6000", f"--screenshot={png}",
                    "file://" + os.path.abspath(html_path)], check=True, capture_output=True)
    return png


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="ISO time the data is read at (default: last sync of every repo)")
    ap.add_argument("--days", type=int, default=30, help="period length in days (default 30)")
    ap.add_argument("--out", help="output HTML path (default reports/harness-social_<date>.html)")
    args = ap.parse_args()
    as_of = ts(args.as_of) if args.as_of else (data_as_of() or now())
    e, s = as_of, as_of - timedelta(days=args.days)

    cur, stars, backlog = {}, {}, {}
    for k in KEYS:
        issues, prs = prepare(k, "issues", as_of), prepare(k, "prs", as_of)
        team = load_team(k, prs + issues)
        cur[k] = period_stats(issues, prs, team, fixes(k, prs, as_of), releases(k, as_of), npm_daily(k, as_of), k, s, e)
        stars[k] = stars_now(k)
        backlog[k] = daily_backlog(issues, (e - timedelta(days=90)).replace(hour=0, minute=0, second=0), e)

    out = args.out or os.path.join(ROOT, "reports", f"harness-social_{as_of.strftime('%Y-%m-%d')}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(render(cur, stars, backlog, s, e, as_of, args.days))
    print(f"social -> {os.path.relpath(out, ROOT)}")
    png = to_png(out)
    if png:
        print(f"image  -> {os.path.relpath(png, ROOT)}")


if __name__ == "__main__":
    main()
