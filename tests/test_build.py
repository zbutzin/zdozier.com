"""Offline tests for scripts/build.py, scripts/charts.py, and scripts/pdf.py.

Each test builds the site from a small inline fixture into a temporary
directory, using the repository's real templates and static files.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build  # noqa: E402
import charts  # noqa: E402
import pdf  # noqa: E402

EM = "\u2014"
SITE_URL = "https://zdozier.com"
NOW = "2026-10-09T21:00:00Z"


def author(given, family, me=False, group=False, apa=None):
    if group:
        return {"given": None, "family": None, "name": family, "is_me": False, "is_group": True,
                "orcid": None, "apa": family, "suffix": None}
    initials = " ".join(p[0] + "." for p in given.split())
    return {"given": given, "family": family, "name": "%s %s" % (given, family), "is_me": me,
            "is_group": False, "orcid": "0000-0001-6419-0008" if me else None,
            "apa": apa or "%s, %s" % (family, initials), "suffix": None}


ME = author("Zachary", "Butzin-Dozier", me=True)


def work(doi, title, authors, year, **kw):
    w = {"id": doi, "doi": doi, "title": title, "authors": authors, "author_count": len(authors),
         "my_position": next((i + 1 for i, a in enumerate(authors) if a.get("is_me")), None),
         "role": "coauthor", "year": year, "date": "%d-03-01" % year, "venue": "Journal of Tests",
         "volume": "12", "issue": "3", "pages": "100-110", "type": "article", "status": "published",
         "status_note": None, "url": "https://doi.org/" + doi, "oa_url": None, "pmid": None,
         "pmcid": None, "cited_by": 4, "versions": [], "topics": ["covid"], "contribution": None,
         "featured": False, "code_url": None, "sources": ["orcid"], "first_seen": "2026-10-01"}
    w.update(kw)
    return w


def fixture_works():
    many = [author("Given%d" % i, "Family%d" % i) for i in range(1, 25)]
    many.insert(2, ME)  # 25 authors, he is third
    return [
        work("10.1000/first", "SSRI use and Long COVID " + EM + " a cohort study",
             [ME, author("Yunwen", "Ji"), author("Jane", "Roe"), author("N3C Consortium", "N3C Consortium", group=True)],
             2024, role="first", featured=True, cited_by=15,
             versions=[{"doi": "10.1101/2024.01.01.1", "server": "medRxiv",
                        "url": "https://doi.org/10.1101/2024.01.01.1", "year": 2023}],
             pmid="123456", oa_url="https://example.org/paper.pdf"),
        work("10.1000/mom", "Cortisol production patterns in young children",
             [author("Kristin", "Bernard"), ME, author("Mary", "Dozier")], 2010,
             topics=["child-health"], contribution="Designed study and conducted data analysis."),
        work("10.1000/many", "A paper with twenty-five authors", many, 2025, topics=["causal-ml"],
             contribution="Generated all figures."),
        work("10.64898/pre", "A preprint about metformin", [ME, author("Lin", "Wang")], 2026,
             type="preprint", status="accepted", status_note="Diabetes, Obesity and Metabolism",
             venue="medRxiv", volume=None, issue=None, pages=None, role="first",
             code_url="https://github.com/zbutzin/LongCOVID"),
        work("10.64898/pre2", "An under-review preprint", [author("Ann", "Lee"), ME], 2026,
             type="preprint", status="preprint", venue="medRxiv", volume=None, issue=None, pages=None),
        {"id": "manual:report", "doi": None, "title": "For the good of us all",
         "authors": [author("Colette", "Auerswald"), ME, author("et al.", "et al.", group=True)],
         "author_count": 3, "my_position": 2, "role": "coauthor", "year": 2020, "date": "2020",
         "venue": "UC Berkeley School of Public Health", "type": "report", "status": "published",
         "url": "https://example.org/report.pdf", "topics": ["covid"], "versions": [],
         "contribution": "Wrote sections.", "featured": False, "sources": ["manual"]},
    ]


def write_fixture(base, works=None, grants=None, software=True, run_report=True, extra_cv=None):
    content = os.path.join(base, "content")
    data = os.path.join(base, "data")
    os.makedirs(content)
    os.makedirs(data)
    site = {
        "site": {"url": SITE_URL, "repo_url": "https://github.com/zbutzin/zdozier.com"},
        "person": {"name": "Zachary B. Dozier", "name_with_degrees": "Zachary B. Dozier, PhD, MPH",
                   "former_name": "Zachary Butzin-Dozier", "title": "Assistant Professor",
                   "appointments_short": ["Pediatrics (Clinical Informatics)"],
                   "institution": "Stanford University School of Medicine",
                   "email": "zdozier@stanford.edu", "address": "3145 Porter Drive, Palo Alto, CA 94304",
                   "tagline": "Machine learning for causal inference",
                   "bio": "First paragraph.\n\nSecond paragraph " + EM + " with a dash."},
        "links": [{"label": "ORCID", "url": "https://orcid.org/0000-0001-6419-0008", "kind": "orcid"},
                  {"label": "Google Scholar", "url": None, "kind": "scholar"},
                  {"label": "CV (PDF)", "url": "/cv.pdf", "kind": "cv"}],
        "research_themes": [{"id": "lc", "title": "Long COVID", "summary": "Studies.", "topics": ["covid"]}],
        "software": {"include": ["LongCOVID"], "exclude": []},
    }
    pubs_yml = {"topics": [{"id": "covid", "label": "COVID-19"}, {"id": "causal-ml", "label": "Causal ML"},
                           {"id": "child-health", "label": "Child health"}]}
    cv = {"summary_statement": "I study things.",
          "appointments": [{"title": "Assistant Professor", "org": "Stanford", "unit": None,
                            "start": "June 2026", "end": None, "description": "Leads a lab."}],
          "education": [{"degree": "Ph.D.", "field": "Epidemiology", "institution": "UC Berkeley",
                         "location": "Berkeley, CA", "year": 2022, "honors": None, "gpa": None}],
          "honors": [{"year": 2024, "text": "An award"}],
          "trainees": {"current": [{"name": "Anna Krasnova", "role": "postdoc"}], "former": []},
          "presentations": {"invited": [{"title": "A talk", "venue": "A seminar", "date": "May 1, 2025",
                                         "location": "Berkeley, CA"}], "teaching": []},
          "media": [], "peer_review": [{"text": "A journal", "date": "May 2026"}],
          "skills": {"technical": ["R"], "languages": ["Spanish"]}}
    if extra_cv:
        cv.update(extra_cv)
    with open(os.path.join(content, "site.yml"), "w") as fh:
        yaml.safe_dump(site, fh)
    with open(os.path.join(content, "publications.yml"), "w") as fh:
        yaml.safe_dump(pubs_yml, fh)
    with open(os.path.join(content, "cv.yml"), "w") as fh:
        yaml.safe_dump(cv, fh)
    with open(os.path.join(content, "funding.yml"), "w") as fh:
        yaml.safe_dump({"show_amounts": False}, fh)

    def dump(name, obj):
        with open(os.path.join(data, name), "w") as fh:
            json.dump(obj, fh)

    dump("publications.json", {"generated": NOW, "works": fixture_works() if works is None else works})
    dump("metrics.json", {"cited_by_count": 313, "h_index": 7, "i10_index": 6,
                          "counts_by_year": [{"year": y, "works_count": 1, "cited_by_count": c}
                                             for y, c in [(2023, 10), (2024, 20), (2025, 30), (2026, 40), (2027, 0)]]})
    dump("grants.json", {"grants": [
        {"id": "K01AI182501", "title": "Machine learning and causal inference", "funder": "NIH",
         "agency": "NIAID", "agency_name": "National Institute of Allergy and Infectious Diseases",
         "mechanism": "K01", "role": "PI", "start": "2024-09", "end": "2029-08", "status": "active",
         "institutions": ["Stanford University"], "url": "https://reporter.nih.gov/project-details/1",
         "description": "Studies sequelae.", "source": "nih_reporter", "pis": ["Zachary Butzin-Dozier"],
         "amount_total": 406461}]} if grants is None else {"grants": grants})
    if software:
        dump("software.json", {"repos": [{"name": "LongCOVID", "description": "Pipeline",
                                          "html_url": "https://github.com/zbutzin/LongCOVID", "language": "Python",
                                          "stargazers_count": 0, "pushed_at": "2026-09-30T16:57:35Z", "fork": False}]})
    if run_report:
        dump("run_report.json", {"started": "2026-10-06T14:00:00Z", "finished": "2026-10-06T14:02:00Z", "ok": True,
                                 "sources": {"orcid": {"ok": True, "count": 44, "error": None, "used_cache": False,
                                                       "fetched_at": "2026-10-06T14:00:10Z"},
                                             "openalex": {"ok": False, "count": 0, "error": "HTTP 503",
                                                          "used_cache": True, "fetched_at": "2026-09-29T14:00:10Z"}},
                                 "new_works": [], "removed_works": [], "warnings": ["a note"]})
    return content, data


class TagCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def tags_of(html):
    p = TagCollector()
    p.feed(html)
    return p.tags


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class BuildTestBase(unittest.TestCase):
    PAGES = ["index.html", "publications/index.html", "cv/index.html", "funding/index.html",
             "team/index.html", "colophon/index.html", "404.html"]

    @classmethod
    def build_fixture(cls, **kw):
        base = tempfile.mkdtemp(prefix="zdozier-test-")
        content, data = write_fixture(base, **kw)
        out = os.path.join(base, "site")
        rc = build.main(["--content-dir", content, "--data-dir", data, "--out", out, "--now", NOW, "--quiet"])
        return base, out, rc


class TestFullBuild(BuildTestBase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.out, cls.rc = cls.build_fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def page(self, rel):
        return read(os.path.join(self.out, rel))

    def html_files(self):
        for base, _, files in os.walk(self.out):
            for f in files:
                if f.endswith(".html"):
                    yield os.path.join(base, f)

    def test_build_succeeds_and_key_files_exist(self):
        self.assertEqual(self.rc, 0)
        for rel in self.PAGES + ["feed.xml", "sitemap.xml", "robots.txt", "CNAME", "publications.bib",
                                 "publications.json", "css/site.css", "js/site.js", "favicon.svg"]:
            self.assertTrue(os.path.exists(os.path.join(self.out, rel)), rel)
        self.assertEqual(self.page("CNAME").strip(), "zdozier.com")
        self.assertIn("Sitemap: https://zdozier.com/sitemap.xml", self.page("robots.txt"))

    def test_his_name_is_bold_and_mary_dozier_is_not(self):
        pubs = self.page("publications/index.html")
        self.assertIn('<strong class="me">Butzin-Dozier\u00a0Z</strong>', pubs)
        self.assertNotIn('<strong class="me">Dozier\u00a0M</strong>', pubs)
        self.assertIn("Dozier\u00a0M", pubs)
        cv = self.page("cv/index.html")
        self.assertIn('<strong class="me">Butzin-Dozier,\u00a0Z.</strong>', cv)
        # Co-authored work with a contribution note gets an asterisk and the note.
        self.assertIn('<strong class="me">Butzin-Dozier,\u00a0Z.</strong>*', cv)
        self.assertIn("*Designed study and conducted data analysis.", cv)
        home = self.page("index.html")
        self.assertIn("Formerly published as Zachary Butzin-Dozier.", home)

    def test_no_em_dashes_anywhere(self):
        for base, _, files in os.walk(self.out):
            for f in files:
                if f.endswith((".html", ".xml", ".txt", ".bib", ".json", ".css", ".js", ".svg")):
                    self.assertNotIn(EM, read(os.path.join(base, f)), f)
        self.assertIn("SSRI use and Long COVID \u2013 a cohort study", self.page("publications/index.html"))

    def test_internal_links_are_root_relative(self):
        for path in self.html_files():
            for tag, attrs in tags_of(read(path)):
                for key in ("href", "src"):
                    v = attrs.get(key)
                    if not v:
                        continue
                    self.assertFalse(v.startswith("//"), "%s: %s" % (path, v))
                    if v.startswith(SITE_URL):
                        ok = (tag == "link" and attrs.get("rel") == "canonical") or "u-abs" in (attrs.get("class") or "")
                        self.assertTrue(ok, "absolute self link in %s: <%s %s=%s>" % (path, tag, key, v))
                    if tag == "a" and not re.match(r"^(https?:|mailto:|#)", v):
                        self.assertTrue(v.startswith("/"), "relative link in %s: %s" % (path, v))
        nav = re.findall(r'<nav class="nav nav-wide".*?</nav>', self.page("index.html"), re.S)[0]
        for href in re.findall(r'href="([^"]+)"', nav):
            self.assertTrue(href.startswith("/"), href)

    def test_no_third_party_resources(self):
        for path in self.html_files():
            for tag, attrs in tags_of(read(path)):
                if tag not in ("link", "script", "img", "iframe", "source", "video", "audio", "embed"):
                    continue
                self.assertNotEqual(tag, "iframe")
                for key in ("href", "src", "srcset"):
                    v = attrs.get(key) or ""
                    if tag == "link" and attrs.get("rel") == "canonical":
                        if key == "href":
                            self.assertTrue(v.startswith(SITE_URL + "/"), v)
                        continue
                    self.assertIsNone(re.search(r"(^|[\s,])(https?:)?//", v), "%s: <%s %s=%s>" % (path, tag, key, v))
        css = read(os.path.join(self.out, "css", "site.css"))
        self.assertNotRegex(css, r"url\(\s*['\"]?(https?:)?//")
        self.assertNotIn("@import", css)
        js = read(os.path.join(self.out, "js", "site.js"))
        self.assertNotRegex(js, r"https?://")

    def test_head_metadata_and_json_ld(self):
        home = self.page("index.html")
        self.assertIn('<link rel="canonical" href="https://zdozier.com/">', home)
        self.assertIn('property="og:image" content="https://zdozier.com/img/headshot.jpg"', home)
        pubs = self.page("publications/index.html")
        self.assertIn('<link rel="canonical" href="https://zdozier.com/publications/">', pubs)
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', home, re.S)
        data = json.loads(m.group(1))
        self.assertEqual(data["@type"], "Person")
        self.assertEqual(data["alternateName"], "Zachary Butzin-Dozier")
        self.assertIn("https://orcid.org/0000-0001-6419-0008", data["sameAs"])
        self.assertEqual(data["affiliation"]["name"], "Stanford University School of Medicine")
        self.assertIn('content="noindex"', self.page("404.html"))

    def test_publications_page(self):
        pubs = self.page("publications/index.html")
        self.assertEqual(pubs.count('<li class="pub" '), 6)
        self.assertIn('data-role="first"', pubs)
        self.assertIn('data-kind="type:preprint status:accepted"', pubs)
        self.assertIn('id="pub-filters" role="search" aria-label="Filter publications" hidden', pubs)
        self.assertIn('aria-live="polite"', pubs)
        self.assertIn("+ 17 more", pubs)            # 25 authors: 8 shown
        self.assertIn("<i>Diabetes, Obesity and Metabolism</i>; preprint on <i>medRxiv</i>", pubs)
        self.assertIn('<span class="tag tag-status">Accepted</span>', pubs)
        self.assertIn(">medRxiv 2023</a>", pubs)
        self.assertIn(">Cited by 15<", pubs)
        self.assertIn("My contribution", pubs)
        self.assertIn('<svg class="viz viz-columns"', pubs)
        # The hidden data table sits in a clipped div: a <table> ignores the
        # 1px width of .sr-only and widened the page on 320-375px phones.
        self.assertIn('<div class="sr-only">\n      <table>', pubs)
        self.assertNotIn('<table class="sr-only">', pubs)
        # Year groups come newest first.
        years = re.findall(r'<h2 class="year" id="y-(\d+)">', pubs)
        self.assertEqual(years, sorted(years, reverse=True))

    def test_cv_formatting(self):
        cv = self.page("cv/index.html")
        self.assertIn("Updated <time", cv)
        self.assertIn("October 9, 2026", cv)
        self.assertIn("Generated automatically from ORCID, OpenAlex, Crossref, Europe PMC, and NIH RePORTER.", cv)
        for title in ("Summary Statement", "Academic Appointments", "Education", "Funding", "Honors and Awards",
                      "Publications", "Teaching", "Trainees", "Presentations", "Peer Review Activities", "Skills"):
            if title == "Teaching":
                continue  # no teaching entries in the fixture
            self.assertIn(">%s</h2>" % title, cv)
        order = [cv.index(">%s</h2>" % t) for t in ("Summary Statement", "Academic Appointments", "Education",
                                                     "Funding", "Honors and Awards", "Publications", "Trainees",
                                                     "Presentations", "Peer Review Activities", "Skills")]
        self.assertEqual(order, sorted(order))
        # APA: more than 20 authors -> first 19, ellipsis, last.
        self.assertIn("Family18,\u00a0G., \u2026 Family24,\u00a0G. (2025).", cv)
        self.assertIn("<i>Journal of Tests</i>, <i>12</i>(3), 100\u2013110.", cv)
        # "et al." group author: no ampersand before it.
        self.assertIn("Auerswald,\u00a0C., <strong class=\"me\">Butzin-Dozier,\u00a0Z.</strong>*, et al. (2020).", cv)
        self.assertIn("Accepted for publication in <i>Diabetes, Obesity and Metabolism</i>.", cv)
        sections = re.findall(r"<h3>([^<]+)</h3>", cv)
        self.assertIn("Peer-Reviewed Journal Articles (Original Research)", sections)
        self.assertIn("Accepted and In Press", sections)
        self.assertIn("Preprints and Manuscripts Under Review", sections)
        self.assertIn("Non-Peer-Reviewed Publications", sections)
        self.assertNotIn("406,461", cv)  # amounts hidden unless show_amounts is true
        self.assertIn("@page cv", read(os.path.join(self.out, "css", "site.css")))

    def test_sync_pill_and_colophon(self):
        home = self.page("index.html")
        self.assertIn("synced 3 days ago", home)
        self.assertIn('data-synced="2026-10-06T14:02:00Z"', home)
        self.assertIn("is-warn", home)  # one source used its cached copy
        col = self.page("colophon/index.html")
        self.assertIn("Failed, used cached copy", col)
        self.assertIn('<svg class="diagram"', col)
        self.assertIn("https://github.com/zbutzin/zdozier.com", col)
        self.assertIn("No third-party requests", col)

    def test_feed_sitemap_and_exports(self):
        feed = ET.fromstring(read(os.path.join(self.out, "feed.xml")).encode("utf-8"))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = feed.findall("a:entry", ns)
        self.assertEqual(len(entries), 6)
        for e in entries:
            self.assertLessEqual(e.find("a:updated", ns).text[:10], "2026-10-09")
        sm = ET.fromstring(read(os.path.join(self.out, "sitemap.xml")).encode("utf-8"))
        locs = [u.text for u in sm.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertIn("https://zdozier.com/publications/", locs)
        bib = read(os.path.join(self.out, "publications.bib"))
        self.assertEqual(len(re.findall(r"^@\w+\{", bib, re.M)), 6)
        self.assertIn("and others}", bib)
        self.assertIn("{N3C Consortium}", bib)
        self.assertIn("pages = {100--110}", bib)
        exported = json.loads(read(os.path.join(self.out, "publications.json")))
        self.assertEqual(exported["count"], 6)

    def test_funding_and_team_pages(self):
        funding = self.page("funding/index.html")
        self.assertIn("K01AI182501", funding)
        self.assertIn("Sep 2024 \u2013 Aug 2029", funding)
        team = self.page("team/index.html")
        self.assertIn("Anna Krasnova", team)
        self.assertIn("Postdoc", team)


class TestSparseData(BuildTestBase):
    def test_missing_optional_data_does_not_crash(self):
        base, out, rc = self.build_fixture(works=[], grants=[], software=False, run_report=False)
        try:
            self.assertEqual(rc, 0)
            home = read(os.path.join(out, "index.html"))
            self.assertIn("sync status unknown", home)
            self.assertNotIn("Current funding", home)
            self.assertNotIn(">Software<", home)
            self.assertTrue(os.path.exists(os.path.join(out, "publications", "index.html")))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_empty_directories(self):
        base = tempfile.mkdtemp(prefix="zdozier-test-")
        try:
            for d in ("content", "data"):
                os.makedirs(os.path.join(base, d))
            rc = build.main(["--content-dir", os.path.join(base, "content"), "--data-dir", os.path.join(base, "data"),
                             "--out", os.path.join(base, "site"), "--now", NOW, "--quiet"])
            self.assertEqual(rc, 0)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_phone_number_fails_the_build(self):
        base, out, rc = self.build_fixture(extra_cv={"summary_statement": "Call me at (555) 010-" + "0199."})
        try:
            self.assertEqual(rc, 1)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_refuses_to_empty_unrelated_directory(self):
        base = tempfile.mkdtemp(prefix="zdozier-test-")
        try:
            content, data = write_fixture(base)
            with open(os.path.join(base, "keep.txt"), "w") as fh:
                fh.write("x")
            with self.assertRaises(SystemExit):
                build.main(["--content-dir", content, "--data-dir", data, "--out", base, "--now", NOW])
            self.assertTrue(os.path.exists(os.path.join(base, "keep.txt")))
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestHelpers(unittest.TestCase):
    def test_nice_scale(self):
        self.assertEqual(charts.nice_scale(15), (15, 5))
        self.assertEqual(charts.nice_scale(7), (8, 2))
        self.assertEqual(charts.nice_scale(0), (1, 1))
        self.assertEqual(charts.nice_scale(156), (200, 50))

    def test_column_chart_marks(self):
        svg = str(charts.pubs_per_year_svg([{"year": 2024, "lead": 2, "co": 1}, {"year": 2025, "lead": 0, "co": 0},
                                            {"year": 2026, "lead": 0, "co": 3}]))
        self.assertEqual(svg.count('class="viz-lead"'), 1)
        self.assertEqual(svg.count('class="viz-co"'), 2)
        self.assertEqual(svg.count('tabindex="0"'), 2)  # empty years are not focus stops
        self.assertIn("2024: 3 works, 2 first or senior author, 1 co-author", svg)

    def test_scrub_and_dates(self):
        self.assertEqual(build.scrub("a\u2014b"), "a \u2013 b")
        self.assertEqual(build.scrub({"x": ["y \u2014 z"]}), {"x": ["y \u2013 z"]})
        self.assertEqual(build.fmt_period("2024-09", "2029-08"), "Sep 2024 \u2013 Aug 2029")
        self.assertEqual(build.fmt_period("2020-01", "2024-12", "year"), "2020\u20132024")
        self.assertEqual(build.fmt_period("2024-09", None, long=True, present="Present"),
                         "September 2024 \u2013 Present")
        self.assertEqual(build.page_range("438-443"), "438\u2013443")

    def test_site_author_names(self):
        self.assertEqual(build.author_site_name({"family": "Wang", "apa": "Wang, L.-C."}), "Wang\u00a0LC")
        self.assertEqual(build.author_site_name({"family": "Colford", "apa": "Colford, J. M., Jr."}), "Colford\u00a0JM\u00a0Jr")
        self.assertEqual(build.author_apa_name({"family": "Colford", "apa": "Colford, J. M., Jr."}),
                         "Colford,\u00a0J.\u00a0M.,\u00a0Jr.")
        self.assertEqual(build.author_site_name({"name": "N3C Consortium", "is_group": True}), "N3C Consortium")

    def test_find_chrome_and_page_count(self):
        exe = os.path.realpath(sys.executable)
        self.assertEqual(pdf.find_chrome({"CHROME_PATH": exe}), exe)
        self.assertEqual(pdf.count_pages(b"<< /Type /Pages /Kids [] >> /Type /Page /Type/Page"), 2)
        cmd = pdf.chrome_command("chrome", "http://127.0.0.1:1/cv/", "/tmp/x.pdf", "/tmp/p")
        self.assertIn("--headless=new", cmd)
        self.assertIn("--no-pdf-header-footer", cmd)
        self.assertIn("--print-to-pdf=/tmp/x.pdf", cmd)

    def test_js_is_small(self):
        size = os.path.getsize(os.path.join(ROOT, "static", "js", "site.js"))
        self.assertLess(size, 8 * 1024)


class TestReviewFixes(BuildTestBase):
    """Regression tests for the October 2026 review (chart window, sync labels,
    URL schemes, software descriptions, CV media URLs, APA group authors)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="zdozier-test-")
        works = fixture_works()
        works.append(work("10.1000/old", "A paper from 2010", [author("Kristin", "Bernard"), ME,
                                                               author("Ann", "Lee")], 2010))
        works.append(work("10.1000/group", "A consortium paper",
                          [ME, author("Yunwen", "Ji"), author("N3C Consortium", "N3C Consortium", group=True)],
                          2022, role="first"))
        bad = work("10.1000/bad", "A paper with odd links", [author("Ann", "Lee"), ME], 2021,
                   versions=[{"doi": "10.1101/2021.01.01.1", "server": "medRxiv", "year": 2021,
                              "url": "javascript:alert(2)"}])
        works.append(bad)
        works.append({"id": "manual:js", "doi": None, "title": "A manual entry with a script link",
                      "authors": [ME, author("Ann", "Lee")], "author_count": 2, "my_position": 1,
                      "role": "first", "year": 2019, "date": "2019", "venue": "Somewhere",
                      "type": "report", "status": "published", "url": "javascript:alert(9)",
                      "topics": [], "versions": [], "sources": ["manual"]})
        works.append({"id": "manual:inpress", "doi": None, "title": "An in-press article without a DOI",
                      "authors": [ME, author("Ann", "Lee")], "author_count": 2, "my_position": 1,
                      "role": "first", "year": 2026, "date": "2026", "venue": "A Journal",
                      "type": "article", "status": "accepted", "status_note": "In press",
                      "topics": [], "versions": [], "sources": ["manual"]})
        works.append({"id": "manual:chapter", "doi": None, "title": "A forthcoming chapter",
                      "authors": [ME], "author_count": 1, "my_position": 1, "role": "first",
                      "year": 2027, "date": "2027", "venue": "A Book", "type": "chapter",
                      "status": "accepted", "status_note": "Forthcoming", "topics": [],
                      "versions": [], "sources": ["manual"]})
        grants = [{"id": "manual-js", "title": "A grant with a script link", "funder": "Somebody",
                   "role": "Co-investigator", "start": "2025-01", "end": "2028-01", "status": "active",
                   "url": "javascript:alert(4)", "source": "manual", "pis": []}]
        media = [{"title": "A news story", "outlet": "A School", "author": "A. Writer",
                  "date": "May 1, 2024", "url": "https://example.org/news/story"}]
        content, data = write_fixture(cls.base, works=works, grants=grants, extra_cv={"media": media})
        site = yaml.safe_load(read(os.path.join(content, "site.yml")))
        site["software"] = {"include": ["LongCOVID", "HAL-in-R", "Odd"],
                            "descriptions": {"HAL-in-R": "Example R code for the highly adaptive lasso"},
                            "languages": {"hal-in-r": "R"}}
        site["research_themes"] = [{"id": "lc", "title": "Long COVID", "topics": ["covid"],
                                    "summary": "Studies of GLP-1 medications and COVID-19."}]
        with open(os.path.join(content, "site.yml"), "w") as fh:
            yaml.safe_dump(site, fh)
        repos = [{"name": "LongCOVID", "description": "pipeline for Long COVID research",
                  "html_url": "https://github.com/zbutzin/LongCOVID", "language": "Python", "fork": False},
                 {"name": "HAL-in-R", "description": None, "html_url": "https://github.com/zbutzin/HAL-in-R",
                  "language": "HTML", "fork": True},
                 {"name": "Odd", "description": "x", "html_url": "javascript:alert(3)", "fork": False}]
        with open(os.path.join(data, "software.json"), "w") as fh:
            json.dump({"repos": repos}, fh)
        cls.out = os.path.join(cls.base, "site")
        cls.rc = build.main(["--content-dir", content, "--data-dir", data, "--out", cls.out,
                             "--now", NOW, "--quiet"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def page(self, rel):
        return read(os.path.join(self.out, rel))

    def test_build_ok(self):
        self.assertEqual(self.rc, 0)

    def test_chart_window_and_summary(self):
        pubs = self.page("publications/index.html")
        # Earliest public year, not a range ending in the 2027 chapter's year.
        self.assertIn("12 works since 2010.", pubs)
        self.assertIn("1 preprint or manuscript under review.", pubs)
        self.assertIn("Plus 2 earlier works (2010).", pubs)
        years = [int(y) for y in re.findall(r'<g class="viz-col" data-tip="\[&#34;(\d{4})', pubs)]
        self.assertEqual(sorted(set(years)), list(range(2019, 2027)))
        # The in-press item without a DOI and the 2027 chapter are not charted.
        self.assertIn("2026: 2 works", pubs)
        self.assertNotIn("2027:", pubs)

    def test_sync_labels_update_in_the_browser(self):
        home = self.page("index.html")
        self.assertIn('<span data-synced="2026-10-06T14:02:00Z" data-cap>Synced 3 days ago</span>', home)
        pubs = self.page("publications/index.html")
        self.assertIn('<span data-synced="2026-10-06T14:02:00Z">synced 3 days ago</span>', pubs)
        js = read(os.path.join(self.out, "js", "site.js"))
        self.assertIn('hasAttribute("data-cap")', js)

    def test_only_http_links(self):
        for base, _, files in os.walk(self.out):
            for f in files:
                if f.endswith((".html", ".xml")):
                    text = read(os.path.join(base, f))
                    self.assertNotIn('href="javascript:', text, f)
                    self.assertNotIn("<link href=\"javascript:", text, f)
        self.assertNotIn("javascript:", read(os.path.join(self.out, "publications.bib")))
        home = self.page("index.html")
        self.assertIn('<span class="repo-name">Odd</span>', home)
        self.assertIn("A grant with a script link</span>", home)

    def test_software_descriptions_and_no_fork_label(self):
        home = self.page("index.html")
        self.assertIn('<p class="repo-desc">Example R code for the highly adaptive lasso</p>', home)
        self.assertIn('<p class="repo-desc">Pipeline for Long COVID research</p>', home)
        self.assertNotIn("<span>fork</span>", home)

    def test_cv_prints_media_urls_and_group_period(self):
        cv = self.page("cv/index.html")
        self.assertIn('A School</i>. <a href="https://example.org/news/story">https://example.org/news/story</a>', cv)
        self.assertIn("&amp; N3C Consortium. (2022).", cv)

    # Second October 2026 review.

    def test_software_language_override(self):
        home = self.page("index.html")
        hal = home.split('href="https://github.com/zbutzin/HAL-in-R"', 1)[1].split("</li>", 1)[0]
        self.assertIn("<span>R</span>", hal)
        self.assertNotIn("HTML", hal)

    def test_stat_strip_counts_works_not_publications(self):
        home = self.page("index.html")
        self.assertIn("<dt>Works</dt>", home)
        self.assertNotIn("<dt>Publications</dt>", home)
        self.assertIn('<dd class="stat-note">6 first or senior author</dd>', home)
        self.assertIn("Work counts from the list on this site, including 1 preprint or "
                      "manuscript under review.", home)
        self.assertIn('per year, <span class="nowrap">2023–2026</span>', home)

    def test_in_press_item_shows_no_year(self):
        pubs = self.page("publications/index.html")
        li = pubs.split('id="pub-manual-inpress"', 1)[1].split("</li>", 1)[0]
        self.assertNotIn('class="pub-year"', li)
        self.assertIn('data-year="2026"', pubs.split('id="pub-manual-inpress"', 1)[0][-400:])
        self.assertIn(">In press<", li)
        # Other entries keep the year, with the separator bound to the venue.
        self.assertIn('<span class="pub-sep" aria-hidden="true">&nbsp;·</span> <span class="pub-year">2025</span>', pubs)

    def test_codes_do_not_break_at_the_hyphen(self):
        self.assertEqual(str(build.nobreak_codes("GLP-1 & COVID-19 in SARS-CoV-2 <b>")),
                         '<span class="nowrap">GLP-1</span> &amp; <span class="nowrap">COVID-19</span> '
                         'in <span class="nowrap">SARS-CoV-2</span> &lt;b&gt;')
        self.assertEqual(str(build.nobreak_codes("post-acute sequelae")), "post-acute sequelae")
        home = self.page("index.html")
        self.assertIn('<span class="nowrap">GLP-1</span> medications and <span class="nowrap">COVID-19</span>.', home)
        # The feed keeps plain titles.
        self.assertNotIn("nowrap", read(os.path.join(self.out, "feed.xml")))

    def test_preprint_chip_says_under_review_too(self):
        pubs = self.page("publications/index.html")
        self.assertIn('for="show-preprints">Preprints and under review<span class="chip-n">', pubs)

    def test_cv_shares_the_site_left_edge(self):
        cv = self.page("cv/index.html")
        self.assertIn('<div class="wrap">\n<article class="cv">', cv)
        self.assertNotIn('class="cv wrap"', cv)

    def test_colophon_time_can_wrap(self):
        col = self.page("colophon/index.html")
        self.assertIn('<td class="num"><span class="nowrap">Oct 6, 2026,</span> <span class="nowrap">14:00 UTC</span></td>', col)
        css = read(os.path.join(self.out, "css", "site.css"))
        self.assertIn('counter(page) " of " counter(pages)', css)


class TestContentRules(unittest.TestCase):
    """Facts in content/publications.yml that a later edit must not undo."""

    def test_n3c_preprints_name_the_consortium(self):
        from common import load_yaml
        ov = load_yaml(os.path.join(ROOT, "content", "publications.yml"))["overrides"]
        for doi in ("10.64898/2026.07.06.26357398", "10.64898/2026.02.27.26347274",
                    "10.64898/2026.07.06.26357401", "10.64898/2026.07.03.26357246",
                    "10.1101/2025.06.06.25329168"):
            self.assertIn("N3C", ov[doi].get("group_author") or "", doi)


class TestSyncTime(unittest.TestCase):
    def test_fetch_time_wins_over_merge_time(self):
        now = build.parse_iso("2026-10-11T22:00:00Z")
        report = {"finished": "2026-10-11T21:42:49Z",
                  "fetch": {"started": "2026-10-09T20:42:00Z", "finished": "2026-10-09T20:43:43Z"},
                  "sources": {}}
        self.assertEqual(build.sync_info(report, now)["label"], "synced 2 days ago")
        self.assertEqual(build.sync_info({"finished": "2026-10-11T21:42:49Z", "fetch": {"finished": None}},
                                         now)["label"], "synced today")


class TestChartHelpers(unittest.TestCase):
    def rows(self, years):
        return [{"year": y, "lead": 1 if y in years else 0, "co": 0} for y in range(min(years), max(years) + 1)]

    def test_window_and_note(self):
        shown, earlier = build.chart_window(self.rows([2010, 2012, 2020, 2026]))
        self.assertEqual([r["year"] for r in shown], list(range(2019, 2027)))
        self.assertEqual(build.earlier_note(earlier), "Plus 2 earlier works (2010, 2012).")
        shown, earlier = build.chart_window(self.rows([2024, 2026]))
        self.assertEqual([r["year"] for r in shown], [2024, 2025, 2026])
        self.assertEqual(build.earlier_note(earlier), "")
        many = self.rows([2001, 2003, 2005, 2007, 2030])
        self.assertEqual(build.earlier_note(build.chart_window(many)[1]),
                         "Plus 4 earlier works (2001\u20132007).")

    def test_is_charted(self):
        self.assertTrue(build.is_charted({"year": 2026, "status": "preprint"}, 2026))
        self.assertTrue(build.is_charted({"year": 2026, "status": "accepted", "doi": "10.1/x"}, 2026))
        self.assertFalse(build.is_charted({"year": 2026, "status": "accepted"}, 2026))
        self.assertFalse(build.is_charted({"year": 2027, "status": "published", "doi": "10.1/x"}, 2026))

    def test_duplicate_yaml_key_stops_the_build(self):
        base = tempfile.mkdtemp(prefix="zdozier-test-")
        try:
            content, data = write_fixture(base)
            with open(os.path.join(content, "publications.yml"), "a") as fh:
                fh.write("topics: []\n")
            saved, sys.stderr = sys.stderr, io.StringIO()
            try:
                with self.assertRaises(SystemExit) as cm:
                    build.main(["--content-dir", content, "--data-dir", data,
                                "--out", os.path.join(base, "site"), "--now", NOW, "--quiet"])
            finally:
                sys.stderr = saved
            self.assertEqual(cm.exception.code, 1)
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
