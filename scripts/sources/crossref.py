"""Crossref: works?filter=orcid:{orcid} for discovery, then works/{doi} for
canonical metadata. Records are cached in data/raw/crossref.json keyed by DOI
with fetched_at; a DOI is re-fetched only when missing or older than 30 days
(7 days for DOIs Crossref did not know), so weekly runs stay light."""
from __future__ import annotations

import time
from urllib.parse import quote

from common import (NON_CROSSREF_PREFIXES, OPENALEX_EXCLUDED_TYPES, HttpError, as_dict, as_list,
                    contact_email, log, normalize_doi, parse_iso_datetime, utc_now)

NAME = "crossref"
BASE = "https://api.crossref.org"
MAX_AGE_DAYS = 30
MISS_MAX_AGE_DAYS = 7
PAUSE = 0.2  # seconds between per-DOI requests
KEEP = ["DOI", "type", "subtype", "title", "subtitle", "container-title",
        "short-container-title", "author", "published", "published-print",
        "published-online", "issued", "posted", "accepted", "volume", "issue", "page",
        "article-number", "relation", "URL", "publisher", "institution", "group-title",
        "is-referenced-by-count", "update-to", "ISSN"]
# Fields the /works list route accepts in select= (subtype and institution are not).
SELECT = [k for k in KEEP if k not in ("institution", "subtype")]
RELATION_KEYS = ("is-preprint-of", "has-preprint", "is-version-of", "has-version",
                 "is-identical-to", "is-same-as")


def _trim(msg):
    out = {}
    for k in KEEP:
        if k in msg:
            out[k] = msg[k]
    authors = []
    for a in as_list(out.get("author")):
        authors.append({k: a[k] for k in ("given", "family", "name", "suffix", "ORCID",
                                          "sequence") if k in a})
    out["author"] = authors
    return out


def relation_dois(msg):
    out = set()
    rel = as_dict((msg or {}).get("relation"))
    for key in RELATION_KEYS:
        for r in as_list(rel.get(key)):
            if isinstance(r, dict) and (r.get("id-type") or "").lower() == "doi":
                d = normalize_doi(r.get("id"))
                if d:
                    out.add(d)
    return out


def target_dois(raw, pubs):
    """DOIs worth a Crossref lookup: everything discovered by other sources plus
    DOIs named in content/publications.yml."""
    dois = set()
    for w in as_list(as_dict(raw.get("orcid")).get("works")):
        dois.update(as_list(w.get("dois")))
    for w in as_list(as_dict(raw.get("openalex")).get("works")):
        if w.get("doi") and w.get("type") not in OPENALEX_EXCLUDED_TYPES:
            dois.add(w["doi"])
        for loc in as_list(w.get("locations")):
            if loc.get("doi"):
                dois.add(loc["doi"])
    for r in as_list(as_dict(raw.get("europepmc")).get("results")):
        if r.get("doi"):
            dois.add(r["doi"])
    pubs = as_dict(pubs)
    for d in as_list(pubs.get("include_dois")):
        dois.add(normalize_doi(d))
    for grp in as_list(pubs.get("same_as")):
        for d in as_list(grp):
            dois.add(normalize_doi(d))
    for m in as_list(pubs.get("manual")):
        if isinstance(m, dict) and m.get("doi"):
            dois.add(normalize_doi(m.get("doi")))
    return {d for d in dois if d and not d.startswith(NON_CROSSREF_PREFIXES)}


def _fresh(entry, now, force=False):
    if force or not isinstance(entry, dict):
        return False
    t = parse_iso_datetime(entry.get("fetched_at"))
    if not t:
        return False
    age = (now - t).total_seconds() / 86400.0
    limit = MAX_AGE_DAYS if entry.get("status") == 200 else MISS_MAX_AGE_DAYS
    return age < limit


def fetch(ctx):
    http, site, ident = ctx["http"], ctx["site"], ctx["ident"]
    prev = as_dict(ctx.get("prev"))
    cache = dict(as_dict(prev.get("works")))
    now_iso = ctx["now"]
    now = parse_iso_datetime(now_iso) or utc_now()
    force = bool(ctx.get("options", {}).get("force_crossref"))
    params = {}
    email = contact_email(site)
    if email:
        params["mailto"] = email

    # 1. Discovery by ORCID.
    orcid_dois = []
    orcid = ident.get("orcid")
    filter_error = None
    if orcid:
        cursor, pages, use_select = "*", 0, True
        try:
            while cursor and pages < 10:
                p = dict(params)
                p.update({"filter": "orcid:%s" % orcid, "rows": 200, "cursor": cursor})
                if use_select:
                    p["select"] = ",".join(SELECT)
                try:
                    page = http.get_json("%s/works" % BASE, params=p)
                except HttpError as exc:
                    if use_select and exc.status == 400:
                        use_select = False  # field list rejected; retry without it
                        continue
                    raise
                pages += 1
                items = as_list(as_dict(page.get("message")).get("items"))
                for it in items:
                    d = normalize_doi(it.get("DOI"))
                    if not d:
                        continue
                    orcid_dois.append(d)
                    old = as_dict(cache.get(d)).get("message") or {}
                    # List results lack some fields (subtype, institution), so they
                    # only refresh a full record we already hold; new DOIs get a
                    # per-DOI lookup below.
                    if old:
                        rec = dict(old)
                        rec.update(_trim(it))
                        cache[d] = {"fetched_at": now_iso, "status": 200, "message": rec}
                cursor = as_dict(page.get("message")).get("next-cursor")
                if not items:
                    break
        except HttpError as exc:
            filter_error = str(exc)
            orcid_dois = list(as_list(prev.get("orcid_filter", {}).get("dois")))
            log("crossref: ORCID filter failed, using cached list: %s" % exc)

    # 2. Per-DOI lookups (cached).
    wanted = target_dois(ctx.get("raw") or {}, ctx.get("pubs") or {})
    wanted.update(orcid_dois)
    fetched = failed = 0
    errors = []

    def lookup(doi):
        nonlocal fetched, failed
        if _fresh(cache.get(doi), now, force):
            return
        try:
            msg = http.get_json("%s/works/%s" % (BASE, quote(doi, safe="/")), params=params,
                                allow_404=True)
            fetched += 1
        except HttpError as exc:
            failed += 1
            if exc.status == 404 or (exc.status and 400 <= exc.status < 500):
                cache[doi] = {"fetched_at": now_iso, "status": exc.status, "message": None}
            else:
                errors.append("%s: %s" % (doi, exc))
            return
        finally:
            time.sleep(PAUSE)
        if msg is None:
            cache[doi] = {"fetched_at": now_iso, "status": 404, "message": None}
        else:
            cache[doi] = {"fetched_at": now_iso, "status": 200,
                          "message": _trim(as_dict(msg.get("message")))}

    for doi in sorted(wanted):
        lookup(doi)
    # 3. One hop along preprint/published relations.
    extra = set()
    for doi in sorted(wanted):
        extra.update(relation_dois(as_dict(cache.get(doi)).get("message")))
    for doi in sorted(extra - wanted):
        if not doi.startswith(NON_CROSSREF_PREFIXES):
            lookup(doi)

    if len(errors) > max(5, len(wanted) // 2):
        raise HttpError("crossref: %d lookups failed, e.g. %s" % (len(errors), errors[0]))

    payload = {
        "source": NAME,
        "fetched_at": now_iso,
        "orcid_filter": {"orcid": orcid, "dois": sorted(set(orcid_dois)),
                         "error": filter_error},
        "partial_error": filter_error,
        "stats": {"wanted": len(wanted), "relation_targets": len(extra - wanted),
                  "fetched": fetched, "failed": failed, "errors": errors[:10]},
        "works": {k: cache[k] for k in sorted(cache)},
    }
    return payload, len(set(orcid_dois))
