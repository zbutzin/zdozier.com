"""Crossref: works?filter=orcid:{orcid} for discovery, then works/{doi} for
canonical metadata. Records are cached in data/raw/crossref.json keyed by DOI
with fetched_at; a DOI is re-fetched only when missing or about 30 days old
(about 7 days for DOIs Crossref did not know), so weekly runs stay light. The
limits allow half a day of slack because scheduled runs start at slightly
different times each week.

Only a 404, 400, or 410 is cached as "Crossref does not know this DOI". Rate
limiting (429), refusals (401, 403), server errors, and lost connections keep
the cached record. After 3 such failures in a row the lookups stop and the
source fails, so fetch.py keeps the previous crossref.json. If the ORCID
filter fails and no lookup succeeds, the source fails too, so its age shows
in the site-health check."""
from __future__ import annotations

import time
from urllib.parse import quote

from common import (NON_CROSSREF_PREFIXES, OPENALEX_EXCLUDED_TYPES, HttpError, as_dict, as_list,
                    contact_email, log, normalize_doi, parse_iso_datetime, utc_now)

NAME = "crossref"
BASE = "https://api.crossref.org"
MAX_AGE_DAYS = 30
MISS_MAX_AGE_DAYS = 7
SLACK_DAYS = 0.5  # a record due "next Monday" is refreshed even if the run starts earlier
PAUSE = 0.2  # seconds between per-DOI requests
BREAKER = 3  # consecutive rate-limit, server, or connection failures before giving up
MISS_STATUSES = (400, 410)  # (404 arrives as None) Crossref does not know this DOI
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
    return age < limit - SLACK_DAYS


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
    fetched = failed = in_a_row = 0
    errors = []

    def lookup(doi):
        nonlocal fetched, failed, in_a_row
        if _fresh(cache.get(doi), now, force):
            return
        try:
            msg = http.get_json("%s/works/%s" % (BASE, quote(doi, safe="/")), params=params,
                                allow_404=True)
            fetched += 1
        except HttpError as exc:
            failed += 1
            if exc.budget:
                raise HttpError("crossref: stopped after %d lookups: %s" % (fetched + failed - 1, exc),
                                budget=True)
            old = as_dict(cache.get(doi))
            if exc.status in MISS_STATUSES and old.get("status") != 200:
                # Crossref does not know this DOI: a miss, retried in a week.
                cache[doi] = {"fetched_at": now_iso, "status": exc.status, "message": None}
                in_a_row = 0
                return
            # Anything else (429, 401, 403, 5xx, no connection) says nothing
            # about the record: keep the cached copy and count the failure.
            errors.append("%s: %s" % (doi, exc))
            in_a_row = in_a_row + 1 if exc.transient else 0
            if in_a_row >= BREAKER:
                raise HttpError("crossref: rate limited or unavailable (%d failed lookups in a row), "
                                "e.g. %s" % (in_a_row, exc), exc.status)
            return
        finally:
            time.sleep(PAUSE)
        in_a_row = 0
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
    if filter_error and fetched == 0:
        # Nothing came from Crossref this run. Failing here keeps the previous
        # file and its fetched_at, so a long outage reaches the health check.
        raise HttpError("crossref unreachable: %s" % filter_error)

    partial = filter_error
    if errors:
        note = "%d of %d lookups failed (cached records kept), e.g. %s" % (
            len(errors), fetched + failed, errors[0])
        partial = "%s; %s" % (filter_error, note) if filter_error else note
    payload = {
        "source": NAME,
        "fetched_at": now_iso,
        "orcid_filter": {"orcid": orcid, "dois": sorted(set(orcid_dois)),
                         "error": filter_error},
        "partial_error": partial,
        "stats": {"wanted": len(wanted), "relation_targets": len(extra - wanted),
                  "fetched": fetched, "failed": failed, "errors": errors[:10]},
        "works": {k: cache[k] for k in sorted(cache)},
    }
    return payload, len(set(orcid_dois))
