#!/usr/bin/env python3
"""Fetch issue or PR metadata for one repo from the GitHub GraphQL API into JSON lines.

  fetch_items.py KEY issues|prs --full [--fresh]   every item; resumable
  fetch_items.py KEY issues|prs --update [--since ISO]
                                                   only items updated since the last sync

A full fetch walks the repo from both ends at once (oldest first and newest first, meeting at the
middle number) and writes data/raw/KEY_KIND_{asc,desc}.jsonl. An update writes
data/raw/updates/KEY_KIND_<stamp>.jsonl, which later loads on top of the full fetch.
"""
import argparse
import fcntl
import json
import os
import sys
import threading
from datetime import timedelta

from common import RAW, UPDATES, graphql, iso, log, now, read_state, record_sync, ts, update_files, wait_for_rate_limit
from config import REPOS, SYNC_OVERLAP_HOURS

ISSUE_FIELDS = """
number title createdAt updatedAt closedAt state stateReason authorAssociation
author { login __typename }
labels(first:15) { nodes { name } }
comments { totalCount }
reactions { totalCount }
"""

PR_FIELDS = """
number title createdAt updatedAt closedAt mergedAt state isDraft authorAssociation baseRefName
additions deletions changedFiles
author { login __typename }
mergedBy { login }
labels(first:10) { nodes { name } }
closingIssuesReferences(first:5) { nodes { number } }
"""


def connection(kind):
    return "issues" if kind == "issues" else "pullRequests"


def page_query(kind, order_field, direction, since_filter=False):
    fields = ISSUE_FIELDS if kind == "issues" else PR_FIELDS
    extra = ", filterBy:{since:$since}" if since_filter else ""
    since_var = ", $since:DateTime" if since_filter else ""
    return f"""
query($owner:String!, $name:String!, $cursor:String{since_var}) {{
  rateLimit {{ remaining resetAt }}
  repository(owner:$owner, name:$name) {{
    {connection(kind)}(first:100, after:$cursor, orderBy:{{field:{order_field}, direction:{direction}}}{extra}) {{
      pageInfo {{ endCursor hasNextPage }}
      nodes {{ {fields} }}
    }}
  }}
}}"""


def newest_number(owner, name, kind):
    q = f"""query($owner:String!, $name:String!) {{ repository(owner:$owner, name:$name) {{
      {connection(kind)}(first:1, orderBy:{{field:CREATED_AT, direction:DESC}}) {{ nodes {{ number }} }} }} }}"""
    nodes = graphql(q, {"owner": owner, "name": name})["data"]["repository"][connection(kind)]["nodes"]
    return nodes[0]["number"] if nodes else 0


def walk(owner, name, kind, direction, cutoff, out):
    """One half of a full fetch. Stops once a whole page is past the cutoff number."""
    label = os.path.basename(out)
    lock = open(out + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"{label}: already running in another process, skipping")
        return False
    state_file = out + ".cursor"
    cursor = open(state_file).read().strip() if os.path.exists(state_file) else None
    if cursor == "DONE":
        return True
    q = page_query(kind, "CREATED_AT", direction)
    pages = 0
    while True:
        data = graphql(q, {"owner": owner, "name": name, "cursor": cursor}, label)
        page = data["data"]["repository"][connection(kind)]
        nodes = [n for n in page["nodes"] if n]
        with open(out, "a") as f:
            for n in nodes:
                f.write(json.dumps(n) + "\n")
        cursor = page["pageInfo"]["endCursor"]
        pages += 1
        nums = [n["number"] for n in nodes]
        crossed = bool(nums) and (min(nums) > cutoff if direction == "ASC" else max(nums) < cutoff)
        if not page["pageInfo"]["hasNextPage"] or crossed:
            open(state_file, "w").write("DONE")
            log(f"{label}: done after {pages} pages")
            return True
        open(state_file, "w").write(cursor)
        if pages % 25 == 0:
            log(f"{label}: {pages} pages, at {nodes[-1]['createdAt'] if nodes else '?'}")
        wait_for_rate_limit(data["data"]["rateLimit"], label)


def full(key, kind, fresh):
    owner, name = REPOS[key]["repo"].split("/")
    outs = {d: os.path.join(RAW, f"{key}_{kind}_{d.lower()}.jsonl") for d in ("ASC", "DESC")}
    started_file = os.path.join(RAW, f"{key}_{kind}.full_started")
    if fresh:
        stale = [started_file] + [p for out in outs.values() for p in (out, out + ".cursor")] + update_files(key, kind)
        if kind == "prs":
            stale.append(os.path.join(RAW, f"{key}_mergers.jsonl"))
        for path in stale:
            if os.path.exists(path):
                os.remove(path)
    if not os.path.exists(started_file):
        open(started_file, "w").write(iso(now()))
    cutoff = newest_number(owner, name, kind) // 2
    log(f"{key} {kind}: full fetch, splitting at #{cutoff}")
    results = {}
    threads = [threading.Thread(target=lambda d=d: results.__setitem__(d, walk(owner, name, kind, d, cutoff, outs[d])))
               for d in ("ASC", "DESC")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if all(results.values()):
        # Anything edited after the fetch began may be stale, so the next update starts from then.
        record_sync(key, kind, ts(open(started_file).read().strip()))


def update(key, kind, since):
    owner, name = REPOS[key]["repo"].split("/")
    if since is None:
        last = read_state().get(key, {}).get(kind)
        if not last:
            sys.exit(f"{key} {kind}: no previous sync recorded; run with --full first")
        since = ts(last) - timedelta(hours=SYNC_OVERLAP_HOURS)
    started = now()
    os.makedirs(UPDATES, exist_ok=True)
    out = os.path.join(UPDATES, f"{key}_{kind}_{started.strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    # Issues can be filtered server-side; PRs are read newest-updated first until older than `since`.
    q = page_query(kind, "UPDATED_AT", "DESC", since_filter=(kind == "issues"))
    variables = {"owner": owner, "name": name, "cursor": None}
    if kind == "issues":
        variables["since"] = iso(since)
    count, pages = 0, 0
    with open(out, "w") as f:
        while True:
            data = graphql(q, variables, os.path.basename(out))
            page = data["data"]["repository"][connection(kind)]
            nodes = [n for n in page["nodes"] if n]
            fresh_nodes = [n for n in nodes if ts(n["updatedAt"]) >= since]
            for n in fresh_nodes:
                f.write(json.dumps(n) + "\n")
            count += len(fresh_nodes)
            pages += 1
            if not page["pageInfo"]["hasNextPage"] or len(fresh_nodes) < len(nodes):
                break
            variables["cursor"] = page["pageInfo"]["endCursor"]
            wait_for_rate_limit(data["data"]["rateLimit"], os.path.basename(out))
    log(f"{key} {kind}: {count} items updated since {iso(since)} ({pages} pages)")
    record_sync(key, kind, started)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", choices=sorted(REPOS))
    ap.add_argument("kind", choices=["issues", "prs"])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="fetch every item (resumes an interrupted run)")
    mode.add_argument("--update", action="store_true", help="fetch items updated since the last sync")
    ap.add_argument("--fresh", action="store_true", help="with --full: discard earlier data and start over")
    ap.add_argument("--since", help="with --update: ISO time to start from instead of the last sync")
    args = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    if args.full:
        full(args.key, args.kind, args.fresh)
    else:
        update(args.key, args.kind, ts(args.since) if args.since else None)


if __name__ == "__main__":
    main()
