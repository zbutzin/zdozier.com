"""GitHub REST: public repositories owned by identity.github_user."""
from __future__ import annotations

import os

from common import as_list

NAME = "github"
BASE = "https://api.github.com"
KEEP = ["name", "full_name", "description", "html_url", "homepage", "language",
        "stargazers_count", "forks_count", "pushed_at", "created_at", "updated_at", "topics",
        "fork", "archived", "license"]


def fetch(ctx):
    http, ident = ctx["http"], ctx["ident"]
    user = ident.get("github_user")
    if not user:
        raise ValueError("identity.github_user is not set")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    repos, page = [], 1
    while page <= 10:
        batch = http.get_json("%s/users/%s/repos" % (BASE, user), headers=headers,
                              params={"per_page": 100, "type": "owner", "sort": "pushed",
                                      "page": page})
        batch = as_list(batch)
        for r in batch:
            if r.get("private"):
                continue
            rec = {k: r.get(k) for k in KEEP}
            lic = r.get("license") or {}
            rec["license"] = lic.get("spdx_id") if isinstance(lic, dict) else None
            rec["topics"] = as_list(r.get("topics"))
            repos.append(rec)
        if len(batch) < 100:
            break
        page += 1
    payload = {"source": NAME, "fetched_at": ctx["now"], "user": user, "repos": repos}
    return payload, len(repos)
