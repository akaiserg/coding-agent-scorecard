#!/usr/bin/env python3
"""Head-to-head image of two repositories for a social post: how each handles the flow of GitHub issues. The
default pair is Codex and OpenCode, the two whose product source is public.

  social_pair.py [--repos A,B] [--as-of ISO] [--days N] [--out PATH]

1. One card per repository: GitHub stars, issues closed for every 100 new ones over the last N days, and how
   its backlog of open issues changed.
2. The backlog day by day.

Writes reports/harness-social-<a>-vs-<b>_<date>.html at 1080x1350 (4:5) and its PNG, like social.py.
"""
import argparse
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


def thousands(n):
    """24,907 -> 24.9k: one decimal, so the image reads like the post."""
    return f"{n / 1e3:.1f}k" if n >= 1000 else f"{n:,}"


def card(c, k):
    """Stars, the closing pace as one big number, and the backlog change, which says what that pace did."""
    up = c["backlog_end"] > c["backlog_start"]
    color = REPOS[k]["color"]
    return (f'<div class="card" style="border-top-color:{color}">'
            f'<div class="top"><span class="sw" style="background:{color}"></span><b>{esc(name(k))}</b>'
            f'<span class="st">★ {short(c["stars"])} <span>GitHub stars</span></span></div>'
            f'<div class="big">{per100(c)}</div>'
            f'<div class="what">issues closed for every 100 new ones</div>'
            f'<div class="bl"><span style="color:{BAD if up else GOOD}">{"▲" if up else "▼"}</span> '
            f'Backlog {"up" if up else "down"} <b>{abs(c["backlog_growth"]):.0%}</b>, to {thousands(c["backlog_end"])} open issues</div>'
            f'</div>')


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
h1 {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 600; font-size: 64px; line-height: 1.05; margin-top: 6px; }}
.dek {{ font-size: 27px; color: {INK2}; margin-top: 8px; }}
.cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 30px; }}
.card {{ padding: 20px 24px 22px; border: 1px solid {RULE}; border-top: 8px solid; border-radius: 14px; }}
.top {{ display: flex; align-items: center; gap: 10px; }}
.top b {{ font-size: 27px; font-weight: 600; }}
.sw {{ display: inline-block; flex: none; width: 16px; height: 16px; border-radius: 4px; }}
.st {{ margin-left: auto; font-size: 25px; font-weight: 600; }}
.st span {{ font-size: 17px; font-weight: 400; color: {INK3}; }}
.big {{ font-size: 140px; font-weight: 600; line-height: 1; margin-top: 18px; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }}
.what {{ font-size: 22px; color: {INK2}; margin-top: 6px; }}
.bl {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid {RULE}; font-size: 22px; }}
.bl b {{ font-weight: 600; }}
.note {{ font-size: 19px; color: {INK3}; margin-top: 12px; }}
h2 {{ font-size: 25px; font-weight: 600; margin: 34px 0 12px; }}
svg text {{ font-family: "IBM Plex Sans", system-ui, sans-serif; }}
svg .ax {{ font-size: 20px; fill: {INK3}; }}
svg .end {{ font-size: 24px; font-weight: 600; fill: {INK}; }}
svg .endv {{ font-weight: 400; fill: {INK2}; }}
svg .chg {{ font-size: 20px; fill: {INK2}; }}
.foot {{ margin-top: auto; padding-top: 12px; border-top: 1px solid {RULE}; font-size: 16.5px; line-height: 1.4; color: {INK3}; }}
</style></head><body>
<p class="eyebrow">AI coding harnesses on GitHub · {fdate(s)} – {fdate(e)}</p>
<h1>{" vs ".join(esc(name(k)) for k in keys)}</h1>
<p class="dek">How they handle the never-ending flow of GitHub issues</p>
<div class="cards">{"".join(card(cur[k], k) for k in keys)}</div>
<p class="note">Last {days} days. 100 means closing issues as fast as they arrive: below it the backlog grows, above it it shrinks.</p>
<h2>Open issues, day by day</h2>
{chart_open_issues({k: backlog[k] for k in keys}, cur, s, e, days, h=520)}
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
