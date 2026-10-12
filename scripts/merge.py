#!/usr/bin/env python3
"""Offline step: data/raw/*.json + content/*.yml -> data/*.json.

Writes data/publications.json, metrics.json, grants.json, software.json and
run_report.json (SPEC sections 3 to 6). Works when any content file or raw
source is missing, empty, or partial. Always exits 0 unless arguments are
invalid; problems are reported in run_report.json.

Usage: python3 scripts/merge.py [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import itertools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (CONTENT_DIR, CROSSREF_EXCLUDED_TYPES, DATA_DIR, OPENALEX_EXCLUDED_TYPES,  # noqa: E402
                    ORCID_EXCLUDED_TYPES, RAW_DIR, as_dict, as_list, author_from_string,
                    clean_date, clean_title, date_sort_key, date_to_ordinal, doi_url,
                    family_keys, identity_config, is_group_name, is_preprint_doi,
                    is_repository_doi, jmir_pair, load_json, load_yaml, log, make_author,
                    match_title, name_key, normalize_doi, normalize_orcid, normalize_pmcid,
                    normalize_pmid, partial_date, preprint_base, preprint_server, pretty_org,
                    pretty_person, repository_name, strip_accents, strip_html, title_overlap,
                    title_ratio, today_iso,
                    utc_now_iso, write_json_atomic, year_of)
from sources.crossref import relation_dois  # noqa: E402
from sources.reporter import core_project, pi_is_me  # noqa: E402

PUB_TYPES = ("article", "review", "conference", "preprint", "chapter", "report", "other")
STATUSES = ("published", "accepted", "under-review", "preprint")
ROLES = ("first", "senior", "coauthor")
SOURCE_ORDER = ["orcid", "openalex", "crossref", "europepmc", "manual", "forced"]
# Override fields that follow a paper from its preprint DOI to the published DOI.
CARRY_FIELDS = ("featured", "topics", "contribution", "code_url", "role")
OVERRIDE_BLOCKED = ("id", "sources", "first_seen", "versions", "alt_ids", "authors_source")
# Sources an `authors_source` override may pin a work's author list to.
AUTHOR_SOURCES = ("crossref", "europepmc", "openalex", "orcid")
SANITY_DROP = 0.15
FUZZY_RATIO = 0.88

WORK_KEYS = ["id", "doi", "title", "authors", "author_count", "my_position", "role", "year",
             "date", "venue", "volume", "issue", "pages", "type", "status", "status_note",
             "url", "oa_url", "pmid", "pmcid", "cited_by", "versions", "topics",
             "contribution", "featured", "code_url", "sources", "first_seen", "alt_ids",
             "openalex_id"]


# --------------------------------------------------------------------------
# Per-source normalizers -> common record shape
# --------------------------------------------------------------------------

def _first(x):
    for v in as_list(x):
        if v:
            return v
    return None


def _cr_date(obj):
    parts = as_list(as_dict(obj).get("date-parts"))
    if not parts or not parts[0]:
        return None
    p = list(parts[0]) + [None, None]
    return partial_date(p[0], p[1], p[2])


def _earliest(dates):
    """Earliest of several partial dates. A less precise date that contains a
    more precise one ('2022' vs '2022-05-19') is ignored."""
    ds = [d for d in (clean_date(x) for x in dates) if d]
    keep = [d for d in ds if not any(o != d and o.startswith(d) for o in ds)]
    if not keep:
        return None
    return min(keep, key=lambda d: (date_to_ordinal(d), -len(d)))


def _is_placeholder(d):
    """OpenAlex and others use YYYY-01-01 when only the year is known."""
    return bool(d) and len(d) == 10 and d.endswith("-01-01")


def expand_pages(pages):
    """'438-43' -> '438-443'; other forms unchanged."""
    if not pages:
        return pages
    p = str(pages).strip().replace("\u2013", "-").replace("\u2014", "-")
    m = re.match(r"^([A-Za-z]*)(\d+)\s*-\s*([A-Za-z]*)(\d+)$", p)
    if not m:
        return p
    pre1, a, pre2, b = m.groups()
    if len(b) < len(a) and (not pre2 or pre2 == pre1):
        b = a[:len(a) - len(b)] + b
        if int(b) < int(a):
            return p
    return "%s%s-%s%s" % (pre1, a, pre2 or pre1, b)


def _blank_rec():
    return {"title": None, "authors": [], "date": None, "venue": None, "volume": None,
            "issue": None, "pages": None, "type": None, "pmid": None, "pmcid": None,
            "cited_by": None, "oa_url": None, "server_hint": None, "openalex_id": None,
            "link_dois": set(), "link_pmids": set(), "epmc_links": [], "ppr_id": None,
            "url": None, "counts_by_year": [], "is_retracted": False, "truncated": False,
            "subtitle": None}


def norm_crossref(msg, me, my_orcid):
    r = _blank_rec()
    r["title"] = strip_html(_first(msg.get("title")))
    sub = strip_html(_first(msg.get("subtitle")))
    if r["title"] and sub and match_title(sub) not in match_title(r["title"]):
        r["subtitle"] = sub
    for a in as_list(msg.get("author")):
        if a.get("family"):
            r["authors"].append(make_author(given=a.get("given"), family=a.get("family"),
                                            suffix=a.get("suffix"), orcid=a.get("ORCID"),
                                            me_cfg=me, my_orcid=my_orcid))
        elif a.get("name"):
            if is_group_name(a["name"]):
                r["authors"].append(make_author(name=a["name"], is_group=True))
            else:
                r["authors"].append(author_from_string(a["name"], orcid=a.get("ORCID"),
                                                       me_cfg=me, my_orcid=my_orcid))
    ctype = msg.get("type")
    if ctype == "posted-content":
        r["date"] = _cr_date(msg.get("posted")) or _cr_date(msg.get("issued"))
    else:
        r["date"] = (_earliest([_cr_date(msg.get("published-online")),
                                _cr_date(msg.get("published-print")),
                                _cr_date(msg.get("published"))])
                     or _cr_date(msg.get("issued")))
    inst = _first([i.get("name") for i in as_list(msg.get("institution")) if isinstance(i, dict)])
    r["venue"] = strip_html(_first(msg.get("container-title"))) or (
        strip_html(inst) if inst else None)
    if ctype == "posted-content" and not r["venue"]:
        r["venue"] = strip_html(msg.get("publisher"))
    r["server_hint"] = inst or _first(msg.get("container-title")) or msg.get("publisher")
    r["volume"] = msg.get("volume")
    r["issue"] = msg.get("issue")
    r["pages"] = msg.get("page") or msg.get("article-number")
    r["type"] = "%s%s" % (ctype or "", ("/" + msg["subtype"]) if msg.get("subtype") else "")
    r["link_dois"] = relation_dois(msg)
    return r


def norm_openalex(w, me, my_orcid):
    r = _blank_rec()
    r["title"] = strip_html(w.get("title"))
    auths = as_list(w.get("authorships"))
    for a in auths:
        raw = a.get("raw_author_name") or a.get("display_name") or ""
        r["authors"].append(author_from_string(raw, orcid=a.get("orcid"), me_cfg=me,
                                               my_orcid=my_orcid))
    r["truncated"] = len(auths) >= 100
    r["date"] = clean_date(w.get("publication_date")) or partial_date(w.get("publication_year"))
    pl = as_dict(w.get("primary_location"))
    r["venue"] = strip_html(pl.get("source"))
    r["server_hint"] = pl.get("source")
    b = as_dict(w.get("biblio"))
    r["volume"], r["issue"] = b.get("volume"), b.get("issue")
    fp, lp = b.get("first_page"), b.get("last_page")
    r["pages"] = ("%s-%s" % (fp, lp)) if fp and lp and fp != lp else (fp or None)
    r["type"] = w.get("type")
    r["pmid"] = normalize_pmid(w.get("pmid")) if w.get("pmid") else None
    r["pmcid"] = normalize_pmcid(w.get("pmcid")) if w.get("pmcid") else None
    r["cited_by"] = w.get("cited_by_count")
    r["openalex_id"] = w.get("id")
    r["counts_by_year"] = as_list(w.get("counts_by_year"))
    r["is_retracted"] = bool(w.get("is_retracted"))
    own = normalize_doi(w.get("doi"))
    oa = as_dict(w.get("best_oa_location"))
    url = oa.get("pdf_url") or oa.get("landing_page_url")
    if url and normalize_doi(url) == own:
        url = oa.get("pdf_url")
    r["oa_url"] = url or None
    for loc in as_list(w.get("locations")):
        lid = str(loc.get("id") or "")
        landing = str(loc.get("landing_page_url") or "")
        for cand in (loc.get("doi"), lid[4:] if lid.startswith("doi:") else None,
                     landing if re.match(r"^https?://(dx\.)?doi\.org/", landing, re.I) else None):
            d = normalize_doi(cand) if cand else None
            if d and d != own:
                r["link_dois"].add(d)
        if lid.startswith("pmid:"):
            p = normalize_pmid(lid[5:])
            if p and p != r["pmid"]:
                r["link_pmids"].add(p)
    return r


def norm_europepmc(x, me, my_orcid):
    r = _blank_rec()
    r["title"] = strip_html(x.get("title"))
    for a in as_list(x.get("authors")):
        if a.get("collectiveName"):
            r["authors"].append(make_author(name=a["collectiveName"], is_group=True))
        elif a.get("lastName"):
            # The suffix is only in fullName ("Colford JM Jr").
            toks = str(a.get("fullName") or "").split()
            suffix = toks[-1] if len(toks) > 2 and toks[-1] in ("Jr", "Jr.", "Sr", "Sr.", "II", "III") \
                and toks[-1] != (a.get("initials") or "") else None
            r["authors"].append(make_author(given=a.get("firstName") or a.get("initials"),
                                            family=a.get("lastName"), orcid=a.get("orcid"),
                                            suffix=suffix, me_cfg=me, my_orcid=my_orcid))
        elif a.get("fullName"):
            r["authors"].append(author_from_string(a["fullName"], orcid=a.get("orcid"),
                                                   me_cfg=me, my_orcid=my_orcid))
    if not r["authors"] and x.get("authorString"):
        for part in str(x["authorString"]).rstrip(".").split(", "):
            r["authors"].append(author_from_string(part, me_cfg=me, my_orcid=my_orcid))
    r["date"] = (clean_date(x.get("firstPublicationDate")) or clean_date(x.get("electronicPublicationDate"))
                 or clean_date(x.get("printPublicationDate")) or partial_date(x.get("pubYear")))
    r["venue"] = strip_html(x.get("journal")) or strip_html(x.get("publisher"))
    r["server_hint"] = x.get("publisher") or x.get("journal")
    r["volume"], r["issue"], r["pages"] = x.get("volume"), x.get("issue"), x.get("pageInfo")
    r["type"] = ";".join(as_list(x.get("pubTypes")))
    r["pmid"] = normalize_pmid(x.get("pmid")) if x.get("pmid") else None
    r["pmcid"] = normalize_pmcid(x.get("pmcid")) if x.get("pmcid") else None
    if (x.get("source") or "").upper() == "PPR":
        r["ppr_id"] = x.get("id")
    for c in as_list(x.get("commentCorrections")):
        t = (c.get("type") or "").lower()
        if "preprint" in t or "update" in t or "published" in t:
            r["epmc_links"].append(((c.get("source") or "").upper(), str(c.get("id") or "")))
    return r


def norm_orcid(w, me, my_orcid):
    r = _blank_rec()
    title = strip_html(w.get("title"))
    sub = strip_html(w.get("subtitle"))
    if title and sub and match_title(sub) not in match_title(title):
        title = "%s: %s" % (title, sub)
    r["title"] = title
    for c in as_list(w.get("contributors")):
        if (c.get("role") or "author") not in ("author", None, "AUTHOR"):
            continue
        r["authors"].append(author_from_string(c.get("name"), orcid=c.get("orcid"), me_cfg=me,
                                               my_orcid=my_orcid))
    r["date"] = clean_date(w.get("date"))
    r["venue"] = strip_html(w.get("journal_title"))
    r["type"] = w.get("type")
    r["pmid"] = normalize_pmid(w.get("pmid")) if w.get("pmid") else None
    r["pmcid"] = normalize_pmcid(w.get("pmcid")) if w.get("pmcid") else None
    r["url"] = w.get("url")
    return r


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------

PRECEDENCE = ["crossref", "openalex", "europepmc", "orcid"]


class Cand(object):
    def __init__(self, key, doi=None):
        self.key = key
        self.doi = doi
        self.sources = set()
        self.orcid_linked = False
        self.name_hit = False
        self.relation_only = False
        self.recs = {}
        self.excluded = None

    # Best value of a field by source precedence.
    def get(self, field):
        for s in PRECEDENCE:
            r = self.recs.get(s)
            if r and r.get(field) not in (None, "", [], set()):
                return r[field]
        return None

    def title(self):
        """Crossref title; its subtitle is appended only when another source's
        title confirms it (Health Affairs, for one, puts a teaser there)."""
        t = self.get("title") or ""
        cr = self.recs.get("crossref")
        if cr and cr.get("subtitle") and cr.get("title"):
            others = [self.recs[s]["title"] for s in ("openalex", "europepmc", "orcid")
                      if s in self.recs and self.recs[s].get("title")]
            sub = match_title(cr["subtitle"])
            if not others or any(sub in match_title(o) for o in others):
                t = "%s: %s" % (cr["title"], cr["subtitle"])
        return t

    def authors(self, source=None):
        """Author list by source precedence (SPEC 3.2), with two corrections
        for consortium papers:

        - Crossref sometimes deposits a consortium's whole member list as
          authors (eBioMedicine: 143 and 376 names) and leaves the group out.
          When that list is more than twice as long as Europe PMC's and Europe
          PMC names the group, Europe PMC has the printed byline; use it.
        - When Crossref drops a trailing group author that Europe PMC keeps
          and the people otherwise match one for one, the group is added back.

        `source` (from an `authors_source` override) picks one source's list."""
        if source:
            r = self.recs.get(source)
            if r and r.get("authors"):
                return r["authors"]
        cr = self.recs.get("crossref")
        oa = self.recs.get("openalex")
        ep = self.recs.get("europepmc")
        if cr and cr["authors"]:
            crl = cr["authors"]
            epl = ep["authors"] if ep else []
            ep_groups = [a for a in epl if a.get("is_group")]
            if ep_groups:
                if len(crl) > 2 * len(epl):
                    return epl
                n_people = len(epl) - len(ep_groups)
                groups_trail = all(a.get("is_group") for a in epl[n_people:])
                if groups_trail and not any(a.get("is_group") for a in crl) \
                        and len(crl) == n_people \
                        and family_keys(crl) == family_keys(epl[:n_people]):
                    return list(crl) + ep_groups
            return crl
        if oa and oa["authors"]:
            if oa["truncated"] and ep and len(ep["authors"]) > len(oa["authors"]):
                return ep["authors"]
            return oa["authors"]
        for s in ("europepmc", "orcid"):
            r = self.recs.get(s)
            if r and r["authors"]:
                return r["authors"]
        return []

    def date(self):
        dates = [self.recs[s]["date"] for s in PRECEDENCE if s in self.recs and self.recs[s]["date"]]
        if not dates:
            return None
        best = dates[0]
        if len(best) < 10:
            # Refine a year- or month-only date (often the print issue) with an
            # exact date from the same year that falls inside or before it,
            # e.g. the online-first date. Placeholder YYYY-01-01 dates are ignored.
            limit = date_to_ordinal(best, end=True)
            for d in dates[1:]:
                if len(d) == 10 and d[:4] == best[:4] and not _is_placeholder(d) \
                        and date_to_ordinal(d) <= limit:
                    best = d
                    break
        return best

    def year(self):
        return year_of(self.date())

    def oa_type(self):
        r = self.recs.get("openalex")
        return r["type"] if r else None

    def cr_type(self):
        r = self.recs.get("crossref")
        return r["type"] if r else None

    def server(self):
        hint = self.get("server_hint")
        s = preprint_server(self.doi, hint) if self.doi else None
        if s:
            return s
        if hint and re.search(r"rxiv|preprint|ssrn|research square|arxiv|osf", str(hint), re.I):
            return strip_html(hint)
        return "Preprint"

    def kind(self):
        """published | preprint | repository | nodoi"""
        if not self.doi:
            return "nodoi"
        if is_repository_doi(self.doi):
            return "repository"
        if is_preprint_doi(self.doi):
            return "preprint"
        ct = self.cr_type() or ""
        if ct.startswith("posted-content") and (ct.endswith("preprint") or ct == "posted-content"):
            return "preprint"
        if self.oa_type() == "preprint" and not ct.startswith("journal-article"):
            return "preprint"
        return "published"


def _orcid_rec_best(recs):
    """Several ORCID summaries can describe one DOI; prefer the richest."""
    return sorted(recs, key=lambda r: (-len(r["authors"]), -len(r.get("date") or "")))[0]


def build_candidates(raw, pubs, ident, warnings):
    me, my_orcid = ident["me"], ident["orcid"]
    cands = {}

    def get(key, doi=None):
        c = cands.get(key)
        if c is None:
            c = cands[key] = Cand(key, doi)
        return c

    # ORCID
    orcid_recs = {}
    for w in as_list(as_dict(raw.get("orcid")).get("works")):
        if (w.get("type") or "") in ORCID_EXCLUDED_TYPES:
            continue
        rec = norm_orcid(w, me, my_orcid)
        dois = [normalize_doi(d) for d in as_list(w.get("dois"))]
        dois = [d for d in dois if d]
        keys = dois or ["orcid:%s" % _orcid_group_key(raw, w)]
        for k in keys:
            orcid_recs.setdefault(k, []).append(rec)
    for k, recs in orcid_recs.items():
        c = get(k, k if not k.startswith("orcid:") else None)
        c.recs["orcid"] = _orcid_rec_best(recs)
        c.sources.add("orcid")
        c.orcid_linked = True

    # OpenAlex
    excluded_oa = []
    for w in as_list(as_dict(raw.get("openalex")).get("works")):
        if w.get("type") in OPENALEX_EXCLUDED_TYPES or w.get("is_paratext"):
            excluded_oa.append("%s (%s)" % (w.get("doi") or w.get("id"), w.get("type")))
            continue
        doi = normalize_doi(w.get("doi"))
        if not doi:
            warnings.append("OpenAlex work without a DOI skipped: %s \"%s\""
                            % (w.get("id"), (w.get("title") or "")[:90]))
            continue
        rec = norm_openalex(w, me, my_orcid)
        if not any(a.get("orcid") == my_orcid for a in rec["authors"]) and not any(
                a["is_me"] for a in rec["authors"]):
            warnings.append("OpenAlex work %s does not list him; skipped" % doi)
            continue
        c = get(doi, doi)
        c.recs["openalex"] = rec
        c.sources.add("openalex")
        c.orcid_linked = True
    if excluded_oa:
        warnings.append("OpenAlex items that are not publications were skipped: %s"
                        % ", ".join(excluded_oa))

    # Crossref discovery by ORCID
    cr_raw = as_dict(raw.get("crossref"))
    cr_works = as_dict(cr_raw.get("works"))
    for d in as_list(as_dict(cr_raw.get("orcid_filter")).get("dois")):
        d = normalize_doi(d)
        if d:
            c = get(d, d)
            c.sources.add("crossref")
            c.orcid_linked = True

    # Europe PMC (name hits must pass the "me" rule)
    for x in as_list(as_dict(raw.get("europepmc")).get("results")):
        doi = normalize_doi(x.get("doi"))
        rec = norm_europepmc(x, me, my_orcid)
        orcid_hit = any(a.get("orcid") == my_orcid for a in rec["authors"])
        name_hit = any(a["is_me"] for a in rec["authors"])
        if not (orcid_hit or name_hit):
            warnings.append("Europe PMC hit excluded (no author passes the name rule): %s \"%s\""
                            % (doi or x.get("id"), (x.get("title") or "")[:90]))
            continue
        lower_types = [t.lower() for t in as_list(x.get("pubTypes"))]
        if any(t in ("published erratum", "retraction of publication") for t in lower_types):
            continue
        if not doi:
            warnings.append("Europe PMC record without a DOI skipped: %s \"%s\""
                            % (x.get("id"), (x.get("title") or "")[:90]))
            continue
        c = get(doi, doi)
        c.recs["europepmc"] = rec
        c.sources.add("europepmc")
        if orcid_hit:
            c.orcid_linked = True
        else:
            c.name_hit = True

    # Forced DOIs and same_as members
    for d in as_list(pubs.get("include_dois")):
        d = normalize_doi(d)
        if d:
            c = get(d, d)
            c.sources.add("forced")
            c.orcid_linked = True
    for grp in as_list(pubs.get("same_as")):
        for d in as_list(grp):
            d = normalize_doi(d)
            if d and d not in cands and as_dict(cr_works.get(d)).get("message"):
                c = get(d, d)
                c.orcid_linked = True

    # Crossref metadata for every DOI candidate
    def attach_crossref(c):
        entry = as_dict(cr_works.get(c.doi))
        msg = entry.get("message")
        if msg:
            c.recs["crossref"] = norm_crossref(msg, me, my_orcid)
            c.sources.add("crossref")
            ctype = msg.get("type")
            if ctype in CROSSREF_EXCLUDED_TYPES and "forced" not in c.sources:
                c.excluded = "Crossref type %s" % ctype

    for c in list(cands.values()):
        if c.doi:
            attach_crossref(c)

    # One hop along Crossref relations: add related versions that list him.
    for c in list(cands.values()):
        cr = c.recs.get("crossref")
        if not cr:
            continue
        for d in sorted(cr["link_dois"]):
            if d in cands or not as_dict(cr_works.get(d)).get("message"):
                continue
            nc = Cand(d, d)
            attach_crossref(nc)
            if any(a["is_me"] for a in nc.recs["crossref"]["authors"]):
                nc.relation_only = True
                cands[d] = nc

    for c in cands.values():
        if not c.recs and not c.excluded:
            c.excluded = "no metadata from any source"
            if "forced" in c.sources:
                warnings.append("include_dois entry %s has no metadata (Crossref lookup failed?)"
                                % c.key)
    return cands


def _orcid_group_key(raw, w):
    """Stable key for an ORCID work without a DOI: first put-code of its group."""
    gi = w.get("group")
    first = None
    for x in as_list(as_dict(raw.get("orcid")).get("works")):
        if x.get("group") == gi:
            pc = x.get("put_code")
            if first is None or (pc is not None and pc < first):
                first = pc
    return first if first is not None else w.get("put_code")


# --------------------------------------------------------------------------
# Version linking
# --------------------------------------------------------------------------

class Groups(object):
    def __init__(self, keys):
        self.parent = {k: k for k in keys}
        self.members = {k: {k} for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def same(self, a, b):
        return self.find(a) == self.find(b)

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        return ra

    def groups(self):
        return [sorted(m) for m in self.members.values()]


def _conflict(cands, a_keys, b_keys):
    """A group may hold one published DOI and one preprint per server. Conflicts
    already present in either side (e.g. from same_as) do not block a join."""
    def profile(keys):
        pub, servers = set(), {}
        for k in keys:
            kind = cands[k].kind()
            if kind == "published":
                pub.add(k)
            elif kind == "preprint":
                servers.setdefault(cands[k].server(), set()).add(k)
        return pub, servers
    pa, sa = profile(a_keys)
    pb, sb = profile(b_keys)
    if len(pa | pb) > max(1, len(pa), len(pb)):
        return "two published DOIs (%s)" % ", ".join(sorted(pa | pb))
    for srv in set(sa) | set(sb):
        both = sa.get(srv, set()) | sb.get(srv, set())
        if len(both) > max(1, len(sa.get(srv, ())), len(sb.get(srv, ()))):
            return "two %s preprints (%s)" % (srv, ", ".join(sorted(both)))
    return None


def link_versions(cands, pubs, warnings, links):
    keys = sorted(k for k, c in cands.items() if not c.excluded)
    g = Groups(keys)

    def join(a, b, rule, detail="", force=False):
        if a not in g.parent or b not in g.parent or g.same(a, b):
            return False
        ra, rb = g.find(a), g.find(b)
        if not force:
            why = _conflict(cands, g.members[ra], g.members[rb])
            if why:
                if rule not in ("fuzzy-title", "fuzzy-authors"):
                    warnings.append("version link %s <-> %s (%s) not applied: %s"
                                    % (a, b, rule, why))
                return False
        g.union(a, b)
        links.append({"a": a, "b": b, "rule": rule, "detail": detail})
        return True

    # 1. Explicit same_as groups.
    for grp in as_list(pubs.get("same_as")):
        ds = [normalize_doi(d) for d in as_list(grp)]
        ds = [d for d in ds if d and d in g.parent]
        for d in ds[1:]:
            join(ds[0], d, "same_as", force=True)

    # 2. Metadata links: Crossref relations, OpenAlex merged locations,
    #    Europe PMC preprint/update links (resolved through PMIDs and PPR ids).
    pmid_map, ppr_map = {}, {}
    for k in keys:
        c = cands[k]
        for s in ("europepmc", "openalex", "orcid"):
            r = c.recs.get(s)
            if r and r.get("pmid"):
                pmid_map.setdefault(r["pmid"], set()).add(k)
        r = c.recs.get("europepmc")
        if r and r.get("ppr_id"):
            ppr_map.setdefault(r["ppr_id"], set()).add(k)
    for k in keys:
        c = cands[k]
        cr = c.recs.get("crossref")
        if cr:
            for d in sorted(cr["link_dois"]):
                join(k, d, "crossref-relation")
        oa = c.recs.get("openalex")
        if oa:
            for d in sorted(oa["link_dois"]):
                join(k, d, "openalex-location")
            for p in sorted(oa["link_pmids"]):
                for other in sorted(pmid_map.get(p, ())):
                    join(k, other, "openalex-location", "pmid %s" % p)
        ep = c.recs.get("europepmc")
        if ep:
            for src, ident in ep["epmc_links"]:
                targets = pmid_map.get(ident, set()) if src == "MED" else ppr_map.get(ident, set())
                for other in sorted(targets):
                    join(k, other, "europepmc-link", "%s %s" % (src, ident))

    # 3. JMIR preprint rule.
    for k in keys:
        other = jmir_pair(k) if cands[k].doi else None
        if other and other in g.parent:
            join(k, other, "jmir")

    # 3b. Numbered versions of one preprint (Research Square .../v1 and .../v2,
    #     Preprints.org ...v1 and ...v2) are the same work. Forced, because the
    #     one-preprint-per-server guard would otherwise keep them apart.
    by_base = {}
    for k in keys:
        b = preprint_base(k) if cands[k].doi else None
        if b:
            by_base.setdefault(b, []).append(k)
    for b in sorted(by_base):
        ks = by_base[b]
        for k in ks[1:]:
            join(ks[0], k, "preprint-version", b, force=True)

    # 4. Fuzzy matching (title similarity plus shared authors), strongest first.
    info = {}
    for k in keys:
        c = cands[k]
        info[k] = {"title": c.title(), "fams": family_keys(c.authors()), "year": c.year(),
                   "date": c.date(), "kind": c.kind(), "first": _first_family(c.authors())}
    pairs = []
    for a, b in itertools.combinations(keys, 2):
        ia, ib = info[a], info[b]
        if not ia["title"] or not ib["title"]:
            continue
        pair_kinds = {ia["kind"], ib["kind"]}
        if ia["kind"] == ib["kind"] and ia["kind"] in ("published", "repository"):
            continue
        ratio = title_ratio(ia["title"], ib["title"])
        overlap = title_overlap(ia["title"], ib["title"])
        if ratio < 0.6 and overlap < 0.6:
            continue
        pairs.append((ratio, overlap, a, b, pair_kinds))
    pairs.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))

    for ratio, overlap, a, b, kinds in pairs:
        ia, ib = info[a], info[b]
        if g.same(a, b) or not _temporal_ok(ia, ib, 180):
            continue
        shared = len(ia["fams"] & ib["fams"])
        if ia["year"] and ib["year"] and abs(ia["year"] - ib["year"]) > 3:
            continue
        ok = False
        if ratio >= FUZZY_RATIO and shared >= 2:
            ok = True
        elif ratio >= 0.95 and (not ia["fams"] or not ib["fams"]) and (
                "nodoi" in kinds or "repository" in kinds) and (
                not ia["year"] or not ib["year"] or abs(ia["year"] - ib["year"]) <= 1):
            ok = True  # author list unknown on one side (e.g. ORCID-only abstract)
        if ok:
            join(a, b, "fuzzy-title", "title %.2f, %d shared authors" % (ratio, shared))

    # 4b. Same first author and most of the author list shared, with a
    #     reworded title (common between a medRxiv preprint and the journal
    #     version). Preprint to published only; preprint posted first.
    for ratio, overlap, a, b, kinds in pairs:
        if kinds != {"preprint", "published"} or g.same(a, b):
            continue
        ia, ib = info[a], info[b]
        pre, pub = (ia, ib) if ia["kind"] == "preprint" else (ib, ia)
        if not _temporal_ok(ia, ib, 31) or not pre["year"] or not pub["year"]:
            continue
        if not 0 <= pub["year"] - pre["year"] <= 2:
            continue
        if not pre["first"] or pre["first"] != pub["first"]:
            continue
        shared = len(pre["fams"] & pub["fams"])
        smaller = min(len(pre["fams"]), len(pub["fams"]))
        if shared < 3 or shared < 0.6 * smaller:
            continue
        if overlap >= 0.7 or ratio >= 0.75:
            join(a, b, "fuzzy-authors", "title %.2f, word overlap %.2f, %d of %d authors shared"
                 % (ratio, overlap, shared, smaller))
    return g


def _first_family(authors):
    for a in authors:
        if not a.get("is_group") and a.get("family"):
            return name_key(a["family"])
    return None


def _temporal_ok(ia, ib, slack_days):
    """A preprint must not appear long after the published version."""
    kinds = (ia["kind"], ib["kind"])
    if "preprint" not in kinds or "published" not in kinds:
        return True
    pre, pub = (ia, ib) if ia["kind"] == "preprint" else (ib, ia)
    a, b = date_to_ordinal(pre["date"]), date_to_ordinal(pub["date"], end=True)
    if a is None or b is None:
        return True
    return a <= b + slack_days


# --------------------------------------------------------------------------
# Assembling works
# --------------------------------------------------------------------------

def _rank(c, same_as_pos):
    kind = c.kind()
    if kind == "published":
        r = 0
    elif kind == "preprint":
        s = c.server()
        r = 1 if s in ("medRxiv", "bioRxiv") else (3 if s == "JMIR Preprints" else 2)
    elif kind == "nodoi":
        r = 4
    else:
        r = 5
    return (r, same_as_pos.get(c.key, 99), -len(c.sources), 0 if "crossref" in c.recs else 1,
            date_sort_key(c.date()), c.key)


def classify_type(c, venue):
    kind = c.kind()
    if kind == "preprint":
        return "preprint"
    oa = c.oa_type() or ""
    cr = (c.cr_type() or "").split("/")[0]
    ep = [t.lower() for t in (as_dict(c.recs.get("europepmc")).get("type") or "").split(";") if t]
    orc = as_dict(c.recs.get("orcid")).get("type") or ""
    v = venue or ""
    if oa == "review" or any(t in ("review", "systematic review") for t in ep):
        return "review"
    if (cr == "proceedings-article" or oa == "conference-paper"
            or orc in ("conference-paper", "conference-abstract")
            or re.search(r"neural information processing|neurips|proceedings of", v, re.I)):
        return "conference"
    if cr in ("book-chapter", "book-section", "book-part", "reference-entry") or oa == "book-chapter" \
            or orc == "book-chapter":
        return "chapter"
    if cr in ("report", "report-series") or oa == "report" or orc == "report":
        return "report"
    if cr == "journal-article" or oa in ("article", "letter") or orc == "journal-article":
        return "article"
    if oa in ("editorial", "other") or orc == "other":
        return "other"
    return "article" if v else "other"


def _role(authors, pos):
    if not pos:
        return "coauthor"
    if pos == 1:
        return "first"
    persons = [i for i, a in enumerate(authors, 1) if not a.get("is_group")]
    if persons and persons[-1] == pos and len(persons) >= 3:
        return "senior"
    return "coauthor"


def _mark_me(authors, ident, c, warnings, label):
    """Ensure exactly the right author is flagged; fall back to OpenAlex ORCID position."""
    me = ident["me"]
    fams = {name_key(f) for f in me.get("family_names") or []}
    initial = (me.get("given_initial") or "Z")[:1].upper()
    for a in authors:
        # Safety: an ORCID-matched author named e.g. "Dozier, M." is not him.
        if a["is_me"] and a.get("given") and name_key(a.get("family")) in fams \
                and not a["given"].strip().upper().startswith(initial):
            a["is_me"] = False
            warnings.append("%s: author %s carried his ORCID but fails the name rule; not marked"
                            % (label, a["name"]))
    mine = [i for i, a in enumerate(authors) if a["is_me"]]
    if len(mine) > 1:
        # Prefer the ORCID-matched one, otherwise the first.
        by_orcid = [i for i in mine if authors[i].get("orcid")]
        keep = by_orcid[0] if by_orcid else mine[0]
        for i in mine:
            if i != keep:
                authors[i]["is_me"] = False
        mine = [keep]
    if not mine:
        oa = c.recs.get("openalex")
        if oa:
            idx = [i for i, a in enumerate(oa["authors"]) if a.get("orcid") == ident["orcid"]]
            if idx and idx[0] < len(authors) and len(oa["authors"]) == len(authors):
                a = authors[idx[0]]
                if not (a.get("given") and name_key(a.get("family")) in fams
                        and not a["given"].upper().startswith(initial)):
                    a["is_me"] = True
                    mine = [idx[0]]
    if not mine and authors:
        warnings.append("%s: could not find him in the author list" % label)
    return (mine[0] + 1) if mine else None


def _clean_author(a):
    return {"given": a.get("given"), "family": a.get("family"), "name": a.get("name"),
            "is_me": bool(a.get("is_me")), "is_group": bool(a.get("is_group")),
            "orcid": a.get("orcid"),
            "suffix": a.get("suffix"), "apa": a.get("apa")}


def _copy_authors(authors):
    return [dict(a) for a in authors]


def _author_key(a):
    if a.get("is_group"):
        return ("group", re.sub(r"\s+", " ", (a.get("name") or "").strip().lower()))
    given = re.sub(r"[^a-z]", "", strip_accents(a.get("given") or "").lower())
    return ("person", name_key(a.get("family")), given)


def dedupe_authors(authors):
    """Drop exact repeats (same family and same full given name, or the same
    group name), keeping the first position. Repeats of family plus initial
    alone are kept: large author lists often have two different 'Wang, Y.'.
    An ORCID or the 'me' flag on a dropped copy moves to the kept one."""
    out, index = [], {}
    for a in authors:
        k = _author_key(a)
        if k[1] and (k[0] == "group" or k[2]) and k in index:
            kept = out[index[k]]
            if a.get("orcid") and not kept.get("orcid"):
                kept["orcid"] = a["orcid"]
            if a.get("is_me"):
                kept["is_me"] = True
            continue
        index.setdefault(k, len(out))
        out.append(a)
    return out


def assemble_group(members, cands, ident, same_as_pos, warnings, author_pins=None):
    ms = sorted((cands[k] for k in members), key=lambda c: _rank(c, same_as_pos))
    primary = ms[0]
    others = ms[1:]
    pin = as_dict(author_pins).get(primary.key)
    if pin and not as_dict(primary.recs.get(pin)).get("authors"):
        warnings.append("%s: authors_source %r has no author list; using the default" % (primary.key, pin))
        pin = None
    authors = _copy_authors(primary.authors(pin))
    if not authors:
        for o in others:
            if o.authors():
                authors = _copy_authors(o.authors())
                break
    authors = dedupe_authors(authors)
    label = primary.key
    pos = _mark_me(authors, ident, primary, warnings, label)
    venue = primary.get("venue")
    kind = primary.kind()
    if kind == "preprint":
        venue = primary.server()
    wtype = classify_type(primary, venue)
    date = primary.date()
    status = "preprint" if kind == "preprint" else ("published" if date else "accepted")
    versions, alt_ids = [], []
    # Numbered versions of one preprint: list only the newest; older ones (and
    # versions of the primary itself) go to alt_ids.
    pbase = preprint_base(primary.doi) if primary.doi else None
    newest = {}
    for o in others:
        b = preprint_base(o.doi) if o.doi else None
        if b and (b not in newest or (date_sort_key(o.date()), o.key)
                  > (date_sort_key(newest[b].date()), newest[b].key)):
            newest[b] = o
    for o in sorted(others, key=lambda c: date_sort_key(c.date())):
        b = preprint_base(o.doi) if o.doi else None
        superseded = bool(b) and (b == pbase or newest[b] is not o)
        if o.kind() == "preprint" and not superseded:
            versions.append({"doi": o.doi, "server": o.server(), "url": doi_url(o.doi),
                             "year": o.year(), "date": o.date()})
        else:
            alt_ids.append(o.key)
    oa = primary.recs.get("openalex") or {}
    url = doi_url(primary.doi) if primary.doi else (primary.get("url") or None)
    sources = set()
    for c in ms:
        sources |= c.sources
    pmid, pmcid = _pick_pubmed_ids(primary, others)
    if kind == "nodoi":
        pmid = None
    work = {
        "id": primary.key,
        "doi": primary.doi,
        "title": clean_title(primary.title()),
        "authors": authors,
        "author_count": len(authors),
        "my_position": pos,
        "role": _role(authors, pos),
        "year": year_of(date),
        "date": date,
        "venue": venue,
        "volume": primary.get("volume"),
        "issue": primary.get("issue"),
        "pages": expand_pages(primary.get("pages")),
        "type": wtype,
        "status": status,
        "status_note": None,
        "url": url,
        "oa_url": oa.get("oa_url"),
        "pmid": pmid,
        "pmcid": pmcid,
        "cited_by": oa.get("cited_by") if oa else None,
        "versions": versions,
        "topics": [],
        "contribution": None,
        "featured": False,
        "code_url": None,
        "sources": [s for s in SOURCE_ORDER if s in sources],
        "first_seen": None,
        "alt_ids": alt_ids,
        "openalex_id": oa.get("openalex_id") if oa else None,
        "_keys": [c.key for c in ms],
        "_orcid_linked": any(c.orcid_linked for c in ms),
        "_retracted": any(as_dict(c.recs.get("openalex")).get("is_retracted") for c in ms),
        # Only institutional-repository copies and nothing they belong to: not shown.
        "_repository_only": kind == "repository" and "forced" not in sources,
    }
    return work


def _pick_pubmed_ids(primary, others):
    """PMID and PMCID for the primary record.

    OpenAlex often merges a medRxiv preprint into the journal article's work
    and then reports the preprint's PubMed record (PubMed indexes NIH-funded
    preprints), so its pmid/pmcid can belong to the preprint. Europe PMC's
    record is looked up by this DOI and is trusted first. ORCID and OpenAlex
    values are used only if they are not known to belong to a linked preprint
    (its own ids, or Europe PMC 'Update of' / 'Preprint of' links)."""
    foreign_pmids, foreign_pmcids = set(), set()
    for o in others:
        if o.kind() != "preprint":
            continue  # repository copies are the same article and share its ids
        for s in ("europepmc", "orcid", "openalex"):
            r = o.recs.get(s)
            if r and r.get("pmid"):
                foreign_pmids.add(r["pmid"])
            if r and r.get("pmcid"):
                foreign_pmcids.add(r["pmcid"])
    ep = primary.recs.get("europepmc") or {}
    for src, ident in ep.get("epmc_links") or []:
        if src == "MED" and normalize_pmid(ident):
            foreign_pmids.add(normalize_pmid(ident))
        elif src == "PMC" and normalize_pmcid(ident):
            foreign_pmcids.add(normalize_pmcid(ident))

    def pick(field, foreign, order):
        if ep.get(field):
            return ep[field]
        for s in order:
            v = as_dict(primary.recs.get(s)).get(field)
            if v and v not in foreign:
                return v
        return None

    return (pick("pmid", foreign_pmids, ("orcid", "openalex")),
            pick("pmcid", foreign_pmcids, ("openalex", "orcid")))


def _manual_work(m, ident, warnings):
    if not isinstance(m, dict) or not m.get("title"):
        warnings.append("publications.yml manual entry without a title skipped: %r" % (m,))
        return None
    mid = str(m.get("id") or "").strip()
    if not mid:
        mid = "manual:" + re.sub(r"[^a-z0-9]+", "-", match_title(m["title"]))[:60].strip("-")
    elif not mid.startswith("manual:"):
        mid = "manual:" + mid
    authors = []
    for s in as_list(m.get("authors")):
        s = str(s).strip()
        if not s:
            continue
        if "," not in s:
            authors.append(make_author(name=s, is_group=True))
        else:
            authors.append(author_from_string(s, me_cfg=ident["me"], my_orcid=ident["orcid"]))
    work = {k: None for k in WORK_KEYS}
    work.update({
        "id": mid.lower(),
        "doi": normalize_doi(m.get("doi")),
        "title": clean_title(m.get("title")),
        "authors": authors,
        "author_count": len(authors),
        "year": m.get("year") or year_of(m.get("date")),
        "date": clean_date(m.get("date")) or (str(m["year"]) if m.get("year") else None),
        "venue": m.get("venue"),
        "volume": m.get("volume"), "issue": m.get("issue"), "pages": m.get("pages"),
        "type": m.get("type") or "article",
        "status": m.get("status") or "accepted",
        "status_note": m.get("status_note"),
        "url": m.get("url") or doi_url(normalize_doi(m.get("doi"))),
        "oa_url": m.get("oa_url"),
        "versions": [],
        "topics": [],
        "contribution": m.get("contribution"),
        "featured": bool(m.get("featured")),
        "code_url": m.get("code_url"),
        "sources": ["manual"],
        "alt_ids": [],
        "_keys": [mid.lower()],
        "_orcid_linked": True,
        "_retracted": False,
        "_manual": m,
    })
    for k, v in m.items():  # extra keys (editors, publisher, ...) pass through
        if k not in work and k not in ("authors",) and not str(k).startswith("_"):
            work[k] = v
    pos = None
    for i, a in enumerate(authors, 1):
        if a["is_me"]:
            pos = i
            break
    if authors and not pos:
        warnings.append("%s: no author matches him (expected e.g. \"Butzin-Dozier, Z.\")" % mid)
    work["my_position"] = pos
    work["role"] = m.get("role") if m.get("role") in ROLES else _role(authors, pos)
    if m.get("topics"):
        work["topics"] = [str(t) for t in as_list(m.get("topics"))]
    return work


def _apply_override(work, ov, warnings, carry_only=False):
    if not isinstance(ov, dict):
        return
    for k, v in ov.items():
        if k in OVERRIDE_BLOCKED or k.startswith("_"):
            continue
        if carry_only and k not in CARRY_FIELDS:
            continue
        if k == "type" and v not in PUB_TYPES:
            warnings.append("%s: override type %r ignored (use one of %s)"
                            % (work["id"], v, ", ".join(PUB_TYPES)))
            continue
        if k == "status" and v not in STATUSES:
            warnings.append("%s: override status %r ignored" % (work["id"], v))
            continue
        if k == "role" and v not in ROLES:
            warnings.append("%s: override role %r ignored" % (work["id"], v))
            continue
        if k == "topics":
            work["_override_topics"] = list(work.get("_override_topics") or []) + [
                str(t) for t in as_list(v)]
            continue
        if k == "authors" and isinstance(v, list):
            auths = []
            for s in v:
                s = str(s)
                auths.append(make_author(name=s, is_group=True) if "," not in s
                             else author_from_string(s))
            work["authors"] = auths
            work["author_count"] = len(auths)
            work["my_position"] = next((i for i, a in enumerate(auths, 1) if a["is_me"]), None)
            continue
        if k == "group_author":
            # A consortium named in the byline that no source reports: added at the end.
            have = {_author_key(a) for a in work.get("authors") or []}
            for name in as_list(v):
                g = make_author(name=str(name).strip(), is_group=True) if name else None
                if g and g["name"] and _author_key(g) not in have:
                    work["authors"] = list(work.get("authors") or []) + [g]
                    have.add(_author_key(g))
            work["author_count"] = len(work.get("authors") or [])
            continue
        if k == "title" and v:
            v = clean_title(v)
        if k == "date":
            v = clean_date(v)
            work["year"] = year_of(v) or work.get("year")
        work[k] = v
    if "role" in ov and ov.get("role") in ROLES:
        work["_role_override"] = True


def _compile_topics(pubs, warnings):
    rules = []
    for tid, pats in as_dict(pubs.get("topic_rules")).items():
        for p in as_list(pats):
            try:
                rules.append((str(tid), re.compile(str(p), re.I)))
            except re.error as exc:
                warnings.append("topic_rules[%s]: bad regex %r (%s)" % (tid, p, exc))
    known = [str(t.get("id")) for t in as_list(pubs.get("topics")) if isinstance(t, dict)
             and t.get("id")]
    return rules, known


def _assign_topics(work, rules, known, warnings):
    text = "%s %s" % (work.get("title") or "", work.get("venue") or "")
    found = []
    for tid, rx in rules:
        if tid not in found and rx.search(text):
            found.append(tid)
    for t in list(work.get("topics") or []) + list(work.pop("_override_topics", None) or []):
        if t not in found:
            found.append(t)
    if known:
        unknown = [t for t in found if t not in known]
        if unknown:
            warnings.append("%s: unknown topic id(s) %s dropped" % (work["id"], ", ".join(unknown)))
        found = [t for t in known if t in found]  # canonical order
    work["topics"] = found


def _keyset(work):
    keys = {work.get("id")}
    if work.get("doi"):
        keys.add(work["doi"])
    for v in as_list(work.get("versions")):
        if isinstance(v, dict) and v.get("doi"):
            keys.add(v["doi"])
    for a in as_list(work.get("alt_ids")):
        keys.add(a)
    for k in as_list(work.get("_keys")):
        keys.add(k)
    return {k for k in keys if k}


def merge_publications(raw, content, prev, today, warnings, links):
    """Returns (visible works, hidden works)."""
    ident = content["ident"]
    pubs = as_dict(content.get("publications"))
    cands = build_candidates(raw, pubs, ident, warnings)
    excluded = sorted("%s (%s)" % (k, c.excluded) for k, c in cands.items()
                      if c.excluded and c.excluded != "no metadata from any source")
    if excluded:
        warnings.append("Excluded as non-publications: %s" % ", ".join(excluded))
    groups = link_versions(cands, pubs, warnings, links)
    same_as_pos = {}
    for grp in as_list(pubs.get("same_as")):
        for i, d in enumerate(as_list(grp)):
            d = normalize_doi(d)
            if d and d not in same_as_pos:
                same_as_pos[d] = i
    author_pins = {}
    for k, v in as_dict(pubs.get("overrides")).items():
        src = as_dict(v).get("authors_source")
        if src is None:
            continue
        nk = normalize_doi(str(k)) or str(k).strip().lower()
        if src in AUTHOR_SOURCES:
            author_pins[nk] = src
        else:
            warnings.append("%s: override authors_source %r ignored (use one of %s)"
                            % (nk, src, ", ".join(AUTHOR_SOURCES)))
    works = []
    for m in groups.groups():
        w = assemble_group(m, cands, ident, same_as_pos, warnings, author_pins)
        if w.pop("_repository_only", False):
            warnings.append("repository copy with no matching publication skipped: %s \"%s\" "
                            "(it joins the journal record once that is found; add the DOI to "
                            "include_dois to show it on its own)" % (w["id"], (w["title"] or "")[:90]))
            continue
        works.append(w)

    # Manual entries: standalone, or absorbed by a discovered version of the same paper.
    for m in as_list(pubs.get("manual")):
        mw = _manual_work(m, ident, warnings)
        if not mw:
            continue
        match = None
        for w in works:
            if mw.get("doi") and mw["doi"] in _keyset(w):
                match = w
                break
            r = title_ratio(mw["title"], w["title"])
            shared = len(family_keys(mw["authors"]) & family_keys(w["authors"]))
            if r >= FUZZY_RATIO and (shared >= 2 or r >= 0.95):
                match = w
                break
        if match is not None and not match.get("doi") and not mw.get("doi"):
            # A DOI-less ORCID record of the same item carries less than the
            # curated entry (no status, often no authors): the manual entry wins.
            works.remove(match)
            mw["sources"] = [s for s in SOURCE_ORDER if s in set(mw["sources"]) | set(match["sources"])]
            mw["alt_ids"] = list(mw["alt_ids"]) + [k for k in match["_keys"] if k != mw["id"]]
            mw["_keys"] = list(mw["_keys"]) + list(match["_keys"])
            works.append(mw)
            continue
        if match is not None:
            warnings.append("manual entry %s matches discovered work %s (%s); the discovered "
                            "record is used and the manual entry can be removed"
                            % (mw["id"], match["id"], match.get("venue")))
            for k in ("contribution", "code_url"):
                if mw.get(k) and not match.get(k):
                    match[k] = mw[k]
            if mw.get("featured"):
                match["featured"] = True
            if mw.get("topics"):
                match["_override_topics"] = list(match.get("_override_topics") or []) + mw["topics"]
            match["alt_ids"] = list(match["alt_ids"]) + [mw["id"]]
            match["_keys"] = list(match["_keys"]) + [mw["id"]]
            continue
        works.append(mw)

    # Overrides: fields that follow the paper from any of its DOIs, then the
    # primary record's own override (wins).
    overrides = {}
    for k, v in as_dict(pubs.get("overrides")).items():
        nk = normalize_doi(str(k)) or str(k).strip().lower()
        overrides[nk] = v
    used = set()
    for w in works:
        for k in w["_keys"]:
            if k != w["id"] and k in overrides:
                _apply_override(w, overrides[k], warnings, carry_only=True)
                used.add(k)
        if w["id"] in overrides:
            _apply_override(w, overrides[w["id"]], warnings)
            used.add(w["id"])
        if w["status"] not in ("accepted", "under-review") and w.get("status_note") \
                and w["id"] not in overrides:
            w["status_note"] = None
        if not w.get("_role_override") and "_manual" not in w:
            w["role"] = _role(w["authors"], w["my_position"])
    unused = sorted(set(overrides) - used)
    if unused:
        warnings.append("overrides for ids that match no work: %s" % ", ".join(unused))

    rules, known = _compile_topics(pubs, warnings)
    for w in works:
        _assign_topics(w, rules, known, warnings)

    # Hide: a hidden primary hides the record; a hidden version only drops that version.
    hide = set()
    for h in as_list(pubs.get("hide")):
        hide.add(normalize_doi(str(h)) or str(h).strip().lower())
    visible, hidden = [], []
    for w in works:
        if w["id"] in hide or (w.get("doi") and w["doi"] in hide):
            hidden.append(w)
            continue
        w["versions"] = [v for v in w["versions"] if v["doi"] not in hide]
        visible.append(w)

    for w in visible:
        if not w["_orcid_linked"]:
            warnings.append("found only by name search, please confirm: %s \"%s\" (%s)"
                            % (w["id"], w["title"], ", ".join(w["sources"])))
        if w["_retracted"]:
            warnings.append("OpenAlex marks %s as retracted" % w["id"])
        if not w.get("title"):
            warnings.append("%s has no title" % w["id"])
        if (rules or known) and not w["topics"]:
            warnings.append("%s matches no topic; add topics in publications.yml overrides "
                            "or a topic_rules pattern" % w["id"])
        if w.get("contribution") and w.get("role") in ("first", "senior"):
            warnings.append("%s has a contribution note but he is %s author; notes are meant "
                            "for middle-author papers" % (w["id"], w["role"]))
        if str(w["id"]).startswith("orcid:"):
            warnings.append("ORCID item without a DOI shown from ORCID data only: %s \"%s\" "
                            "(hide it by this id, or add a manual entry)"
                            % (w["id"], (w["title"] or "")[:90]))

    visible.sort(key=lambda w: (date_sort_key(w.get("date")), w.get("title") or ""), reverse=True)
    return visible, hidden


def finalize_work(w):
    out = {}
    for k in WORK_KEYS:
        v = w.get(k)
        if k == "authors":
            v = [_clean_author(a) for a in (v or [])]
        out[k] = v
    for k in sorted(w):
        if k not in out and not str(k).startswith("_"):
            out[k] = w[k]
    if out["featured"] is None:
        out["featured"] = False
    for k in ("versions", "topics", "alt_ids", "sources"):
        if out[k] is None:
            out[k] = []
    return out


# --------------------------------------------------------------------------
# first_seen, new/removed, sanity guard
# --------------------------------------------------------------------------

def carry_first_seen(works, prev_works, today):
    index = {}
    for p in prev_works or []:
        fs = p.get("first_seen")
        if not fs:
            continue
        for k in _keyset(p):
            if k not in index or fs < index[k]:
                index[k] = fs
    for w in works:
        seen = [index[k] for k in _keyset(w) if k in index]
        w["first_seen"] = min(seen) if seen else today
    return works


def diff_works(works, prev_works):
    """new: no key overlap with any previous work; removed: previous works with
    no key overlap with any current work; promoted: preprint now published."""
    if prev_works is None:
        return [], [], []
    prev_index = {}
    for p in prev_works:
        for k in _keyset(p):
            prev_index.setdefault(k, p)
    cur_keys = set()
    new, promoted = [], []
    for w in works:
        ks = _keyset(w)
        cur_keys |= ks
        match = next((prev_index[k] for k in ks if k in prev_index), None)
        if match is None:
            new.append(_brief(w))
        elif match.get("status") == "preprint" and w.get("status") == "published" \
                and match.get("id") != w.get("id"):
            b = _brief(w)
            b["previous_id"] = match.get("id")
            promoted.append(b)
    removed = [_brief(p) for p in prev_works if not (_keyset(p) & cur_keys)]
    return new, removed, promoted


def _brief(w):
    return {"id": w.get("id"), "title": w.get("title"), "venue": w.get("venue"),
            "year": w.get("year"), "sources": w.get("sources") or []}


def sanity_check(n_new, prev_works, hidden_keys):
    """True if the visible count dropped by more than 15% (not counting works
    hidden on purpose since the last run)."""
    if not prev_works:
        return False
    n_prev = len(prev_works)
    now_hidden = sum(1 for p in prev_works if _keyset(p) & hidden_keys)
    return (n_new + now_hidden) < n_prev * (1.0 - SANITY_DROP)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def _h_index(counts):
    cs = sorted((c or 0 for c in counts), reverse=True)
    h = 0
    for i, c in enumerate(cs, 1):
        if c >= i:
            h = i
    return h


def _is_public(w, this_year=None):
    """Counted in the per-year charts: published, posted as a preprint, or
    otherwise reachable by DOI (e.g. an accepted paper's preprint), and not
    dated after the current year (a forthcoming chapter dated next year).
    scripts/build.py uses the same rule for the chart on /publications/."""
    if this_year is not None and isinstance(w.get("year"), int) and w["year"] > this_year:
        return False
    return w.get("status") in ("published", "preprint") or bool(w.get("doi"))


def build_metrics(works, hidden, raw, now, today):
    oa = as_dict(raw.get("openalex"))
    author = as_dict(oa.get("author"))
    stats = as_dict(author.get("summary_stats"))
    hidden_keys = set()
    for w in hidden:
        hidden_keys |= _keyset(w)
    oa_works = [w for w in as_list(oa.get("works"))
                if w.get("type") not in OPENALEX_EXCLUDED_TYPES and not w.get("is_paratext")]
    kept = [w for w in oa_works if normalize_doi(w.get("doi")) not in hidden_keys]
    hidden_cites = sum((w.get("cited_by_count") or 0) for w in oa_works
                       if normalize_doi(w.get("doi")) in hidden_keys)
    cited = author.get("cited_by_count")
    h, i10 = stats.get("h_index"), stats.get("i10_index")
    adjusted = False
    if hidden_cites and cited is not None:
        cited = max(0, cited - hidden_cites)
        counts = [w.get("cited_by_count") or 0 for w in kept]
        h, i10 = _h_index(counts), sum(1 for c in counts if c >= 10)
        adjusted = True

    this_year = int(today[:4])
    works_by_year, role_by_year = {}, {}
    charted = [w for w in works if _is_public(w, this_year)]
    for w in charted:
        y = w.get("year")
        if not y:
            continue
        works_by_year[y] = works_by_year.get(y, 0) + 1
        slot = role_by_year.setdefault(y, {"first_or_senior": 0, "coauthor": 0})
        if w.get("role") in ("first", "senior"):
            slot["first_or_senior"] += 1
        else:
            slot["coauthor"] += 1
    cites_by_year = {}
    for w in kept:
        for c in as_list(w.get("counts_by_year")):
            y = c.get("year")
            if y:
                cites_by_year[y] = cites_by_year.get(y, 0) + (c.get("cited_by_count") or 0)
    years = set(works_by_year) | {y for y, v in cites_by_year.items() if v}
    counts_by_year = []
    if years:
        for y in range(min(years), max(max(years), this_year) + 1):
            counts_by_year.append({"year": y, "works_count": works_by_year.get(y, 0),
                                   "cited_by_count": cites_by_year.get(y, 0)})
    by_year_role = []
    if role_by_year:
        for y in range(min(role_by_year), max(max(role_by_year), this_year) + 1):
            r = role_by_year.get(y, {"first_or_senior": 0, "coauthor": 0})
            by_year_role.append({"year": y, "first_or_senior": r["first_or_senior"],
                                 "coauthor": r["coauthor"]})
    return {
        "generated": now,
        "works_count": len(works),
        "cited_by_count": cited,
        "h_index": h,
        "i10_index": i10,
        "counts_by_year": counts_by_year,
        "by_year_role": by_year_role,
        "charted_works_count": sum(1 for w in charted if w.get("year")),
        "first_or_senior_count": sum(1 for w in works if w.get("role") in ("first", "senior")),
        "preprint_count": sum(1 for w in works if w.get("type") == "preprint"),
        "openalex_works_count": author.get("works_count"),
        "openalex_author_id": author.get("id"),
        "openalex_counts_by_year": [
            {"year": c.get("year"), "works_count": c.get("works_count"),
             "cited_by_count": c.get("cited_by_count")}
            for c in sorted(as_list(author.get("counts_by_year")), key=lambda c: c.get("year") or 0)],
        "adjusted_for_hidden": adjusted,
        "notes": ("works_count, first_or_senior_count and preprint_count count every visible "
                  "work. counts_by_year.works_count and by_year_role count only works that are "
                  "publicly available (status published or preprint, or any work with a DOI), "
                  "by year; in-press and under-review items without a DOI, and works dated "
                  "after the current year, are left out until they appear, so by_year_role "
                  "sums to charted_works_count. "
                  "counts_by_year.cited_by_count is citations received in that year, summed "
                  "from OpenAlex per-work counts. Totals and h-index are OpenAlex author "
                  "summary_stats. openalex_counts_by_year is OpenAlex's author-level series "
                  "as returned."),
        "source": "openalex",
    }


# --------------------------------------------------------------------------
# Grants
# --------------------------------------------------------------------------

def _ym(s):
    d = clean_date(s)
    return d[:7] if d and len(d) >= 7 else d


def _grant_title(t):
    t = re.sub(r"\s+", " ", strip_html(t) or "").strip()
    if t.endswith(".") and not t.endswith(".."):
        t = t[:-1].rstrip()
    return t or None


def _grant_status(end, today):
    if not end:
        return "active"
    e = date_to_ordinal(end, end=True)
    t = date_to_ordinal(today)
    if e is None or t is None:
        return "active"
    return "active" if e >= t else "completed"


def build_grants(raw, content, now, today, warnings):
    ident = content["ident"]
    funding = as_dict(content.get("funding"))
    show_amounts = bool(funding.get("show_amounts"))
    enrich = {core_project(k): v for k, v in as_dict(funding.get("enrich")).items()}
    hide = {str(h).strip().upper() for h in as_list(funding.get("hide"))}
    me = ident["me"]
    rep = as_dict(raw.get("reporter"))
    profile_ids = set()
    for v in as_list(rep.get("pi_profile_ids")) + as_list(
            as_dict(ident.get("nih_reporter")).get("pi_profile_ids")):
        try:
            profile_ids.add(int(v))
        except (TypeError, ValueError):
            pass
    configured = {core_project(c) for c in as_list(as_dict(ident.get("nih_reporter")).get("core_projects"))}

    by_core = {}
    for p in as_list(rep.get("projects")):
        core = core_project(p.get("core_project_num") or p.get("project_num"))
        if not core:
            continue
        pis = as_list(p.get("principal_investigators"))
        mine = [x for x in pis if (x.get("profile_id") in profile_ids) or pi_is_me(x, me)]
        if not mine and core not in configured:
            continue
        by_core.setdefault(core, []).append(p)

    grants = []
    for core, rows in sorted(by_core.items()):
        rows.sort(key=lambda p: (p.get("fiscal_year") or 0, p.get("budget_start") or "",
                                 p.get("appl_id") or 0))
        latest = rows[-1]
        starts = [clean_date(p.get("project_start_date")) for p in rows if p.get("project_start_date")]
        start = min(starts) if starts else None
        end = clean_date(latest.get("project_end_date")) or max(
            [clean_date(p.get("project_end_date")) for p in rows if p.get("project_end_date")] or [None])
        insts = []
        for p in rows:
            name = pretty_org(as_dict(p.get("organization")).get("org_name"))
            if name and name not in insts:
                insts.append(name)
        pis = as_list(latest.get("principal_investigators"))
        pi_names = []
        for x in pis:
            n = pretty_person(x.get("full_name") or " ".join(
                v for v in (x.get("first_name"), x.get("last_name")) if v))
            if n and n not in pi_names:
                pi_names.append(n)
        mine = [x for x in pis if (x.get("profile_id") in profile_ids) or pi_is_me(x, me)]
        if mine and len(pis) == 1:
            role = "PI"
        elif mine:
            role = "Contact PI" if mine[0].get("is_contact_pi") else "MPI"
        else:
            role = None
        ic = as_dict(latest.get("agency_ic_admin"))
        amount = sum(int(p.get("award_amount") or 0) for p in rows)
        g = {
            "id": core,
            "title": _grant_title(latest.get("project_title")),
            "display_title": None,
            "funder": "NIH",
            "agency": ic.get("abbreviation"),
            "agency_name": ic.get("name"),
            "mechanism": latest.get("activity_code"),
            "role": role,
            "start": _ym(start),
            "end": _ym(end),
            "status": _grant_status(end, today),
            "institutions": insts,
            "url": "https://reporter.nih.gov/project-details/%s" % latest.get("appl_id"),
            "description": None,
            "source": "nih_reporter",
            "pis": pi_names,
            "amount_total": amount if show_amounts else None,
            "fiscal_years": sorted({p.get("fiscal_year") for p in rows if p.get("fiscal_year")}),
            "project_nums": [p.get("project_num") for p in rows if p.get("project_num")],
        }
        e = as_dict(enrich.get(core))
        for k in ("role", "description", "display_title", "url"):
            if e.get(k):
                g[k] = e[k]
        grants.append(g)

    # ORCID fundings (skip ones that duplicate a RePORTER project).
    known_cores = {g["id"] for g in grants}
    for f in as_list(as_dict(raw.get("orcid")).get("fundings")):
        nums = [core_project(e.get("value")) for e in as_list(f.get("external_ids"))]
        if any(n in known_cores for n in nums if n):
            continue
        title = strip_html(f.get("title"))
        if any(title and g["title"] and title_ratio(title, g["title"]) > 0.9 for g in grants):
            continue
        end = clean_date(f.get("end"))
        grants.append({
            "id": "orcid:%s" % f.get("put_code"), "title": title, "display_title": None,
            "funder": f.get("organization"), "agency": None, "agency_name": None,
            "mechanism": f.get("type"), "role": None, "start": _ym(f.get("start")),
            "end": _ym(end), "status": _grant_status(end, today), "institutions": [],
            "url": f.get("url"), "description": None, "source": "orcid", "pis": [],
            "amount_total": None, "fiscal_years": [], "project_nums": [n for n in nums if n],
        })

    for m in as_list(funding.get("manual")):
        if not isinstance(m, dict) or not m.get("title"):
            continue
        end = clean_date(m.get("end"))
        gid = str(m.get("id") or re.sub(r"[^a-z0-9]+", "-", m["title"].lower())[:50].strip("-"))
        g = {
            "id": gid, "title": m.get("title"), "display_title": m.get("display_title"),
            "funder": m.get("funder"), "agency": m.get("agency"), "agency_name": None,
            "mechanism": m.get("mechanism"), "role": m.get("role"),
            "start": _ym(m.get("start")) if m.get("start") else None,
            "end": _ym(end) if end else None,
            "status": m.get("status") if m.get("status") in ("active", "completed")
            else _grant_status(end, today),
            "institutions": as_list(m.get("institutions")), "url": m.get("url"),
            "description": m.get("description"), "source": "manual",
            "pis": [str(p) for p in as_list(m.get("pis"))],
            "amount_total": m.get("amount_total") if show_amounts else None,
            "fiscal_years": [], "project_nums": [],
        }
        for k, v in m.items():  # extra keys (date_precision, ...) pass through
            if k not in g and not str(k).startswith("_"):
                g[k] = v
        grants.append(g)

    grants = [g for g in grants if str(g["id"]).upper() not in hide]
    grants.sort(key=lambda g: (0 if g["status"] == "active" else 1,
                               -(date_to_ordinal(g.get("start")) or 0), g["id"]))
    return {"generated": now, "show_amounts": show_amounts, "grants": grants}


# --------------------------------------------------------------------------
# Software
# --------------------------------------------------------------------------

def build_software(raw, content, now, warnings):
    site = as_dict(content.get("site"))
    sw = as_dict(site.get("software"))
    include = [str(x) for x in as_list(sw.get("include"))]
    exclude = {str(x).lower() for x in as_list(sw.get("exclude"))}
    gh = as_dict(raw.get("github"))
    repos = {}
    for r in as_list(gh.get("repos")):
        if r.get("name"):
            repos[r["name"].lower()] = r
    shown = []
    if include:
        for name in include:
            r = repos.get(name.lower())
            if r is None:
                warnings.append("software.include lists %r but no such public repo" % name)
            elif name.lower() not in exclude:
                shown.append(r)
    else:
        shown = sorted((r for r in repos.values() if not r.get("fork")
                        and r["name"].lower() not in exclude),
                       key=lambda r: r.get("pushed_at") or "", reverse=True)
    keep = ["name", "description", "html_url", "homepage", "language", "stargazers_count",
            "pushed_at", "topics", "fork", "archived", "license"]
    shown_names = {r["name"].lower() for r in shown}
    return {
        "generated": now,
        "user": gh.get("user"),
        "repos": [{k: r.get(k) for k in keep} for r in shown],
        "hidden": sorted(r["name"] for r in repos.values() if r["name"].lower() not in shown_names),
    }


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def load_content(content_dir):
    site = load_yaml(os.path.join(content_dir, "site.yml"), {})
    site = site if isinstance(site, dict) else {}
    pubs = load_yaml(os.path.join(content_dir, "publications.yml"), {})
    funding = load_yaml(os.path.join(content_dir, "funding.yml"), {})
    return {"site": site, "ident": identity_config(site),
            "publications": pubs if isinstance(pubs, dict) else {},
            "funding": funding if isinstance(funding, dict) else {}}


def load_raw(raw_dir):
    out = {}
    for name in ("orcid", "openalex", "crossref", "europepmc", "reporter", "github"):
        out[name] = load_json(os.path.join(raw_dir, "%s.json" % name), None)
    return out


def run(content, raw, prev, today, now):
    """Pure merge: returns dict of output documents plus status details."""
    warnings, links = [], []
    works, hidden = merge_publications(raw, content, prev, today, warnings, links)
    prev_pubs = as_dict(prev.get("publications"))
    prev_works = prev_pubs.get("works") if prev_pubs else None
    hidden_keys = set()
    for w in hidden:
        hidden_keys |= _keyset(w)

    guard = sanity_check(len(works), prev_works, hidden_keys)
    if guard:
        warnings.insert(0, "sanity guard: visible works fell from %d to %d (more than %d%%); "
                           "kept the previous data/publications.json"
                        % (len(prev_works), len(works), int(SANITY_DROP * 100)))
        out_works = prev_works
        new = removed = promoted = []
    else:
        carry_first_seen(works, prev_works, today)
        out_works = [finalize_work(w) for w in works]
        new, removed, promoted = diff_works(out_works, prev_works)
    publications = {"generated": now, "works": out_works}
    metrics = build_metrics(out_works, [finalize_work(w) for w in hidden], raw, now, today)
    grants = build_grants(raw, content, now, today, warnings)
    software = build_software(raw, content, now, warnings)
    prev_grants = as_dict(prev.get("grants"))
    new_grants = []
    if prev_grants:
        old_ids = {g.get("id") for g in as_list(prev_grants.get("grants"))}
        new_grants = [{"id": g["id"], "title": g.get("display_title") or g.get("title"),
                       "funder": g.get("funder")} for g in grants["grants"]
                      if g["id"] not in old_ids]
    return {
        "publications": publications, "metrics": metrics, "grants": grants,
        "software": software, "guard": guard, "warnings": warnings, "links": links,
        "new_works": new, "removed_works": removed, "promoted_works": promoted,
        "new_grants": new_grants, "hidden_count": len(hidden),
        "first_run": prev_works is None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--content-dir", default=CONTENT_DIR)
    ap.add_argument("--data-dir", default=DATA_DIR)
    args = ap.parse_args(argv)
    started = utc_now_iso()
    today = args.today or today_iso()
    data_dir = args.data_dir
    raw_dir = os.path.join(data_dir, "raw") if args.data_dir != DATA_DIR else RAW_DIR
    content = load_content(args.content_dir)
    raw = load_raw(raw_dir)
    prev = {"publications": load_json(os.path.join(data_dir, "publications.json"), None),
            "grants": load_json(os.path.join(data_dir, "grants.json"), None)}
    fetch_status = load_json(os.path.join(raw_dir, "fetch_status.json"), {}) or {}
    now = utc_now_iso()
    res = run(content, raw, prev, today, now)

    if not res["guard"]:
        write_json_atomic(os.path.join(data_dir, "publications.json"), res["publications"])
    write_json_atomic(os.path.join(data_dir, "metrics.json"), res["metrics"])
    write_json_atomic(os.path.join(data_dir, "grants.json"), res["grants"])
    write_json_atomic(os.path.join(data_dir, "software.json"), res["software"])

    sources = dict(fetch_status.get("sources") or {})
    for name, payload in raw.items():
        if name not in sources:
            sources[name] = {"ok": payload is not None, "count": None,
                             "error": None if payload is not None else "no raw data",
                             "used_cache": payload is not None,
                             "fetched_at": (payload or {}).get("fetched_at")}
    warnings = list(res["warnings"])
    if res["first_run"]:
        warnings.append("first run: no previous data/publications.json, so new_works is empty")
    failed = [k for k, v in sources.items() if not v.get("ok")]
    report = {
        "started": fetch_status.get("started") or started,
        "finished": utc_now_iso(),
        "ok": not failed and not res["guard"],
        "sources": sources,
        "new_works": res["new_works"],
        "removed_works": res["removed_works"],
        "new_grants": res["new_grants"],
        "warnings": warnings,
        "promoted_works": res["promoted_works"],
        "sanity_guard": {"tripped": res["guard"],
                         "previous": len(as_list(as_dict(prev["publications"]).get("works"))),
                         "current": len(res["publications"]["works"]) if not res["guard"] else None,
                         "max_drop": SANITY_DROP},
        "counts": {"visible_works": len(res["publications"]["works"]),
                   "hidden_works": res["hidden_count"],
                   "grants": len(res["grants"]["grants"]),
                   "repos_shown": len(res["software"]["repos"])},
        "version_links": res["links"],
        "fetch": {"started": fetch_status.get("started"), "finished": fetch_status.get("finished")},
        "merge": {"started": started, "finished": utc_now_iso(), "today": today},
    }
    write_json_atomic(os.path.join(data_dir, "run_report.json"), report)
    log("merge: %d visible works (%d hidden), %d grants, %d repos shown, %d warnings%s"
        % (report["counts"]["visible_works"], res["hidden_count"], report["counts"]["grants"],
           report["counts"]["repos_shown"], len(warnings),
           "; SANITY GUARD TRIPPED" if res["guard"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
