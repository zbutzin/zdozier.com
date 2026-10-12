"""ORCID public API v3.0: works (summaries + bulk details) and fundings."""
from __future__ import annotations

from common import (as_list, normalize_doi, normalize_orcid, normalize_pmcid, normalize_pmid,
                    partial_date)

NAME = "orcid"
BASE = "https://pub.orcid.org/v3.0"
HEADERS = {"Accept": "application/json"}
BULK_MAX = 100


def _val(x):
    if isinstance(x, dict):
        return x.get("value")
    return x


def _date(d):
    if not isinstance(d, dict):
        return None
    return partial_date(_val(d.get("year")), _val(d.get("month")), _val(d.get("day")))


def _ext_ids(obj):
    out = []
    for e in as_list(((obj or {}).get("external-ids") or {}).get("external-id")):
        if not isinstance(e, dict):
            continue
        out.append({"type": (e.get("external-id-type") or "").lower(),
                    "value": e.get("external-id-value"),
                    "relationship": e.get("external-id-relationship")})
    return out


def _work_record(w, group_index):
    title = ((w.get("title") or {}).get("title") or {}).get("value")
    subtitle = ((w.get("title") or {}).get("subtitle") or {})
    subtitle = subtitle.get("value") if isinstance(subtitle, dict) else None
    ids = _ext_ids(w)
    dois, pmid, pmcid = [], None, None
    for e in ids:
        if e["relationship"] not in (None, "self"):
            continue
        if e["type"] == "doi":
            d = normalize_doi(e["value"])
            if d and d not in dois:
                dois.append(d)
        elif e["type"] == "pmid" and not pmid:
            pmid = normalize_pmid(e["value"])
        elif e["type"] == "pmc" and not pmcid:
            pmcid = normalize_pmcid(e["value"])
    contributors = []
    for c in as_list((w.get("contributors") or {}).get("contributor")):
        name = _val(c.get("credit-name"))
        if not name:
            continue
        orcid = None
        co = c.get("contributor-orcid")
        if isinstance(co, dict):
            orcid = normalize_orcid(co.get("path") or co.get("uri"))
        attrs = c.get("contributor-attributes") or {}
        contributors.append({"name": name, "orcid": orcid,
                             "sequence": attrs.get("contributor-sequence"),
                             "role": attrs.get("contributor-role")})
    src = ((w.get("source") or {}).get("source-name") or {})
    return {
        "put_code": w.get("put-code"),
        "group": group_index,
        "type": w.get("type"),
        "title": title,
        "subtitle": subtitle,
        "journal_title": _val(w.get("journal-title")),
        "date": _date(w.get("publication-date")),
        "dois": dois,
        "pmid": pmid,
        "pmcid": pmcid,
        "url": _val(w.get("url")),
        "source_name": _val(src),
        "contributors": contributors,
    }


def fetch(ctx):
    http = ctx["http"]
    orcid = ctx["ident"]["orcid"]
    if not orcid:
        raise ValueError("identity.orcid is not set in content/site.yml")
    summary = http.get_json("%s/%s/works" % (BASE, orcid), headers=HEADERS)
    groups = as_list(summary.get("group"))
    put_codes, summaries = [], {}
    for gi, g in enumerate(groups):
        for s in as_list(g.get("work-summary")):
            pc = s.get("put-code")
            if pc is None:
                continue
            put_codes.append(pc)
            summaries[pc] = (gi, s)

    details = {}
    for i in range(0, len(put_codes), BULK_MAX):
        chunk = put_codes[i:i + BULK_MAX]
        bulk = http.get_json("%s/%s/works/%s" % (BASE, orcid, ",".join(str(p) for p in chunk)),
                             headers=HEADERS)
        for item in as_list(bulk.get("bulk")):
            w = item.get("work") if isinstance(item, dict) else None
            if isinstance(w, dict) and w.get("put-code") is not None:
                details[w["put-code"]] = w

    works = []
    for pc in put_codes:
        gi, s = summaries[pc]
        w = details.get(pc) or s  # fall back to the summary if bulk detail failed
        rec = _work_record(w, gi)
        if not rec["dois"]:
            rec["dois"] = _work_record(s, gi)["dois"]
        works.append(rec)

    fundings = []
    fsum = http.get_json("%s/%s/fundings" % (BASE, orcid), headers=HEADERS)
    for g in as_list(fsum.get("group")):
        for s in as_list(g.get("funding-summary")):
            org = s.get("organization") or {}
            fundings.append({
                "put_code": s.get("put-code"),
                "type": s.get("type"),
                "title": ((s.get("title") or {}).get("title") or {}).get("value"),
                "organization": org.get("name"),
                "start": _date(s.get("start-date")),
                "end": _date(s.get("end-date")),
                "external_ids": [{"type": e["type"], "value": e["value"]} for e in _ext_ids(s)],
                "url": _val(s.get("url")),
                "source_name": _val((s.get("source") or {}).get("source-name")),
            })

    payload = {
        "source": NAME,
        "fetched_at": ctx["now"],
        "orcid": orcid,
        "works": works,
        "fundings": fundings,
    }
    return payload, len(groups)
