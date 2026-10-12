#!/usr/bin/env python3
"""Build zdozier.com: data/*.json + content/*.yml + templates/ -> _site/.

Offline. Reads the merged data snapshot written by merge.py and the curated
YAML in content/, renders every page with Jinja2 (autoescaped), draws the
charts as inline SVG (scripts/charts.py), and writes the feed, sitemap,
BibTeX and JSON exports, robots.txt, CNAME and 404 page. static/ is copied
verbatim. scripts/pdf.py prints the CV to _site/cv.pdf afterwards.

Usage:
  python3 scripts/build.py                 # -> _site/
  python3 scripts/build.py --out /tmp/site --data-dir tests/fixtures/data

Missing or empty optional data (no grants, no software.json, no run report)
never stops the build; the affected section is left out.

Runs on Python 3.8 (Jinja2 2.11, PyYAML 5.4) and 3.12.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charts  # noqa: E402
from common import load_yaml as _load_content_yaml  # noqa: E402

DEFAULTS = {
    "content": os.path.join(ROOT, "content"),
    "data": os.path.join(ROOT, "data"),
    "templates": os.path.join(ROOT, "templates"),
    "static": os.path.join(ROOT, "static"),
    "out": os.path.join(ROOT, "_site"),
}
BUILD_MARKER = ".zdozier-build"

EM_DASH = "\u2014"
EN_DASH = "\u2013"
NBSP = "\u00a0"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTHS_SHORT = [m[:3] for m in MONTHS]

SOURCE_LABELS = [
    ("orcid", "ORCID", "Works list curated on his ORCID record"),
    ("openalex", "OpenAlex", "Discovery, venues, open access links, citation counts, h-index"),
    ("crossref", "Crossref", "Authors, volume, issue, pages, preprint links"),
    ("europepmc", "Europe PMC", "PubMed IDs, ahead-of-print papers, preprints"),
    ("reporter", "NIH RePORTER", "NIH grants"),
    ("github", "GitHub", "Public code repositories"),
]

ROLE_LABELS = {"first": "First author", "senior": "Senior author"}
TYPE_LABELS = {"review": "Review", "conference": "Conference paper",
               "chapter": "Book chapter", "report": "Report"}
TYPE_OPTIONS = [
    ("type:article", "Journal articles"),
    ("type:review", "Reviews"),
    ("type:conference", "Conference papers"),
    ("type:preprint", "Preprints"),
    ("type:chapter", "Book chapters"),
    ("type:report", "Reports"),
    ("status:accepted", "Accepted or in press"),
    ("status:under-review", "Under review"),
]
CV_PUB_SECTIONS = [
    ("articles", "Peer-Reviewed Journal Articles (Original Research)"),
    ("reviews", "Peer-Reviewed Review Articles"),
    ("conference", "Peer-Reviewed Conference Papers"),
    ("accepted", "Accepted and In Press"),
    ("preprints", "Preprints and Manuscripts Under Review"),
    ("chapters", "Book Chapters"),
    ("nonpeer", "Non-Peer-Reviewed Publications"),
]
NAV = [
    ("research", "Research", "/#research"),
    ("publications", "Publications", "/publications/"),
    ("funding", "Funding", "/funding/"),
    ("team", "Team", "/team/"),
    ("cv", "CV", "/cv/"),
]
# Formats a phone number could take in rendered text. The site must never
# show one (SPEC section 1); the build fails if any appears.
PHONE_RES = [
    re.compile(r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}"),
    re.compile(r"(?<![\d./-])\d{3}-\d{3}-\d{4}(?![\d-])"),
    re.compile(r"\+1[\s.-]?\d{3}[\s.-]\d{3}[\s.-]\d{4}"),
]


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_yaml(path, default=None):
    """content/*.yml, read as merge.py reads it: a missing file gives `default`;
    a syntax error or a repeated key stops the build with a one-line message."""
    if not os.path.exists(path):
        return default
    return _load_content_yaml(path, {} if default is None else default)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        log("build: warning: could not parse %s (%s); treating it as missing" % (path, exc))
        return default


def scrub(obj):
    """Replace em dashes in any loaded text with a spaced en dash (render-time only)."""
    if isinstance(obj, str):
        if EM_DASH in obj:
            obj = re.sub(r"\s*\u2014\s*", " " + EN_DASH + " ", obj)
        return obj
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return dict((k, scrub(v)) for k, v in obj.items())
    return obj


def as_list(x):
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def as_dict(x):
    return x if isinstance(x, dict) else {}


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def parse_now(value=None):
    value = value or os.environ.get("SITE_BUILD_TIME")
    if value:
        d = parse_iso(value)
        if d is None:
            raise SystemExit("build: could not parse build time %r" % value)
        return d
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        t += "T00:00:00Z"
    t = t.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M%z"):
        try:
            d = dt.datetime.strptime(t, fmt)
            return d.astimezone(dt.timezone.utc)
        except ValueError:
            continue
    return None


def date_parts(s):
    """'2024-11-05' -> (2024, 11, 5); '2024-11' -> (2024, 11, None); '2024' -> (2024, None, None)."""
    if s is None:
        return None
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(s).strip())
    if not m:
        return None
    y = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else None
    d = int(m.group(3)) if m.group(3) else None
    if mo is not None and not 1 <= mo <= 12:
        mo, d = None, None
    return (y, mo, d)


def fmt_month_year(s, long=False):
    p = date_parts(s)
    if not p:
        return ""
    y, m, _ = p
    if m is None:
        return str(y)
    return "%s %d" % ((MONTHS if long else MONTHS_SHORT)[m - 1], y)


def fmt_long_date(d):
    return "%s %d, %d" % (MONTHS[d.month - 1], d.day, d.year)


def fmt_period(start, end, precision=None, long=False, present="present"):
    if precision == "year":
        a = str(date_parts(start)[0]) if date_parts(start) else ""
        b = str(date_parts(end)[0]) if date_parts(end) else present
        return a + EN_DASH + b if a else b
    a = fmt_month_year(start, long)
    b = fmt_month_year(end, long) if end else present
    if not a:
        return b
    return "%s %s %s" % (a, EN_DASH, b)


def work_sort_key(w):
    p = date_parts(w.get("date")) or ((w.get("year") or 0), None, None)
    y, m, d = p
    if w.get("year") and not y:
        y = w.get("year")
    if m is None:
        # Undated accepted or under-review work is the newest in its year;
        # undated published work sorts to the end of its year.
        m = 13 if w.get("status") in ("accepted", "under-review") else 0
    return (y or 0, m, d or 0)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def slugify(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def work_anchor(w):
    return "pub-" + slugify(w.get("doi") or w.get("id") or w.get("title") or "")


def page_range(p):
    if not p:
        return ""
    return re.sub(r"(?<=\w)\s*-\s*(?=\w)", EN_DASH, str(p))


def sentence_case_first(s):
    s = str(s or "")
    return s[:1].upper() + s[1:]


def end_punct(s):
    s = (s or "").rstrip()
    return s if s[-1:] in ".?!" else s + "."


def doi_url(doi):
    return "https://doi.org/" + doi if doi else None


def is_http(url):
    return isinstance(url, str) and re.match(r"^https?://", url) is not None


# --------------------------------------------------------------------------
# Authors
# --------------------------------------------------------------------------

def _split_apa(a):
    """Return (family, initials, suffix) from an author record."""
    apa = a.get("apa")
    family = a.get("family") or ""
    initials = ""
    suffix = a.get("suffix") or ""
    if apa and "," in apa:
        parts = [p.strip() for p in apa.split(",")]
        family = parts[0]
        initials = parts[1] if len(parts) > 1 else ""
        if len(parts) > 2 and not suffix:
            suffix = parts[2]
    elif a.get("given"):
        initials = " ".join(t[0] + "." for t in re.split(r"[\s]+", a["given"]) if t)
    return family, initials, suffix


def is_etal(a):
    n = (a.get("name") or a.get("family") or "").strip().lower()
    return n in ("et al.", "et al", "others")


def author_site_name(a):
    """Compact PubMed-like form: 'Butzin-Dozier Z', 'Wang LC', 'Colford JM Jr'."""
    if a.get("is_group") or (not a.get("family") and a.get("name")):
        return a.get("name") or a.get("family") or ""
    family, initials, suffix = _split_apa(a)
    ini = re.sub(r"[.\s\u2010-]", "", initials)
    out = family + (NBSP + ini if ini else "")
    if suffix:
        out += NBSP + suffix.rstrip(".")
    return out


def author_apa_name(a):
    """'Family, G. I.' with non-breaking spaces so one name never splits across lines."""
    if a.get("is_group") or (not a.get("family") and a.get("name")):
        return a.get("name") or a.get("family") or ""
    if a.get("apa"):
        return a["apa"].replace(" ", NBSP)
    family, initials, suffix = _split_apa(a)
    out = family + ("," + NBSP + initials.replace(" ", NBSP) if initials else "")
    if suffix:
        out += "," + NBSP + suffix
    return out


def _me_html(text, star=False):
    return Markup('<strong class="me">%s</strong>%s') % (text, Markup("*") if star else "")


def authors_site_html(w, limit=8):
    """Author line for the publications page: up to `limit` names, then a <details> with all."""
    authors = as_list(w.get("authors"))
    if not authors:
        return Markup("")
    names = []
    for a in authors:
        nm = author_site_name(a)
        names.append(_me_html(nm) if a.get("is_me") else escape(nm))
    n = len(names)
    me_idx = next((i for i, a in enumerate(authors) if a.get("is_me")), None)
    if n <= limit + 1:
        return Markup('<span class="au-list">%s</span>') % Markup(", ").join(names)
    if me_idx is None or me_idx < limit:
        shown = names[:limit]
        short = Markup(", ").join(shown) + Markup(",\u00a0\u2026")
        hidden = n - limit
    else:
        shown = names[:limit - 2] + [names[me_idx]]
        short = Markup(", ").join(names[:limit - 2]) + Markup(",\u00a0\u2026, ") + names[me_idx]
        if me_idx < n - 1:
            short += Markup(",\u00a0\u2026")
        hidden = n - len(shown)
    full = Markup(", ").join(names)
    return Markup(
        '<span class="au-short">%s</span>'
        '<details class="au-all"><summary><span class="more-closed">+ %d more</span>'
        '<span class="more-open">Show fewer</span><span class="sr-only"> (all %d authors)</span></summary>'
        '<span class="au-list">%s</span></details>'
    ) % (short, hidden, n, full)


def authors_apa_html(w):
    """APA 7 author list for the CV. More than 20 authors: first 19, ellipsis, last."""
    authors = as_list(w.get("authors"))
    star = bool(w.get("contribution"))
    names = []
    for a in authors:
        nm = author_apa_name(a)
        names.append(_me_html(nm, star) if a.get("is_me") else escape(nm))
    n = len(names)
    if n == 0:
        return Markup("")
    etal = is_etal(authors[-1])
    if etal:
        body = names[:-1]
        if not body:
            return Markup("et al.")
        return Markup(", ").join(body) + Markup(", et al.")
    if n == 1:
        return names[0]
    if n <= 20:
        return Markup(", ").join(names[:-1]) + Markup(", &amp; ") + names[-1]
    me_idx = next((i for i, a in enumerate(authors) if a.get("is_me")), None)
    if me_idx is not None and 19 <= me_idx < n - 1:
        return (Markup(", ").join(names[:18]) + Markup(", \u2026 ") + names[me_idx]
                + Markup(", \u2026 ") + names[-1])
    return Markup(", ").join(names[:19]) + Markup(", \u2026 ") + names[-1]


# --------------------------------------------------------------------------
# Works: display fields
# --------------------------------------------------------------------------

def is_preprintish(w):
    """Not yet accepted: a posted preprint or a manuscript under review."""
    return w.get("status") in ("preprint", "under-review")


def status_tag(w):
    st = w.get("status")
    note = (w.get("status_note") or "").strip()
    if st == "preprint":
        return "Preprint"
    if st == "under-review":
        return "Under review"
    if st == "accepted":
        if note.lower() in ("in press", "forthcoming"):
            return sentence_case_first(note.lower())
        return "Accepted"
    return None


def accepted_journal(w):
    note = (w.get("status_note") or "").strip()
    if w.get("status") == "accepted" and note and note.lower() not in ("in press", "forthcoming"):
        return note
    return None


def venue_html(w):
    """Venue line for the site (italic venue plus volume, issue, pages)."""
    venue = w.get("venue")
    journal = accepted_journal(w)
    parts = []
    if journal:
        main = Markup("<i>%s</i>") % journal
        if venue:
            main += Markup("; preprint on <i>%s</i>") % venue
        parts.append(main)
    elif w.get("type") == "chapter" and venue:
        main = Markup("In <i>%s</i>") % venue
        if w.get("publisher"):
            main += Markup(" (%s)") % w["publisher"]
        parts.append(main)
    elif venue:
        parts.append(Markup("<i>%s</i>") % venue)
    elif w.get("status_note"):
        parts.append(escape(w["status_note"]))
    loc = ""
    if w.get("volume"):
        loc = str(w["volume"])
        if w.get("issue"):
            loc += "(%s)" % w["issue"]
    if w.get("pages"):
        loc += (":" if loc else "") + page_range(w["pages"])
    if loc and not journal:
        parts.append(Markup('<span class="num">%s</span>') % loc)
    return Markup(" ").join(parts)


def venue_short_html(w):
    """Venue only (no volume or pages), for compact lists."""
    journal = accepted_journal(w)
    if journal:
        return Markup("<i>%s</i>") % journal
    if w.get("venue"):
        return Markup("<i>%s</i>") % w["venue"]
    return escape(w.get("status_note") or "")


def oa_label(url):
    u = (url or "").lower()
    if u.endswith(".pdf") or "/pdf" in u or "pdf" in u.rsplit("/", 1)[-1]:
        return "PDF"
    return "Free full text"


def badges(w):
    out = []
    if w.get("doi"):
        out.append({"label": "DOI", "url": doi_url(w["doi"]), "title": "doi:" + w["doi"]})
    if w.get("oa_url") and is_http(w["oa_url"]):
        out.append({"label": oa_label(w["oa_url"]), "url": w["oa_url"], "title": "Open access copy"})
    for v in as_list(w.get("versions")):
        url = v.get("url") if is_http(v.get("url")) else doi_url(v.get("doi"))
        if not url:
            continue
        label = (v.get("server") or "Preprint") + (" %s" % v["year"] if v.get("year") else "")
        out.append({"label": label, "url": url, "title": "Earlier version"})
    if w.get("pmid"):
        out.append({"label": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % w["pmid"], "title": "PMID " + str(w["pmid"])})
    if w.get("code_url") and is_http(w["code_url"]):
        out.append({"label": "Code", "url": w["code_url"], "title": "Analysis code"})
    if w.get("cited_by"):
        n = int(w["cited_by"])
        url = None
        if w.get("openalex_id"):
            url = "https://openalex.org/works?filter=cites:%s" % w["openalex_id"]
        out.append({"label": "Cited by %d" % n, "url": url, "title": "Citations counted by OpenAlex", "num": True})
    return out


def prepare_work(w, topic_labels):
    v = dict(w)
    v["anchor"] = work_anchor(w)
    v["link"] = doi_url(w.get("doi")) or (w.get("url") if is_http(w.get("url")) else None)
    v["role_label"] = ROLE_LABELS.get(w.get("role"))
    v["status_label"] = status_tag(w)
    v["type_label"] = TYPE_LABELS.get(w.get("type"))
    v["authors_html"] = authors_site_html(w)
    v["apa_authors_html"] = authors_apa_html(w)
    v["venue_html"] = venue_html(w)
    v["venue_short_html"] = venue_short_html(w)
    v["badges"] = badges(w)
    v["preprintish"] = is_preprintish(w)
    v["topic_list"] = [t for t in as_list(w.get("topics")) if t in topic_labels]
    kinds = ["type:%s" % (w.get("type") or "other"), "status:%s" % (w.get("status") or "published")]
    v["kinds"] = " ".join(kinds)
    v["year_label"] = str(w.get("year") or "")
    # The year printed next to the venue. An in-press item has no real year
    # yet (its year only sorts it), so it shows none; the status tag already
    # says "In press", as the CV's "(in press)" does.
    v["year_shown"] = "" if is_in_press(w) else v["year_label"]
    return v


def is_in_press(w):
    return w.get("status") == "accepted" and (w.get("status_note") or "").strip().lower() == "in press"


# --------------------------------------------------------------------------
# CV citations
# --------------------------------------------------------------------------

def cv_section_of(w):
    t = w.get("type")
    st = w.get("status")
    if t == "chapter":
        return "chapters"
    if t in ("report", "other"):
        return "nonpeer"
    if st == "accepted":
        return "accepted"
    if st in ("preprint", "under-review") or t == "preprint":
        return "preprints"
    if t == "review":
        return "reviews"
    if t == "conference":
        return "conference"
    return "articles"


def cite_apa(w):
    """APA 7-style reference as HTML (his name bold, venue and volume italic)."""
    parts = []
    au = authors_apa_html(w)
    st = w.get("status")
    note = (w.get("status_note") or "").strip()
    if is_in_press(w):
        when = "in press"
    else:
        when = str(w.get("year") or "n.d.")
    if au:
        # APA ends the author element with a period: initials supply it, a
        # trailing group author ("... & N3C Consortium") needs one added.
        text = re.sub(r"<[^>]+>", "", str(au)).rstrip().rstrip("*")
        head = au + Markup(" " if text.endswith(".") else ". ") + Markup("(%s). ") % when
    else:
        head = Markup("(%s). ") % when
    title = w.get("title") or "Untitled"
    t = w.get("type")
    venue = w.get("venue")
    if t == "report":
        parts.append(head + Markup("<i>%s</i>") % end_punct(title))
        if venue:
            parts.append(escape(end_punct(venue)))
    elif t == "chapter":
        parts.append(head + escape(end_punct(title)))
        eds = [e for e in as_list(w.get("editors")) if e]
        ed_txt = ""
        if eds:
            names = []
            for e in eds:
                if "," in e:
                    fam, ini = [x.strip() for x in e.split(",", 1)]
                    names.append("%s %s" % (ini, fam))
                else:
                    names.append(e)
            ed_txt = (", ".join(names[:-1]) + ", & " + names[-1] if len(names) > 1 else names[0])
            ed_txt += " (Eds.), " if len(names) > 1 else " (Ed.), "
        if venue:
            parts.append(Markup("In %s<i>%s</i>.") % (ed_txt, venue))
        if w.get("publisher"):
            parts.append(escape(end_punct(w["publisher"])))
        if note and st == "accepted" and note.lower() != "in press":
            parts.append(escape(end_punct(note)))
    else:
        parts.append(head + escape(end_punct(title)))
        journal = accepted_journal(w)
        if journal:
            parts.append(Markup("Accepted for publication in <i>%s</i>.") % journal)
            if venue:
                parts.append(Markup("Preprint: <i>%s</i>.") % venue)
        elif t == "conference" and venue:
            pp = page_range(w.get("pages"))
            if pp:
                parts.append(Markup("In <i>%s</i> (pp. %s).") % (venue, pp))
            else:
                parts.append(Markup("In <i>%s</i>.") % venue)
        elif venue:
            seg = Markup("<i>%s</i>") % venue
            if w.get("volume"):
                seg += Markup(", <i>%s</i>") % w["volume"]
                if w.get("issue"):
                    seg += Markup("(%s)") % w["issue"]
            if w.get("pages"):
                seg += Markup(", %s") % page_range(w["pages"])
            parts.append(seg + Markup("."))
        elif note:
            parts.append(escape(end_punct(note)))
    link = doi_url(w.get("doi")) or (w.get("url") if is_http(w.get("url")) else None)
    if link:
        parts.append(Markup('<a href="%s">%s</a>') % (link, link))
    return Markup(" ").join(parts)


# --------------------------------------------------------------------------
# Grants
# --------------------------------------------------------------------------

def grant_view(g, long_dates=False, show_amounts=False):
    v = dict(g)
    v["url"] = g.get("url") if is_http(g.get("url")) else None
    v["display"] = g.get("display_title") or g.get("title") or "Untitled grant"
    bits = []
    if g.get("funder"):
        bits.append(g["funder"])
    if g.get("agency") and g.get("agency") != g.get("funder"):
        bits.append(g["agency"])
    v["funder_line"] = " / ".join(bits)
    if g.get("agency_name"):
        long_name = g["agency_name"] + (" (%s)" % g["agency"] if g.get("agency") else "")
        if g.get("funder") and g["funder"] not in long_name:
            long_name += ", " + g["funder"]
        v["funder_long"] = long_name
    else:
        v["funder_long"] = v["funder_line"]
    v["mechanism"] = g.get("mechanism")
    v["period"] = fmt_period(g.get("start"), g.get("end"), g.get("date_precision"), long=long_dates,
                             present="Present" if long_dates else "present")
    v["pis_line"] = ", ".join(as_list(g.get("pis")))
    v["show_id"] = g.get("source") == "nih_reporter" or bool(re.match(r"^[A-Z0-9]{8,}$", str(g.get("id") or "")))
    amt = g.get("amount_total")
    v["amount"] = "${:,.0f}".format(amt) if (show_amounts and isinstance(amt, (int, float)) and amt) else None
    return v


def split_grants(grants, today, long_dates=False, show_amounts=False):
    active, done = [], []
    for g in grants:
        st = g.get("status")
        if not st:
            endp = date_parts(g.get("end"))
            st = "active" if (not endp or (endp[0], endp[1] or 12) >= (today.year, today.month)) else "completed"
        gv = grant_view(g, long_dates, show_amounts)
        gv["status"] = st
        (active if st == "active" else done).append(gv)
    active.sort(key=lambda g: g.get("start") or "", reverse=True)
    done.sort(key=lambda g: (g.get("end") or "", g.get("start") or ""), reverse=True)
    return active, done


# --------------------------------------------------------------------------
# BibTeX and other exports
# --------------------------------------------------------------------------

_BIB_ESC = [("\\", r"\textbackslash{}"), ("{", r"\{"), ("}", r"\}"), ("&", r"\&"), ("%", r"\%"),
            ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")]


def bib_escape(s):
    s = str(s)
    out = []
    for ch in s:
        rep = None
        for a, b in _BIB_ESC:
            if ch == a:
                rep = b
                break
        out.append(rep if rep is not None else ch)
    return "".join(out)


def bib_title(s):
    """Escape and protect words with two or more capitals (COVID-19, N3C, SSRI)."""
    words = re.split(r"(\s+)", str(s))
    out = []
    for wd in words:
        if wd.strip() and sum(1 for c in wd if c.isupper()) >= 2:
            out.append("{" + bib_escape(wd) + "}")
        else:
            out.append(bib_escape(wd))
    return "".join(out)


def bib_name(a):
    if is_etal(a):
        return "others"
    if a.get("is_group") or not a.get("family"):
        return "{" + bib_escape(a.get("name") or a.get("family") or "") + "}"
    family = a["family"]
    given = a.get("given") or _split_apa(a)[1]
    suffix = a.get("suffix")
    if suffix:
        return "%s, %s, %s" % (bib_escape(family), bib_escape(suffix), bib_escape(given))
    return "%s, %s" % (bib_escape(family), bib_escape(given)) if given else bib_escape(family)


def bib_key(w, used):
    authors = as_list(w.get("authors"))
    fam = ""
    for a in authors:
        fam = a.get("family") or a.get("name") or ""
        if fam:
            break
    fam = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", fam).encode("ascii", "ignore").decode("ascii").lower()) or "anon"
    stop = {"a", "an", "the", "of", "and", "in", "on", "for", "to", "with", "by", "is", "are", "from", "at", "via"}
    word = "work"
    for tok in re.findall(r"[A-Za-z0-9]+", w.get("title") or ""):
        if tok.lower() not in stop:
            word = tok.lower()
            break
    base = "%s%s%s" % (fam, w.get("year") or "nd", word)
    key = base
    i = 0
    while key in used:
        i += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[(i - 1) % 26] + ("" if i <= 26 else str(i))
    used.add(key)
    return key


def bibtex(works):
    used = set()
    chunks = ["% Publications of Zachary B. Dozier (formerly published as Zachary Butzin-Dozier).",
              "% Generated from https://zdozier.com/publications/ . Names are given as published.", ""]
    for w in works:
        t = w.get("type")
        fields = []
        authors = as_list(w.get("authors"))
        if authors:
            fields.append(("author", " and ".join(bib_name(a) for a in authors)))
        fields.append(("title", bib_title(w.get("title") or "")))
        journal = accepted_journal(w)
        note = None
        if t in ("article", "review") and not is_preprintish(w):
            kind = "article"
            if w.get("venue"):
                fields.append(("journal", bib_escape(w["venue"])))
        elif t == "conference":
            kind = "inproceedings"
            if w.get("venue"):
                fields.append(("booktitle", bib_escape(w["venue"])))
        elif t == "chapter":
            kind = "incollection"
            if w.get("venue"):
                fields.append(("booktitle", bib_escape(w["venue"])))
            eds = as_list(w.get("editors"))
            if eds:
                fields.append(("editor", " and ".join(bib_escape(e) for e in eds)))
            if w.get("publisher"):
                fields.append(("publisher", bib_escape(w["publisher"])))
        elif t == "report":
            kind = "techreport"
            if w.get("venue"):
                fields.append(("institution", bib_escape(w["venue"])))
        else:
            kind = "misc"
            if w.get("venue"):
                fields.append(("howpublished", bib_escape(w["venue"])))
        if w.get("year"):
            fields.append(("year", str(w["year"])))
        p = date_parts(w.get("date"))
        if p and p[1]:
            fields.append(("month", MONTHS_SHORT[p[1] - 1].lower()))
        if w.get("volume"):
            fields.append(("volume", bib_escape(w["volume"])))
        if w.get("issue"):
            fields.append(("number", bib_escape(w["issue"])))
        if w.get("pages"):
            fields.append(("pages", bib_escape(str(w["pages"]).replace("-", "--"))))
        if w.get("doi"):
            fields.append(("doi", w["doi"]))
        link = doi_url(w.get("doi")) or (w.get("url") if is_http(w.get("url")) else None)
        if link:
            fields.append(("url", link))
        if w.get("pmid"):
            fields.append(("pmid", str(w["pmid"])))
        if journal:
            note = "Accepted for publication in " + journal
        elif w.get("status") == "under-review":
            note = "Under review" + (". " + w["status_note"] if w.get("status_note") else "")
        elif w.get("status") == "accepted":
            note = w.get("status_note") or "In press"
        elif is_preprintish(w):
            note = "Preprint"
        if note:
            fields.append(("note", bib_escape(note)))
        key = bib_key(w, used)
        body = ",\n".join(("  %s = %s" if k == "month" else "  %s = {%s}") % (k, v) for k, v in fields)
        chunks.append("@%s{%s,\n%s\n}\n" % (kind, key, body))
    return "\n".join(chunks)


EXPORT_FIELDS = ["id", "doi", "title", "authors", "author_count", "my_position", "role", "year", "date",
                 "venue", "volume", "issue", "pages", "type", "status", "status_note", "url", "oa_url",
                 "pmid", "pmcid", "cited_by", "versions", "topics", "contribution", "code_url"]


def export_json(works, generated):
    out = []
    for w in works:
        rec = dict((k, w.get(k)) for k in EXPORT_FIELDS)
        rec["authors"] = [dict((k, a.get(k)) for k in ("given", "family", "name", "is_me", "is_group", "orcid"))
                          for a in as_list(w.get("authors"))]
        out.append(rec)
    return json.dumps({"generated": generated, "source": "https://zdozier.com/publications/",
                       "count": len(out), "works": out}, indent=1, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def sync_info(report, now):
    report = as_dict(report)
    # "Synced" means the APIs were queried: use the fetch step's finish time.
    # A local merge from the committed snapshot (make offline) rewrites
    # report.finished but fetches nothing, so it is only the fallback.
    fin = parse_iso(as_dict(report.get("fetch")).get("finished")) or parse_iso(report.get("finished"))
    sources = as_dict(report.get("sources"))
    rows = []
    known = set()
    for key, label, use in SOURCE_LABELS:
        s = as_dict(sources.get(key))
        known.add(key)
        if not s and sources:
            continue
        rows.append(dict({"key": key, "label": label, "use": use, "ok": bool(s.get("ok")) if s else None,
                          "count": s.get("count"), "error": s.get("error"),
                          "used_cache": bool(s.get("used_cache"))}, **fetched_parts(s.get("fetched_at"))))
    for key, s in sources.items():
        if key not in known:
            s = as_dict(s)
            rows.append(dict({"key": key, "label": key, "use": "", "ok": bool(s.get("ok")), "count": s.get("count"),
                              "error": s.get("error"), "used_cache": bool(s.get("used_cache"))},
                             **fetched_parts(s.get("fetched_at"))))
    if not fin:
        return {"label": "sync status unknown", "state": "warn", "iso": None, "days": None,
                "finished": None, "sources": rows, "failing": [], "ok": False,
                "warnings": as_list(report.get("warnings"))}
    days = max(0, (now.date() - fin.date()).days)
    label = "synced today" if days == 0 else ("synced 1 day ago" if days == 1 else "synced %d days ago" % days)
    failing = [r["label"] for r in rows if r["ok"] is False or r["used_cache"]]
    ok = bool(report.get("ok", True)) and not failing and days <= 14
    return {"label": label, "state": "ok" if ok else "warn", "iso": fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days": days, "finished": fmt_utc(fin), "sources": rows, "failing": failing, "ok": ok,
            "warnings": as_list(report.get("warnings")),
            "guard": as_dict(report.get("sanity_guard"))}


def fmt_utc(d):
    if not d:
        return ""
    return "%s %d, %d, %02d:%02d UTC" % (MONTHS_SHORT[d.month - 1], d.day, d.year, d.hour, d.minute)


def fetched_parts(value):
    """'fetched' ('Oct 9, 2026, 20:42 UTC') plus its date and time halves, so
    the colophon table can break between them on a phone."""
    d = parse_iso(value) if value else None
    if not d:
        return {"fetched": "", "fetched_date": "", "fetched_time": ""}
    return {"fetched": fmt_utc(d),
            "fetched_date": "%s %d, %d," % (MONTHS_SHORT[d.month - 1], d.day, d.year),
            "fetched_time": "%02d:%02d UTC" % (d.hour, d.minute)}


# Publications-per-year chart: the latest charted year and the 7 before it.
# Earlier works are summarized in a line under the chart.
CHART_YEARS = 8


def is_charted(w, this_year):
    """Same rule as merge.py _is_public (metrics.by_year_role): published, a
    posted preprint, or reachable by DOI, and not dated after this year. An
    in-press item without a DOI and a chapter dated next year are left out
    until they appear."""
    y = w.get("year")
    if not isinstance(y, int) or y > this_year:
        return False
    return w.get("status") in ("published", "preprint") or bool(w.get("doi"))


def chart_window(rows, span=CHART_YEARS):
    """Split consecutive per-year rows into (shown, earlier). The window starts
    at max(first year, last year - (span - 1)); earlier rows with works are
    returned so the template can mention them."""
    if not rows:
        return [], []
    lo = max(rows[0]["year"], rows[-1]["year"] - (span - 1))
    shown = [r for r in rows if r["year"] >= lo]
    earlier = [r for r in rows if r["year"] < lo and r["lead"] + r["co"]]
    return shown, earlier


def earlier_note(earlier):
    """'Plus 1 earlier work (2010).' or 'Plus 4 earlier works (2008, 2010).'"""
    n = sum(r["lead"] + r["co"] for r in earlier)
    if not n:
        return ""
    years = sorted(r["year"] for r in earlier)
    when = ", ".join(str(y) for y in years) if len(years) <= 3 else "%d%s%d" % (years[0], EN_DASH, years[-1])
    return "Plus %d earlier %s (%s)." % (n, "work" if n == 1 else "works", when)


def per_year_rows(works):
    years = {}
    for w in works:
        y = w.get("year")
        if not isinstance(y, int):
            continue
        r = years.setdefault(y, {"year": y, "lead": 0, "co": 0})
        if w.get("role") in ("first", "senior"):
            r["lead"] += 1
        else:
            r["co"] += 1
    if not years:
        return []
    lo, hi = min(years), max(years)
    return [years.get(y, {"year": y, "lead": 0, "co": 0}) for y in range(lo, hi + 1)]


def citation_rows(metrics, now):
    rows = []
    for r in as_list(as_dict(metrics).get("counts_by_year")):
        r = as_dict(r)
        y = r.get("year")
        if isinstance(y, int) and y <= now.year:
            rows.append({"year": y, "value": int(r.get("cited_by_count") or 0)})
    rows.sort(key=lambda r: r["year"])
    while rows and rows[0]["value"] == 0:
        rows.pop(0)
    return rows[-12:]


def theme_views(themes, works, topic_labels):
    used = set()
    out = []
    for th in as_list(themes):
        th = as_dict(th)
        tset = set(as_list(th.get("topics")))
        pool = [w for w in works if tset & set(as_list(w.get("topics")))]
        pool.sort(key=lambda w: (bool(w.get("featured")), work_sort_key(w)), reverse=True)
        linked = [w for w in pool if w.get("link")] or pool
        pick = [w for w in linked if w["id"] not in used][:3]
        if len(pick) < 2:
            for w in linked:
                if len(pick) >= 2:
                    break
                if w not in pick:
                    pick.append(w)
        used.update(w["id"] for w in pick)
        topics = [t for t in as_list(th.get("topics")) if t in topic_labels]
        out.append(dict(th, papers=pick, count=len(pool), link_topic=topics[0] if topics else None))
    return out


def software_view(software, site_sw):
    software = as_dict(software)
    site_sw = as_dict(site_sw)
    include = [str(x) for x in as_list(site_sw.get("include"))]
    exclude = set(str(x) for x in as_list(site_sw.get("exclude")))
    repos = [as_dict(r) for r in as_list(software.get("repos"))]
    shown = []
    for r in repos:
        name = r.get("name")
        if not name or name in exclude:
            continue
        if include and name not in include and r.get("fork"):
            continue
        shown.append(r)
    order = dict((n, i) for i, n in enumerate(include))
    shown.sort(key=lambda r: (order.get(r.get("name"), len(order)), "" if not r.get("pushed_at") else r["pushed_at"]))
    # content/site.yml software.descriptions: {RepoName: "One plain sentence."}
    # replaces the GitHub description, which is often terse or empty.
    descs = dict((str(k).lower(), str(v).strip()) for k, v in as_dict(site_sw.get("descriptions")).items()
                 if v and str(v).strip())
    # software.languages: {RepoName: "R"} replaces GitHub's language guess,
    # which counts rendered HTML output (an R demo can show up as "HTML").
    langs = dict((str(k).lower(), str(v).strip()) for k, v in as_dict(site_sw.get("languages")).items()
                 if v and str(v).strip())
    out = []
    for r in shown:
        v = dict(r)
        desc = descs.get(str(r.get("name")).lower()) or (r.get("description") or "").strip()
        v["description"] = sentence_case_first(desc) if desc else None
        v["language"] = langs.get(str(r.get("name")).lower()) or r.get("language")
        v["html_url"] = r.get("html_url") if is_http(r.get("html_url")) else None
        v["updated"] = fmt_month_year((r.get("pushed_at") or "")[:7])
        out.append(v)
    return out


def jsonld(person, links, site_url):
    same = [l["url"] for l in links if l.get("kind") in ("orcid", "stanford", "openalex", "github", "scholar", "linkedin")
            and is_http(l.get("url"))]
    data = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": person.get("name"),
        "alternateName": person.get("former_name") or "Zachary Butzin-Dozier",
        "url": site_url + "/",
        "image": site_url + "/img/headshot.jpg",
        "jobTitle": person.get("title"),
        "affiliation": {"@type": "Organization", "name": person.get("institution")},
        "sameAs": same,
    }
    if person.get("name_with_degrees") and "," in person["name_with_degrees"]:
        data["honorificSuffix"] = person["name_with_degrees"].split(",", 1)[1].strip()
    if person.get("email"):
        data["email"] = "mailto:" + person["email"]
    if person.get("address"):
        data["workLocation"] = {"@type": "Place", "address": person["address"]}
    for k in list(data):
        if data[k] in (None, "", []):
            del data[k]
    s = json.dumps(data, ensure_ascii=False, indent=1)
    return Markup(s.replace("</", "<\\/"))


def asset_version(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:8]
    except IOError:
        return "0"


def build_model(content_dir, data_dir, now):
    site_yml = scrub(as_dict(load_yaml(os.path.join(content_dir, "site.yml"), {})))
    cv = scrub(as_dict(load_yaml(os.path.join(content_dir, "cv.yml"), {})))
    pubs_yml = scrub(as_dict(load_yaml(os.path.join(content_dir, "publications.yml"), {})))
    funding_yml = as_dict(load_yaml(os.path.join(content_dir, "funding.yml"), {}))

    pubs = scrub(as_dict(load_json(os.path.join(data_dir, "publications.json"), {})))
    metrics = scrub(as_dict(load_json(os.path.join(data_dir, "metrics.json"), {})))
    grants_d = scrub(as_dict(load_json(os.path.join(data_dir, "grants.json"), {})))
    software = scrub(as_dict(load_json(os.path.join(data_dir, "software.json"), {})))
    report = scrub(as_dict(load_json(os.path.join(data_dir, "run_report.json"), {})))

    site = as_dict(site_yml.get("site"))
    site_url = (site.get("url") or "https://zdozier.com").rstrip("/")
    person = as_dict(site_yml.get("person"))
    person.setdefault("name", "Zachary B. Dozier")
    person.setdefault("name_with_degrees", person["name"])
    person.setdefault("former_name", "Zachary Butzin-Dozier")
    person["bio_paragraphs"] = [p.strip() for p in re.split(r"\n\s*\n", person.get("bio") or "") if p.strip()]
    links = [as_dict(l) for l in as_list(site_yml.get("links")) if as_dict(l).get("url")]
    for l in links:
        l["external"] = is_http(l["url"])

    topics = [as_dict(t) for t in as_list(pubs_yml.get("topics")) if as_dict(t).get("id")]
    topic_labels = dict((t["id"], t.get("label") or t["id"]) for t in topics)

    raw_works = [as_dict(w) for w in as_list(pubs.get("works")) if as_dict(w).get("title")]
    raw_works = [w for w in raw_works if not w.get("hidden")]
    raw_works.sort(key=work_sort_key, reverse=True)
    works = [prepare_work(w, topic_labels) for w in raw_works]

    today = now.date()
    show_amounts = bool(funding_yml.get("show_amounts")) and bool(grants_d.get("show_amounts", True))
    grants = [as_dict(g) for g in as_list(grants_d.get("grants"))]
    active, completed = split_grants(grants, today, False, show_amounts)
    cv_active, cv_completed = split_grants(grants, today, True, show_amounts)

    n_lead = sum(1 for w in works if w.get("role") in ("first", "senior"))
    n_pre = sum(1 for w in works if w["preprintish"])
    years = [w["year"] for w in works if isinstance(w.get("year"), int)]

    # Filter controls.
    chips = [{"value": "all", "label": "All", "count": len(works)}]
    if n_lead:
        chips.append({"value": "lead", "label": "First or senior author", "count": n_lead})
    for t in topics:
        c = sum(1 for w in works if t["id"] in w["topic_list"])
        if c:
            chips.append({"value": t["id"], "label": t.get("label") or t["id"], "count": c})
    if n_pre:
        # Counts manuscripts under review too, so the label says so (the Type
        # select's "Preprints" option counts preprints only).
        chips.append({"value": "preprints", "label": "Preprints and under review", "count": n_pre})
    type_options = []
    for val, label in TYPE_OPTIONS:
        c = sum(1 for w in works if val in w["kinds"].split())
        if c:
            type_options.append({"value": val, "label": label, "count": c})
    year_options = []
    for y in sorted(set(years), reverse=True):
        year_options.append({"value": str(y), "count": years.count(y)})

    groups = []
    for w in works:
        y = w["year_label"] or "Undated"
        if not groups or groups[-1]["year"] != y:
            groups.append({"year": y, "works": []})
        groups[-1]["works"].append(w)

    # Stats and charts.
    m = metrics
    charted = [w for w in works if is_charted(w, today.year)]
    rows, earlier_rows = chart_window(per_year_rows(charted))
    charted_years = [w["year"] for w in charted]
    spark_rows = citation_rows(m, now)
    spark_hi = today.year if any(r["year"] == today.year for r in spark_rows) else (spark_rows[-1]["year"] if spark_rows else None)
    spark_last = spark_rows[-1] if spark_rows else None
    # "Works", not "Publications": the count includes preprints and manuscripts
    # under review; the line under the strip says how many (n_pre).
    stats = [{"label": "Works", "value": len(works),
              "note": "%d first or senior author" % n_lead if works else None}]
    if m.get("cited_by_count") is not None:
        stats.append({"label": "Citations", "value": m.get("cited_by_count"), "spark": True})
    if m.get("h_index") is not None:
        stats.append({"label": "h-index", "value": m.get("h_index"),
                      "note": ("i10-index %d" % m["i10_index"]) if m.get("i10_index") is not None else None})
    mech = []
    for g in active:
        lbl = " ".join(x for x in [g.get("funder"), g.get("mechanism")] if x and x not in ("Subcontract",))
        if lbl and lbl not in mech:
            mech.append(lbl)
    stats.append({"label": "Active grants", "value": len(active), "note": ", ".join(mech[:2]) or None})

    recent = [w for w in works if w.get("link")][:5]

    # CV.
    cv_sections = []
    buckets = dict((k, []) for k, _ in CV_PUB_SECTIONS)
    for w in works:
        buckets[cv_section_of(w)].append(w)
    for key, title in CV_PUB_SECTIONS:
        items = buckets[key]
        if items:
            cv_sections.append({"key": key, "title": title,
                                "entries": [dict(w, citation=cite_apa(w)) for w in items]})

    team = as_dict(cv.get("trainees"))
    for grp in ("current", "former"):
        team[grp] = [dict(as_dict(t), role_label=sentence_case_first(as_dict(t).get("role") or ""))
                     for t in as_list(team.get(grp)) if as_dict(t).get("name")]

    sync = sync_info(report, now)

    return {
        "now": now,
        "today": today,
        "build_date": fmt_long_date(today),
        "build_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site": site,
        "site_url": site_url,
        "repo_url": site.get("repo_url"),
        "person": person,
        "links": links,
        "topics": topics,
        "topic_labels": topic_labels,
        "works": works,
        "groups": groups,
        "chips": chips,
        "type_options": type_options,
        "year_options": year_options,
        "n_lead": n_lead,
        "n_pre": n_pre,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "since_year": min(charted_years) if charted_years else None,
        "chart_earlier": earlier_note(earlier_rows),
        "metrics": m,
        "stats": stats,
        "per_year": rows,
        "chart_wide": charts.pubs_per_year_svg(rows, compact=False, title_id="ppy-title", desc_id="ppy-desc"),
        "chart_compact": charts.pubs_per_year_svg(rows, compact=True, title_id="ppy-title", desc_id="ppy-desc"),
        "spark_rows": spark_rows,
        "spark_last": spark_last,
        "spark_partial": spark_last is not None and spark_last["year"] == today.year,
        "sparkline": charts.sparkline_svg(spark_rows, spark_hi, today.year),
        "themes": theme_views(site_yml.get("research_themes"), works, topic_labels),
        "recent": recent,
        "active_grants": active,
        "completed_grants": completed,
        "cv_active_grants": cv_active,
        "cv_completed_grants": cv_completed,
        "software": software_view(software, site_yml.get("software")),
        "cv": cv,
        "cv_sections": cv_sections,
        "team": team,
        "sync": sync,
        "pipeline_wide": charts.pipeline_svg([(r["label"], r["ok"] is not False, r["used_cache"]) for r in sync["sources"]],
                                             compact=False, title_id="dg-title"),
        "pipeline_tall": charts.pipeline_svg([(r["label"], r["ok"] is not False, r["used_cache"]) for r in sync["sources"]],
                                             compact=True, title_id="dg-title"),
        "jsonld": jsonld(person, links, site_url),
        "generated": pubs.get("generated"),
        "raw_works": raw_works,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

PAGES = [
    # (page id, output path, template, title, description)
    ("home", "index.html", "index.html", None,
     "Zachary B. Dozier, Assistant Professor at Stanford University School of Medicine. Causal machine learning with electronic health records."),
    ("publications", "publications/index.html", "publications.html", "Publications",
     "Publications of Zachary B. Dozier (formerly Zachary Butzin-Dozier), updated weekly from ORCID, OpenAlex, Crossref, and Europe PMC."),
    ("funding", "funding/index.html", "funding.html", "Funding",
     "Active and completed research grants of Zachary B. Dozier."),
    ("team", "team/index.html", "team.html", "Team",
     "Current and former trainees and research staff working with Zachary B. Dozier."),
    ("cv", "cv/index.html", "cv.html", "CV",
     "Curriculum vitae of Zachary B. Dozier, PhD, MPH, generated from public data sources."),
    ("colophon", "colophon/index.html", "colophon.html", "How this site works",
     "How zdozier.com is built and kept up to date from public APIs."),
    ("404", "404.html", "404.html", "Page not found", "This page does not exist."),
]


# Codes such as COVID-19, GLP-1, IL-6 and SARS-CoV-2: a line break at their
# hyphen ("COVID-" / "19") is hard to read, so HTML pages keep them whole.
NOBREAK_CODE_RE = re.compile(r"\b[A-Z][A-Za-z]*(?:-[A-Za-z]+)*-\d+[A-Za-z]*\b")


def nobreak_codes(text):
    """Escape text and wrap each code in <span class="nowrap">. For HTML
    templates only (not the feed or BibTeX)."""
    if text is None:
        return Markup("")
    s = str(escape(text))
    return Markup(NOBREAK_CODE_RE.sub(lambda m: '<span class="nowrap">%s</span>' % m.group(0), s))


def make_env(templates_dir):
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=ChainableUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["month_year"] = fmt_month_year
    env.filters["thousands"] = lambda n: "{:,}".format(n) if isinstance(n, int) else n
    env.filters["cap"] = sentence_case_first
    env.filters["nobreak_codes"] = nobreak_codes
    env.globals["fmt_period"] = fmt_period
    return env


def path_url(out_rel):
    if out_rel == "index.html":
        return "/"
    if out_rel.endswith("/index.html"):
        return "/" + out_rel[: -len("index.html")]
    return "/" + out_rel


def cname_for(site_url):
    """GitHub Pages custom domain: the host of site.url (default zdozier.com)."""
    m = re.match(r"^https?://([^/:]+)", site_url or "")
    host = m.group(1).lower() if m else ""
    if not host or host in ("localhost", "127.0.0.1") or host.endswith(".github.io"):
        return "zdozier.com"
    return host


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def prepare_out(out):
    """Empty the output directory, keeping an existing cv.pdf (pdf.py replaces it)."""
    out = os.path.abspath(out)
    keep = None
    if os.path.isdir(out):
        entries = os.listdir(out)
        if entries and BUILD_MARKER not in entries and out != os.path.abspath(DEFAULTS["out"]):
            raise SystemExit("build: refusing to empty %s (not a previous build output)" % out)
        pdf = os.path.join(out, "cv.pdf")
        if os.path.exists(pdf):
            fd, keep = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            shutil.copy2(pdf, keep)
        shutil.rmtree(out)
    os.makedirs(out)
    write_text(os.path.join(out, BUILD_MARKER), "Output of scripts/build.py. Safe to delete.\n")
    if keep:
        shutil.move(keep, os.path.join(out, "cv.pdf"))


def copy_static(static_dir, out):
    if not os.path.isdir(static_dir):
        return 0
    n = 0
    for base, dirs, files in os.walk(static_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = os.path.relpath(base, static_dir)
        dest = out if rel == "." else os.path.join(out, rel)
        if not os.path.isdir(dest):
            os.makedirs(dest)
        for f in files:
            if f.startswith(".") or f.endswith("~"):
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(dest, f))
            n += 1
    return n


def feed_entries(model, limit=20):
    out = []
    site_url = model["site_url"]
    for w in model["works"][:limit]:
        p = date_parts(w.get("date")) or (w.get("year") or model["today"].year, None, None)
        try:
            d = dt.date(p[0], p[1] or 1, p[2] or 1)
        except ValueError:
            d = model["today"]
        first_seen = parse_iso(w.get("first_seen"))
        if d > model["today"]:
            d = first_seen.date() if first_seen else model["today"]
        stamp = d.strftime("%Y-%m-%dT00:00:00Z")
        authors = ", ".join(author_site_name(a) for a in as_list(w.get("authors"))[:6])
        if len(as_list(w.get("authors"))) > 6:
            authors += ", et al"
        summary_bits = [authors]
        venue = accepted_journal(w) or w.get("venue") or w.get("status_note")
        if venue:
            summary_bits.append(venue)
        if w.get("status_label"):
            summary_bits.append(w["status_label"])
        out.append({
            "title": w.get("title"),
            "url": w.get("link") or "%s/publications/#%s" % (site_url, w["anchor"]),
            "id": doi_url(w.get("doi")) or "%s/publications/#%s" % (site_url, w["anchor"]),
            "updated": stamp,
            "summary": ". ".join(summary_bits) + ".",
        })
    return out


def render_site(model, templates_dir, static_dir, out):
    env = make_env(templates_dir)
    prepare_out(out)
    n_static = copy_static(static_dir, out)
    versions = {
        "css": asset_version(os.path.join(static_dir, "css", "site.css")),
        "js": asset_version(os.path.join(static_dir, "js", "site.js")),
    }
    written = []
    base_ctx = dict(model)
    base_ctx["nav"] = NAV
    base_ctx["v"] = versions
    js_path = os.path.join(static_dir, "js", "site.js")
    base_ctx["js_kb"] = "%.1f" % (os.path.getsize(js_path) / 1024.0) if os.path.exists(js_path) else "0"
    for page_id, rel, tpl, title, desc in PAGES:
        ctx = dict(base_ctx)
        url = path_url(rel)
        ctx.update({
            "page_id": page_id,
            "page_url": url,
            "canonical": model["site_url"] + url if page_id != "404" else None,
            "page_title": ("%s \u00b7 %s" % (title, model["person"]["name"])) if title else
                          "%s \u00b7 %s" % (model["person"]["name"], model["person"].get("institution") or ""),
            "heading": title,
            "description": desc,
            "og_type": "profile" if page_id == "home" else "website",
        })
        html = env.get_template(tpl).render(**ctx)
        write_text(os.path.join(out, rel), html)
        written.append(rel)

    feed = env.get_template("feed.xml").render(entries=feed_entries(model), **base_ctx)
    write_text(os.path.join(out, "feed.xml"), feed)
    sitemap_urls = [model["site_url"] + path_url(rel) for pid, rel, _, _, _ in PAGES if pid != "404"]
    sitemap_urls.append(model["site_url"] + "/cv.pdf")
    sm = env.get_template("sitemap.xml").render(urls=sitemap_urls, lastmod=model["today"].isoformat())
    write_text(os.path.join(out, "sitemap.xml"), sm)
    write_text(os.path.join(out, "robots.txt"),
               "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % model["site_url"])
    write_text(os.path.join(out, "CNAME"), cname_for(model["site_url"]) + "\n")
    write_text(os.path.join(out, "publications.bib"), bibtex(model["works"]))
    write_text(os.path.join(out, "publications.json"), export_json(model["works"], model["generated"] or model["build_iso"]))
    write_text(os.path.join(out, ".nojekyll"), "")
    written += ["feed.xml", "sitemap.xml", "robots.txt", "CNAME", "publications.bib", "publications.json"]
    return written, n_static


def post_checks(out):
    """No em dashes and no phone numbers anywhere in the text output."""
    problems = []
    for base, _, files in os.walk(out):
        for f in files:
            if not f.endswith((".html", ".xml", ".txt", ".bib", ".json", ".css", ".js", ".svg")) and f != "CNAME":
                continue
            p = os.path.join(base, f)
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            if EM_DASH in text:
                write_text(p, scrub(text))
                log("build: warning: replaced em dashes in %s" % os.path.relpath(p, out))
            visible = re.sub(r"<[^>]+>", " ", text) if f.endswith((".html", ".xml")) else text
            for rx in PHONE_RES:
                m = rx.search(visible)
                if m:
                    problems.append("%s: text that looks like a phone number (%s)" % (os.path.relpath(p, out), m.group(0)))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build zdozier.com into _site/ (offline).")
    ap.add_argument("--content-dir", default=DEFAULTS["content"])
    ap.add_argument("--data-dir", default=DEFAULTS["data"])
    ap.add_argument("--templates-dir", default=DEFAULTS["templates"])
    ap.add_argument("--static-dir", default=DEFAULTS["static"])
    ap.add_argument("--out", default=DEFAULTS["out"])
    ap.add_argument("--now", default=None, help="build time, ISO 8601 (default: now; env SITE_BUILD_TIME)")
    ap.add_argument("--quiet", action="store_true", help="only print warnings and errors")
    args = ap.parse_args(argv)

    now = parse_now(args.now)
    model = build_model(args.content_dir, args.data_dir, now)
    written, n_static = render_site(model, args.templates_dir, args.static_dir, args.out)
    problems = post_checks(args.out)
    if problems:
        for p in problems:
            log("build: error: " + p)
        log("build: the site must not show a phone number. Remove it from content/ and rebuild.")
        return 1
    if args.quiet:
        return 0
    log("build: %d pages and exports, %d static files -> %s (%d works, %d grants, %d repos; %s)"
        % (len(written), n_static, os.path.relpath(args.out, os.getcwd()) if args.out.startswith(os.getcwd()) else args.out,
           len(model["works"]), len(model["active_grants"]) + len(model["completed_grants"]),
           len(model["software"]), model["sync"]["label"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
