"""Europe PMC REST search (resultType=core), cursor-paged."""
from __future__ import annotations

from common import as_list, normalize_doi, normalize_orcid

NAME = "europepmc"
URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 1000
MAX_PAGES = 10


def _trim(r):
    authors = []
    for a in as_list((r.get("authorList") or {}).get("author")):
        orcid = None
        for aid in as_list((a.get("authorId") or {}) if isinstance(a.get("authorId"), list)
                           else [a.get("authorId")]):
            if isinstance(aid, dict) and (aid.get("type") or "").upper() == "ORCID":
                orcid = normalize_orcid(aid.get("value"))
        authors.append({
            "fullName": a.get("fullName"),
            "firstName": a.get("firstName"),
            "lastName": a.get("lastName"),
            "initials": a.get("initials"),
            "collectiveName": a.get("collectiveName"),
            "orcid": orcid,
        })
    ji = r.get("journalInfo") or {}
    journal = ji.get("journal") or {}
    corrections = []
    for c in as_list((r.get("commentCorrectionList") or {}).get("commentCorrection")):
        corrections.append({"type": c.get("type"), "id": c.get("id"), "source": c.get("source")})
    return {
        "id": r.get("id"),
        "source": r.get("source"),
        "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"),
        "doi": normalize_doi(r.get("doi")),
        "title": r.get("title"),
        "authorString": r.get("authorString"),
        "authors": authors,
        "journal": journal.get("title"),
        "volume": ji.get("volume"),
        "issue": ji.get("issue"),
        "pageInfo": r.get("pageInfo"),
        "pubYear": r.get("pubYear"),
        "firstPublicationDate": r.get("firstPublicationDate"),
        "electronicPublicationDate": r.get("electronicPublicationDate"),
        "printPublicationDate": ji.get("printPublicationDate"),
        "pubTypes": as_list((r.get("pubTypeList") or {}).get("pubType")),
        "publisher": (r.get("bookOrReportDetails") or {}).get("publisher"),
        "commentCorrections": corrections,
        "citedByCount": r.get("citedByCount"),
        "isOpenAccess": r.get("isOpenAccess"),
    }


def fetch(ctx):
    http, ident = ctx["http"], ctx["ident"]
    query = ident.get("europepmc_query")
    if not query:
        parts = []
        for fam in ident["me"].get("family_names") or []:
            parts.append('AUTH:"%s %s"' % (fam, ident["me"].get("given_initial") or "Z"))
        if ident.get("orcid"):
            parts.append('AUTHORID:"%s"' % ident["orcid"])
        query = " OR ".join(parts)
    results, cursor, pages = [], "*", 0
    while pages < MAX_PAGES:
        page = http.get_json(URL, params={"query": query, "resultType": "core",
                                          "format": "json", "pageSize": PAGE_SIZE,
                                          "cursorMark": cursor})
        pages += 1
        batch = as_list((page.get("resultList") or {}).get("result"))
        results.extend(_trim(r) for r in batch)
        nxt = page.get("nextCursorMark")
        if not batch or not nxt or nxt == cursor or len(batch) < PAGE_SIZE:
            break
        cursor = nxt
    payload = {"source": NAME, "fetched_at": ctx["now"], "query": query, "results": results}
    return payload, len(results)
