"""NIH RePORTER v2 project search, at most one request per second.

Queries: configured core project numbers, PI profile ids (configured plus any
resolved from earlier results), and PI names. Name and profile hits are kept
only when a PI on the record passes the "me" rule."""
from __future__ import annotations

import re

from common import as_dict, as_list, is_me, make_author

NAME = "reporter"
URL = "https://api.reporter.nih.gov/v2/projects/search"
HOST = "api.reporter.nih.gov"
LIMIT = 500
MAX_PAGES = 10


def core_project(num):
    """'1K01AI182501-01' or '5K01AI182501-04' -> 'K01AI182501'."""
    if not num:
        return None
    s = re.sub(r"\s+", "", str(num)).upper()
    m = re.match(r"^\d?([A-Z][A-Z0-9]{2}[A-Z]{2}\d{6})", s)
    return m.group(1) if m else s


def _trim(p):
    org = p.get("organization") or {}
    ic = p.get("agency_ic_admin") or {}
    pis = []
    for x in as_list(p.get("principal_investigators")):
        pis.append({"full_name": x.get("full_name"), "first_name": x.get("first_name"),
                    "middle_name": x.get("middle_name"), "last_name": x.get("last_name"),
                    "profile_id": x.get("profile_id"), "is_contact_pi": x.get("is_contact_pi")})
    return {
        "appl_id": p.get("appl_id"),
        "project_num": p.get("project_num"),
        "core_project_num": p.get("core_project_num") or core_project(p.get("project_num")),
        "fiscal_year": p.get("fiscal_year"),
        "project_title": p.get("project_title"),
        "project_start_date": p.get("project_start_date"),
        "project_end_date": p.get("project_end_date"),
        "budget_start": p.get("budget_start"),
        "budget_end": p.get("budget_end"),
        "award_amount": p.get("award_amount"),
        "activity_code": p.get("activity_code"),
        "funding_mechanism": p.get("funding_mechanism"),
        "agency_code": p.get("agency_code"),
        "agency_ic_admin": {"abbreviation": ic.get("abbreviation"), "name": ic.get("name")},
        "organization": {"org_name": org.get("org_name"), "org_city": org.get("org_city"),
                         "org_state": org.get("org_state")},
        "principal_investigators": pis,
        "project_detail_url": p.get("project_detail_url"),
        "is_active": p.get("is_active"),
    }


def pi_is_me(pi, me_cfg):
    a = make_author(given=pi.get("first_name") or None, family=pi.get("last_name") or None,
                    me_cfg=me_cfg)
    return is_me(a, me_cfg)


def _search(http, criteria):
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        res = http.post_json(URL, {"criteria": criteria, "offset": offset, "limit": LIMIT})
        batch = as_list(res.get("results"))
        out.extend(_trim(p) for p in batch)
        total = as_dict(res.get("meta")).get("total") or 0
        offset += len(batch)
        if not batch or offset >= total:
            break
    return out


def fetch(ctx):
    http, ident = ctx["http"], ctx["ident"]
    http.min_interval[HOST] = 1.05  # <= 1 request per second
    cfg = as_dict(ident.get("nih_reporter"))
    me_cfg = ident["me"]
    prev = as_dict(ctx.get("prev"))
    projects, queries = {}, []

    def add(items, require_me):
        for p in items:
            if require_me and not any(pi_is_me(x, me_cfg) for x in p["principal_investigators"]):
                continue
            projects[p["appl_id"]] = p

    cores = [core_project(c) for c in as_list(cfg.get("core_projects")) if c]
    if cores:
        queries.append({"project_nums": cores})
        add(_search(http, {"project_nums": cores}), require_me=False)

    profile_ids = set()
    for v in as_list(cfg.get("pi_profile_ids")) + as_list(prev.get("pi_profile_ids")):
        try:
            profile_ids.add(int(v))
        except (TypeError, ValueError):
            pass
    for p in projects.values():
        for x in p["principal_investigators"]:
            if x.get("profile_id") and pi_is_me(x, me_cfg):
                profile_ids.add(int(x["profile_id"]))
    if profile_ids:
        crit = {"pi_profile_ids": sorted(profile_ids)}
        queries.append(crit)
        add(_search(http, crit), require_me=True)

    for name in as_list(cfg.get("pi_names")):
        if not isinstance(name, dict) or not (name.get("last_name") or name.get("any_name")):
            continue
        crit = {"pi_names": [{k: v for k, v in name.items() if v}]}
        queries.append(crit)
        add(_search(http, crit), require_me=True)

    for p in projects.values():
        for x in p["principal_investigators"]:
            if x.get("profile_id") and pi_is_me(x, me_cfg):
                profile_ids.add(int(x["profile_id"]))

    rows = sorted(projects.values(), key=lambda p: (p.get("core_project_num") or "",
                                                     p.get("fiscal_year") or 0,
                                                     p.get("appl_id") or 0))
    payload = {"source": NAME, "fetched_at": ctx["now"], "queries": queries,
               "pi_profile_ids": sorted(profile_ids), "projects": rows}
    return payload, len({p.get("core_project_num") for p in rows})
