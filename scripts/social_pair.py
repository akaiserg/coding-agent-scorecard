#!/usr/bin/env python3
"""Head-to-head image of two repositories for a social post: how each handles the flow of GitHub issues. The
default pair is Codex and OpenCode, the two whose product source is public.

  social_pair.py [--repos A,B] [--as-of ISO] [--days N] [--out PATH]

Under the title, each repository's GitHub stars, which also serve as the color key.
1. Issues closed for every 100 new ones over the last N days, against the 100 mark where the backlog holds still.
2. The backlog (open issues) day by day.
3. How the backlog changed: open at the start + new − closed = open today.
The finding above the charts is written from the numbers on every run.

Writes reports/harness-social-<a>-vs-<b>_<date>.html at 1080x1350 (4:5) and its PNG, like social.py.
"""
import argparse
import math
import os
from datetime import timedelta

from classify import fixes, load_team, npm_daily, prepare, releases
from common import ROOT, data_as_of, now, ts
from config import REPOS
from report import BAD, GOOD, INK, INK2, INK3, RULE, SURFACE, daily_backlog, esc, fdate, name, period_stats, short
from social import H, W, chart_open_issues, to_png


def per100(c):
    """Issues closed for every 100 opened in the period."""
    return round(100 * (c["closed_per_new"] or 0))


def chart_per_100(cur, keys):
    """One bar per repo: issues closed for every 100 new ones, with a dashed mark at 100, where closes match
    arrivals. Left of the mark the backlog grows; right of it, it shrinks."""
    w, ml, mr, mt, bar, gap = 984, 170, 90, 44, 48, 18
    top = max(150, 50 * math.ceil(max(per100(cur[k]) for k in keys) / 50))
    X = lambda v: (w - ml - mr) * v / top
    yb = mt + len(keys) * (bar + gap) - gap
    h, x100 = yb + 44, ml + X(100)
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">']
    for i, k in enumerate(keys):
        y, v = mt + i * (bar + gap), per100(cur[k])
        bw = max(3, X(v))
        out.append(f'<text x="0" y="{y + bar / 2}" dy="0.35em" class="lab">{esc(name(k))}</text>'
                   f'<rect x="{ml}" y="{y}" width="{bw:.1f}" height="{bar}" rx="4" fill="{REPOS[k]["color"]}"/>'
                   f'<text x="{ml + bw + 14:.1f}" y="{y + bar / 2}" dy="0.35em" class="big">{v}</text>')
    out.append(f'<line x1="{x100:.1f}" x2="{x100:.1f}" y1="{mt - 10}" y2="{yb + 8}" stroke="{INK}" stroke-width="2" stroke-dasharray="6 5"/>'
               f'<text x="{x100:.1f}" y="{mt - 20}" text-anchor="middle" class="mark">100 = closing them as fast as they arrive</text>'
               f'<text x="{x100 - 14:.1f}" y="{yb + 32}" text-anchor="end" class="ax">◀ backlog grows</text>'
               f'<text x="{x100 + 14:.1f}" y="{yb + 32}" class="ax">backlog shrinks ▶</text>')
    out.append("</svg>")
    return "".join(out)


def backlog_sum(cur, keys, days):
    """Open issues at the start, plus new, minus closed, equals open today: one written-out sum per repo,
    so the change at the end of the line chart can be traced to the issues that came in and went out."""
    rows = []
    for k in keys:
        c = cur[k]
        a, n, x, z = c["backlog_start"], c["opened"], c["closed"], c["backlog_end"]
        if a + n - x != z:
            print(f"warning: {name(k)} open issues do not add up ({a:,} + {n:,} − {x:,} ≠ {z:,})")
        up = z > a
        rows.append(f'<tr><th><span class="sw" style="background:{REPOS[k]["color"]}"></span>{esc(name(k))}</th>'
                    f'<td>{a:,}</td><td class="op">+</td><td>{n:,}</td><td class="op">−</td><td>{x:,}</td>'
                    f'<td class="op">=</td><td class="b">{z:,}</td>'
                    f'<td class="chg"><span style="color:{BAD if up else GOOD}">{"▲" if up else "▼"}</span> '
                    f'{"up" if up else "down"} {abs(c["backlog_growth"]):.0%}</td></tr>')
    return (f'<table class="eq"><tr class="hd"><th></th><th>{days} days ago</th><th></th><th>new</th><th></th>'
            f'<th>closed</th><th></th><th>today</th><th></th></tr>{"".join(rows)}</table>')


def stars(cur, keys):
    """Each repository's GitHub stars, with its color swatch, so the strip is also the key to the charts."""
    return '<div class="stars">' + "".join(
        f'<div class="star"><span class="sw" style="background:{REPOS[k]["color"]}"></span><b>{esc(name(k))}</b>'
        f'<span class="sv">★ {short(cur[k]["stars"])}</span><span class="sl">GitHub stars</span></div>' for k in keys) + "</div>"


def finding(cur, keys, days):
    """Who keeps up with their issues, in the words of the post that goes with the image."""
    best = min(keys, key=lambda k: cur[k]["backlog_growth"] or 0)
    worst = next(k for k in keys if k != best)
    b, wc = cur[best], cur[worst]
    return (f"Over the last {days} days, {name(best)} closed {per100(b)} issues for every 100 new ones, "
            f"{'cutting' if b['backlog_growth'] < 0 else 'growing'} its backlog by {abs(b['backlog_growth']):.0%}. "
            f"{name(worst)} closed {'just ' if per100(wc) < 100 else ''}{per100(wc)} per 100, and its backlog "
            f"{'grew' if wc['backlog_growth'] > 0 else 'shrank'} {abs(wc['backlog_growth']):.0%}, reaching {wc['backlog_end'] / 1e3:.1f}k issues.")


def render(cur, backlog, keys, s, e, as_of, days):
    left_out = [f"{esc(name(k))} is not included: its product source code is not public."
                for k in REPOS if k not in keys and REPOS[k].get("source_public") is False]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{esc(" vs ".join(name(k) for k in keys))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: {SURFACE}; }}
body {{ color: {INK}; font: 24px/1.35 "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; padding: 46px 48px 34px; display: flex; flex-direction: column; }}
.eyebrow {{ font-size: 20px; letter-spacing: .1em; text-transform: uppercase; color: {INK3}; }}
h1 {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 600; font-size: 60px; line-height: 1.05; margin-top: 6px; }}
.dek {{ font-size: 26px; color: {INK2}; margin-top: 6px; }}
.stars {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 18px; }}
.star {{ display: flex; align-items: baseline; gap: 10px; padding: 10px 18px; border: 1px solid {RULE}; border-radius: 14px; }}
.star b {{ font-size: 24px; font-weight: 600; }}
.star .sv {{ margin-left: auto; font-size: 32px; font-weight: 600; }}
.star .sl {{ font-size: 18px; color: {INK3}; }}
.star .sw {{ margin-right: 2px; }}
.sw {{ display: inline-block; flex: none; width: 16px; height: 16px; border-radius: 4px; margin-right: 10px; }}
.find {{ margin-top: 14px; padding: 16px 22px; background: #f3f4f6; border-radius: 14px;
        font-family: "Source Serif 4", Georgia, serif; font-size: 27px; line-height: 1.3; }}
h2 {{ font-size: 25px; font-weight: 600; margin: 28px 0 10px; }}
svg text {{ font-family: "IBM Plex Sans", system-ui, sans-serif; }}
svg .ax {{ font-size: 20px; fill: {INK3}; }}
svg .end {{ font-size: 23px; font-weight: 600; fill: {INK}; }}
svg .endv {{ font-weight: 400; fill: {INK2}; }}
svg .chg {{ font-size: 19px; fill: {INK2}; }}
svg .lab {{ font-size: 23px; font-weight: 600; fill: {INK}; }}
svg .big {{ font-size: 34px; font-weight: 600; fill: {INK}; }}
svg .mark {{ font-size: 19px; fill: {INK2}; }}
.eq {{ border-collapse: collapse; font-variant-numeric: tabular-nums; }}
.eq th, .eq td {{ padding: 6px 0; text-align: right; white-space: nowrap; }}
.eq .hd th {{ font-size: 18px; font-weight: 400; color: {INK3}; padding: 0; }}
.eq tr > th:first-child {{ text-align: left; font-size: 23px; font-weight: 600; padding-right: 30px; }}
.eq td {{ font-size: 34px; color: {INK2}; }}
.eq td.op {{ color: {INK3}; font-size: 30px; padding: 6px 20px; text-align: center; }}
.eq td.b {{ font-weight: 600; color: {INK}; }}
.eq td.chg {{ font-size: 21px; padding-left: 26px; text-align: left; }}
.gap {{ height: 14px; }}
.foot {{ margin-top: auto; padding-top: 12px; border-top: 1px solid {RULE}; font-size: 16.5px; line-height: 1.4; color: {INK3}; }}
</style></head><body>
<p class="eyebrow">AI coding harnesses on GitHub · {fdate(s)} – {fdate(e)}</p>
<h1>{" vs ".join(esc(name(k)) for k in keys)}</h1>
<p class="dek">How they handle the never-ending flow of GitHub issues</p>
{stars(cur, keys)}
<p class="find">{esc(finding(cur, keys, days))}</p>
<h2>Issues closed for every 100 new ones, last {days} days</h2>
{chart_per_100(cur, keys)}
<h2>Backlog: open issues, day by day</h2>
{chart_open_issues({k: backlog[k] for k in keys}, cur, s, e, days, h=230)}
<h2>How the backlog changed</h2>
{backlog_sum(cur, keys, days)}
<div class="gap"></div>
<p class="foot">Source: every issue of {" and ".join(esc(REPOS[k]["repo"]) for k in keys)}, read from the GitHub API and checked against GitHub’s own counts. Closed counts every close, including duplicates and issues closed as not planned. {" ".join(left_out)} Data as of {fdate(as_of)}.</p>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", default="codex,opencode", help="two repository keys from config.py (default codex,opencode)")
    ap.add_argument("--as-of", help="ISO time the data is read at (default: last sync of every repo)")
    ap.add_argument("--days", type=int, default=30, help="period length in days (default 30)")
    ap.add_argument("--out", help="output HTML path (default reports/harness-social-<a>-vs-<b>_<date>.html)")
    args = ap.parse_args()
    keys = args.repos.split(",")
    if len(keys) != 2 or any(k not in REPOS for k in keys):
        ap.error(f"--repos takes two of: {', '.join(REPOS)}")
    as_of = ts(args.as_of) if args.as_of else (data_as_of() or now())
    e, s = as_of, as_of - timedelta(days=args.days)

    cur, backlog = {}, {}
    for k in keys:
        issues, prs = prepare(k, "issues", as_of), prepare(k, "prs", as_of)
        team = load_team(k, prs + issues)
        cur[k] = period_stats(issues, prs, team, fixes(k, prs, as_of), releases(k, as_of), npm_daily(k, as_of), k, s, e)
        backlog[k] = daily_backlog(issues, (e - timedelta(days=90)).replace(hour=0, minute=0, second=0), e)

    out = args.out or os.path.join(ROOT, "reports", f"harness-social-{keys[0]}-vs-{keys[1]}_{as_of:%Y-%m-%d}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(render(cur, backlog, keys, s, e, as_of, args.days))
    print(f"social -> {os.path.relpath(out, ROOT)}")
    png = to_png(out)
    if png:
        print(f"image  -> {os.path.relpath(png, ROOT)}")


if __name__ == "__main__":
    main()
