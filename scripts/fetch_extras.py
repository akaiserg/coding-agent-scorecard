#!/usr/bin/env python3
"""Fetch the small, fully refreshed inputs: repo totals, releases, search counts, CHANGELOG, npm dates and downloads.

Writes into data/raw/: repo_info.jsonl, KEY_releases.jsonl, search_extras.jsonl, KEY_npm_downloads.json,
KEY_main_commits.json (commits landing on the default branch per month),
and for repos configured with a changelog, KEY_CHANGELOG.md and KEY_npm_time.json. Appends the current
star and fork counts to stars_history.jsonl, since GitHub does not list when each star was given.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

from common import RAW, graphql, iso, log, now, rest
from config import REPOS

REPO_INFO = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    nameWithOwner createdAt stargazerCount forkCount
    watchers { totalCount }
    primaryLanguage { name }
    licenseInfo { spdxId }
    issues { totalCount }
    openIssues: issues(states:OPEN) { totalCount }
    pullRequests { totalCount }
    openPRs: pullRequests(states:OPEN) { totalCount }
    mergedPRs: pullRequests(states:MERGED) { totalCount }
    releases { totalCount }
    latestRelease { tagName publishedAt }
  }
}"""

RELEASES = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    releases(first:100, after:$cursor, orderBy:{field:CREATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { tagName name createdAt publishedAt isPrerelease isDraft }
    }
  }
}"""


def search_count(q):
    return rest("search/issues", {"q": q, "per_page": 1})["total_count"]


def main_commits(owner, name, start):
    """Commits on the default branch per calendar month since `start`, counted by GitHub in one query."""
    months, y, m, today = [], start.year, start.month, now().date()
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    fields = " ".join(
        f'm{y}_{m:02d}: history(since:"{y}-{m:02d}-01T00:00:00Z", until:"{y + (m == 12)}-{m % 12 + 1:02d}-01T00:00:00Z") {{ totalCount }}'
        for y, m in months)
    q = f"""query($owner:String!, $name:String!) {{ repository(owner:$owner, name:$name) {{
      defaultBranchRef {{ name target {{ ... on Commit {{ {fields} }} }} }} }} }}"""
    ref = graphql(q, {"owner": owner, "name": name}, f"{name} main commits")["data"]["repository"]["defaultBranchRef"]
    return {"branch": ref["name"],
            "months": {f"{y}-{m:02d}": ref["target"][f"m{y}_{m:02d}"]["totalCount"] for y, m in months}}


def npm_downloads(package, start):
    """Daily downloads from `start` to today, a year per request (the API serves at most 18 months)."""
    days, a, today = {}, start, now().date()
    while a <= today:
        b = min(a + timedelta(days=364), today)
        url = f"https://api.npmjs.org/downloads/range/{a}:{b}/" + urllib.parse.quote(package, safe="@/")
        with urllib.request.urlopen(url, timeout=120) as resp:
            days.update({d["day"]: d["downloads"] for d in json.loads(resp.read()).get("downloads", [])})
        a = b + timedelta(days=1)
    return days


def main():
    os.makedirs(RAW, exist_ok=True)
    infos, extras = [], []
    for key, cfg in REPOS.items():
        repo = cfg["repo"]
        owner, name = repo.split("/")
        infos.append(graphql(REPO_INFO, {"owner": owner, "name": name}, f"{key} info")["data"]["repository"])
        # Who may open PRs (e.g. collaborators_only) is only in the REST view of the repo.
        infos[-1]["prCreationPolicy"] = rest(f"repos/{repo}").get("pull_request_creation_policy")
        with open(os.path.join(RAW, "stars_history.jsonl"), "a") as f:
            f.write(json.dumps({"repo": key, "at": iso(now()), "stars": infos[-1]["stargazerCount"],
                                "forks": infos[-1]["forkCount"]}) + "\n")

        releases, cursor = [], None
        while True:
            page = graphql(RELEASES, {"owner": owner, "name": name, "cursor": cursor}, f"{key} releases")["data"]["repository"]["releases"]
            releases += [r for r in page["nodes"] if r]
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        with open(os.path.join(RAW, f"{key}_releases.jsonl"), "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in releases)

        top = rest("search/issues", {"q": f"repo:{repo} is:issue is:open", "sort": "reactions", "order": "desc", "per_page": 10})
        extras.append({
            "repo": repo,
            "open_zero_comments": search_count(f"repo:{repo} is:issue is:open comments:0"),
            "open_gt10_comments": search_count(f"repo:{repo} is:issue is:open comments:>10"),
            "top_open_by_reactions": [
                {"number": i["number"], "title": i["title"], "reactions": i["reactions"]["total_count"],
                 "comments": i["comments"], "created": i["created_at"][:10], "labels": [l["name"] for l in i["labels"]]}
                for i in top["items"]
            ],
        })

        if cfg.get("changelog_path"):
            text = rest(f"repos/{repo}/contents/{cfg['changelog_path']}", accept="application/vnd.github.raw", raw=True)
            open(os.path.join(RAW, f"{key}_CHANGELOG.md"), "w").write(text)
        if cfg.get("npm_package"):
            url = "https://registry.npmjs.org/" + urllib.parse.quote(cfg["npm_package"], safe="@")
            with urllib.request.urlopen(url, timeout=120) as resp:
                times = json.loads(resp.read())["time"]
            json.dump(times, open(os.path.join(RAW, f"{key}_npm_time.json"), "w"), indent=1)
        commits = main_commits(owner, name, date.fromisoformat(infos[-1]["createdAt"][:10]))
        json.dump(commits, open(os.path.join(RAW, f"{key}_main_commits.json"), "w"))
        if cfg.get("downloads_package"):
            dl = npm_downloads(cfg["downloads_package"], date.fromisoformat(infos[-1]["createdAt"][:10]))
            json.dump(dl, open(os.path.join(RAW, f"{key}_npm_downloads.json"), "w"))
        log(f"{key}: {len(releases)} releases, {extras[-1]['open_zero_comments']} open issues without comments")

    with open(os.path.join(RAW, "repo_info.jsonl"), "w") as f:
        f.writelines(json.dumps(i) + "\n" for i in infos)
    with open(os.path.join(RAW, "search_extras.jsonl"), "w") as f:
        f.writelines(json.dumps(e) + "\n" for e in extras)


if __name__ == "__main__":
    main()
