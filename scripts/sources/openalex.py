"""OpenAlex: author summary (by ORCID, falling back to the configured author
id) and works filtered by author ORCID, cursor-paged at 200 per page."""
from __future__ import annotations

import os

from common import HttpError, as_list, contact_email, normalize_doi, normalize_orcid

NAME = "openalex"
BASE = "https://api.openalex.org"
PER_PAGE = 200
MAX_PAGES = 25
WORK_FIELDS = ["id", "doi", "display_name", "publication_year", "publication_date", "ids",
               "type", "primary_location", "best_oa_location", "locations", "authorships",
               "cited_by_count", "counts_by_year", "biblio", "is_retracted", "is_paratext"]


def _params(site, extra=None):
    p = {}
    email = contact_email(site)
    if email:
        p["mailto"] = email
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        p["api_key"] = key
    if extra:
        p.update(extra)
    return p


def _short_id(x):
    if not x:
        return None
    return str(x).rstrip("/").split("/")[-1]


def _loc(loc):
    if not isinstance(loc, dict):
        return None
    src = loc.get("source") or {}
    return {
        "id": loc.get("id"),
        "doi": normalize_doi(loc.get("doi")),
        "landing_page_url": loc.get("landing_page_url"),
        "pdf_url": loc.get("pdf_url"),
        "is_oa": loc.get("is_oa"),
        "version": loc.get("version"),
        "source": src.get("display_name") if isinstance(src, dict) else None,
        "source_type": src.get("type") if isinstance(src, dict) else None,
    }


def _trim_work(w):
    auths = []
    for a in as_list(w.get("authorships")):
        au = a.get("author") or {}
        auths.append({
            "position": a.get("author_position"),
            "author_id": _short_id(au.get("id")),
            "display_name": au.get("display_name"),
            "raw_author_name": a.get("raw_author_name"),
            "orcid": normalize_orcid(au.get("orcid")),
        })
    ids = w.get("ids") or {}
    return {
        "id": _short_id(w.get("id")),
        "doi": normalize_doi(w.get("doi")),
        "title": w.get("display_name"),
        "publication_year": w.get("publication_year"),
        "publication_date": w.get("publication_date"),
        "type": w.get("type"),
        "pmid": ids.get("pmid"),
        "pmcid": ids.get("pmcid"),
        "primary_location": _loc(w.get("primary_location")),
        "best_oa_location": _loc(w.get("best_oa_location")),
        "locations": [l for l in (_loc(x) for x in as_list(w.get("locations"))) if l],
        "authorships": auths,
        "cited_by_count": w.get("cited_by_count"),
        "counts_by_year": [{"year": c.get("year"), "cited_by_count": c.get("cited_by_count")}
                           for c in as_list(w.get("counts_by_year"))],
        "biblio": w.get("biblio") or {},
        "is_retracted": bool(w.get("is_retracted")),
        "is_paratext": bool(w.get("is_paratext")),
    }


def fetch(ctx):
    http, site, ident = ctx["http"], ctx["site"], ctx["ident"]
    orcid = ident["orcid"]
    author = None
    errors = []
    if orcid:
        try:
            author = http.get_json("%s/authors/orcid:%s" % (BASE, orcid), params=_params(site),
                                   allow_404=True)
        except HttpError as exc:
            errors.append(str(exc))
    if not author and ident.get("openalex_author_id"):
        author = http.get_json("%s/authors/%s" % (BASE, ident["openalex_author_id"]),
                               params=_params(site), allow_404=True)
    if not author:
        raise HttpError("OpenAlex author not found (%s)" % "; ".join(errors))
    author_id = _short_id(author.get("id"))

    def query(flt):
        """All works for a filter. Raises if the pages add up to fewer works
        than OpenAlex's own count, so a cut-off list never replaces the cache."""
        out, cursor, pages, total = [], "*", 0, None
        while cursor and pages < MAX_PAGES:
            page = http.get_json("%s/works" % BASE, params=_params(site, {
                "filter": flt, "per-page": PER_PAGE, "cursor": cursor,
                "select": ",".join(WORK_FIELDS)}))
            pages += 1
            meta = page.get("meta") or {}
            if total is None and isinstance(meta.get("count"), int):
                total = meta["count"]
            results = as_list(page.get("results"))
            out.extend(_trim_work(w) for w in results)
            cursor = meta.get("next_cursor")
            if not results:
                break
        # One missing work is tolerated (the index can change between pages).
        if total is not None and pages < MAX_PAGES and len(out) < total - max(1, total // 50):
            raise HttpError("OpenAlex returned %d of %d works for %s" % (len(out), total, flt))
        return out

    # Primary query by ORCID; the author id is a fallback (merge.py still
    # requires each work to list him by ORCID or by the name rule).
    flt = "author.orcid:%s" % orcid if orcid else "authorships.author.id:%s" % author_id
    works = query(flt)
    if not works and orcid and author_id and (author.get("works_count") or 0) > 0:
        flt = "authorships.author.id:%s" % author_id
        works = query(flt)
    if not works and (author.get("works_count") or 0) > 0:
        raise HttpError("OpenAlex returned no works, but the author record lists %d"
                        % author["works_count"])

    payload = {
        "source": NAME,
        "fetched_at": ctx["now"],
        "filter": flt,
        "author": {
            "id": author_id,
            "display_name": author.get("display_name"),
            "orcid": normalize_orcid(author.get("orcid")),
            "works_count": author.get("works_count"),
            "cited_by_count": author.get("cited_by_count"),
            "summary_stats": author.get("summary_stats") or {},
            "counts_by_year": author.get("counts_by_year") or [],
            "updated_date": author.get("updated_date"),
        },
        "works": works,
    }
    return payload, len(works)
