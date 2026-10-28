#!/usr/bin/env bash
# Refresh the GitHub data and rebuild every output: metrics, dashboard, executive report, overview, social image,
# and the comparison of agent PRs across GitHub.
#
#   scripts/run.sh                    incremental refresh (full fetch for any repo never fetched), then build
#   scripts/run.sh --full             finish or resume a full fetch of every repo, then build
#   scripts/run.sh --fresh            discard fetched data and download everything again (~40 min)
#   scripts/run.sh --no-fetch         rebuild outputs from the data already on disk
#   scripts/run.sh --no-agents        skip the agent PR comparison (its sample takes ~30 min and is redone weekly)
#
# Report options (passed to report.py):
#   --days N                          report period: the last N days (default 30)
#   --month YYYY-MM                   report period: one calendar month
#   --as-of ISO                       read the data as of this time instead of the last sync
set -euo pipefail
cd "$(dirname "$0")"
self="$PWD/$(basename "$0")"

mode=update
agents=yes
report_args=()
analyze_args=()
overview_args=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --full) mode=full ;;
    --fresh) mode=fresh ;;
    --no-fetch) mode=none ;;
    --no-agents) agents=no ;;
    --days|--month) report_args+=("$1" "$2"); shift ;;
    --as-of) report_args+=("$1" "$2"); analyze_args+=("$1" "$2"); overview_args+=("$1" "$2"); shift ;;
    -h|--help) sed -n '2,14p' "$self" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not signed in: run 'gh auth login'" >&2; exit 1; }

if [[ $mode != none ]]; then
  # data/ is not in the repository, so a fresh clone starts without it.
  mkdir -p ../data
  # One line per repo and kind: incremental where a previous sync exists, otherwise a full fetch.
  python3 - "$mode" > ../data/fetch_jobs.txt <<'EOF'
import sys
from common import read_state
from config import REPOS
mode, state = sys.argv[1], read_state()
for key in REPOS:
    for kind in ("issues", "prs"):
        synced = state.get(key, {}).get(kind)
        flag = {"fresh": "--full --fresh", "full": "--full"}.get(mode) or ("--update" if synced else "--full")
        print(key, kind, flag)
EOF
  echo "== fetching issues and PRs"
  cat ../data/fetch_jobs.txt
  # Two streams at a time (a full fetch runs two requests each) stays clear of GitHub's secondary rate limits.
  xargs -P 2 -L 1 python3 fetch_items.py < ../data/fetch_jobs.txt
  echo "== fetching releases, repo totals, search counts, changelog"
  python3 fetch_extras.py
fi

echo "== analyzing"
python3 analyze.py "${analyze_args[@]+"${analyze_args[@]}"}"
python3 export_dashboard.py
python3 ../dashboard/build.py
echo "== executive report"
python3 report.py "${report_args[@]+"${report_args[@]}"}"
echo "== overview"
python3 overview.py "${overview_args[@]+"${overview_args[@]}"}"
echo "== social image"
python3 social.py "${overview_args[@]+"${overview_args[@]}"}"
python3 social_pair.py "${overview_args[@]+"${overview_args[@]}"}"
echo "== checking the numbers against GitHub's own counts"
python3 validate.py "${overview_args[@]+"${overview_args[@]}"}" || echo "some numbers differ from GitHub by more than 1%; see above"
if [[ $agents == yes ]]; then
  echo "== agent PRs across GitHub"
  # The cohort moves slowly, so the sample is redone once a week; other runs rebuild the page from it.
  if [[ $mode == none ]]; then agents_args=(--no-fetch); else agents_args=(--reuse-days 6); fi
  python3 agents.py "${agents_args[@]}" || echo "the agent PR sample disagrees with GitHub's counts; see above"
fi
