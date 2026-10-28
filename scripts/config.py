"""Which repositories to compare and how their issues are classified.

Edit REPOS to add or swap a harness; every other script reads this file. `downloads_package` is the
npm package whose daily downloads serve as the usage signal (installs through native installers,
Homebrew or release binaries are not counted, and CI installs are). The report colors are the
first three slots of a colorblind-checked categorical palette; a fourth repo should take #eda100.
"""

REPOS = {
    "claude-code": {
        "repo": "anthropics/claude-code",
        "color": "#2a78d6",
        "name": "Claude Code",
        # The product source is not in this repository (it holds plugins, examples and the issue tracker), so
        # its PRs say little about delivery; fixes are counted from CHANGELOG "Fixed" entries dated by npm publish time.
        "source_public": False,
        "fix_source": "changelog",
        "changelog_path": "CHANGELOG.md",
        "npm_package": "@anthropic-ai/claude-code",
        # GitHub releases only start in Dec 2025; count changelog versions instead.
        "versions_from_changelog": True,
        "bug_labels": True,
        "downloads_package": "@anthropic-ai/claude-code",
    },
    "codex": {
        "repo": "openai/codex",
        "color": "#eb6834",
        "name": "Codex",
        "fix_source": "prs",
        "bug_labels": True,
        "downloads_package": "@openai/codex",
    },
    "opencode": {
        "repo": "anomalyco/opencode",
        "color": "#1baf7a",
        "name": "OpenCode",
        "fix_source": "prs",
        # No type labels on issues; bugs are estimated from title wording.
        "bug_labels": False,
        "downloads_package": "opencode-ai",
    },
}

# Accounts that mirror commits from an internal monorepo; typed as User but not community work.
SYNC_ACCOUNTS = {"copyberry"}

# Labels that triage automation applies before closing an issue without a fix.
AUTO_CLOSE_LABELS = {"stale", "autoclose", "pending close"}

# Labels that describe triage state rather than what an issue is about.
META_LABELS = {
    "bug", "enhancement", "duplicate", "stale", "autoclose", "invalid", "has repro", "needs-repro",
    "needs-info", "question", "reproduced", "triaging", "pending close", "oncall", "external",
}

# GitHub author associations that mark someone as part of the maintaining team.
MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# Incremental runs re-read this much before the last sync, to cover items edited mid-fetch.
SYNC_OVERLAP_HOURS = 1
