"""Offline tests for scripts/common.py helpers (no network)."""
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402
from common import (apa_name, author_from_string, is_group_name, is_me, make_author,  # noqa: E402
                    normalize_doi, parse_name, preprint_server)

ORCID = "0000-0001-6419-0008"
ME = {"family_names": ["Butzin-Dozier", "Butzin Dozier", "Dozier"], "given_initial": "Z"}


class TestDoiNormalization(unittest.TestCase):
    def test_prefixes_and_case(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1186/S12916-024-03655-X"),
                         "10.1186/s12916-024-03655-x")
        self.assertEqual(normalize_doi("http://dx.doi.org/10.2196/53322"), "10.2196/53322")
        self.assertEqual(normalize_doi("doi:10.1101/2024.02.05.24302352"),
                         "10.1101/2024.02.05.24302352")
        self.assertEqual(normalize_doi("DOI: 10.1093/epirev/mxac003"), "10.1093/epirev/mxac003")
        self.assertEqual(normalize_doi("doi.org/10.1093/OFID/OFAF731"), "10.1093/ofid/ofaf731")

    def test_whitespace_and_parentheses(self):
        self.assertEqual(normalize_doi("  10.1016/S2352-3018(25)00302-9 \n"),
                         "10.1016/s2352-3018(25)00302-9")
        self.assertEqual(normalize_doi("10.1016/j.ebiom.2024.105333."), "10.1016/j.ebiom.2024.105333")

    def test_url_encoded(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1101%2F2024.02.05.24302352"),
                         "10.1101/2024.02.05.24302352")

    def test_not_a_doi(self):
        self.assertIsNone(normalize_doi("not a doi"))
        self.assertIsNone(normalize_doi(""))
        self.assertIsNone(normalize_doi(None))
        self.assertIsNone(normalize_doi("https://pubmed.ncbi.nlm.nih.gov/39380062"))


class TestPreprintDetection(unittest.TestCase):
    def test_each_prefix(self):
        cases = {
            "10.1101/2024.02.05.24302352": "medRxiv",
            "10.1101/2024.02.05.578123": "bioRxiv",
            "10.1101/123456": "bioRxiv",
            "10.64898/2026.07.06.26357398": "medRxiv",
            "10.2196/preprints.53322": "JMIR Preprints",
            "10.21203/rs.3.rs-455696/v1": "Research Square",
            "10.2139/ssrn.3918273": "SSRN",
            "10.48550/arxiv.2511.01077": "arXiv",
            "10.31219/osf.io/abcd3": "OSF Preprints",
            "10.20944/preprints202401.0001.v1": "Preprints.org",
        }
        for doi, server in cases.items():
            self.assertEqual(preprint_server(doi), server, doi)
            self.assertTrue(common.is_preprint_doi(doi), doi)

    def test_server_hint_for_cshl_prefix(self):
        self.assertEqual(preprint_server("10.1101/2024.02.05.578123", "medRxiv"), "medRxiv")
        self.assertEqual(preprint_server("10.1101/2024.02.05.24302352", "bioRxiv"), "bioRxiv")

    def test_not_preprints(self):
        for doi in ("10.2196/53322", "10.1186/s12916-024-03655-x", "10.1101/gr.275123.121",
                    "10.1101/cshperspect.a041234", "10.1016/j.psyneuen.2024.107023",
                    "10.17615/0sh5-mt86"):
            self.assertIsNone(preprint_server(doi), doi)
            self.assertFalse(common.is_preprint_doi(doi), doi)

    def test_repository_and_jmir_pair(self):
        self.assertTrue(common.is_repository_doi("10.17615/qzk6-0023"))
        self.assertFalse(common.is_repository_doi("10.1186/s12916-024-03655-x"))
        self.assertEqual(common.jmir_pair("10.2196/preprints.53322"), "10.2196/53322")
        self.assertEqual(common.jmir_pair("10.2196/53322"), "10.2196/preprints.53322")
        self.assertIsNone(common.jmir_pair("10.2196/jmir.1234"))


class TestMeDetection(unittest.TestCase):
    def a(self, raw, orcid=None):
        return author_from_string(raw, orcid=orcid, me_cfg=ME, my_orcid=ORCID)

    def test_name_rule(self):
        self.assertTrue(self.a("Butzin-Dozier, Z.")["is_me"])
        self.assertTrue(self.a("Zachary Butzin-Dozier")["is_me"])
        self.assertTrue(self.a("Zach Butzin-Dozier")["is_me"])
        self.assertTrue(self.a("Butzin-Dozier Z")["is_me"])           # PubMed style
        self.assertTrue(self.a("Dozier ZB")["is_me"])
        self.assertTrue(self.a("Zachary B. Dozier")["is_me"])
        self.assertTrue(self.a("Zachary Butzin Dozier")["is_me"])
        self.assertTrue(self.a("Butzin\u2010Dozier, Zachary")["is_me"])  # Unicode hyphen

    def test_never_mary_dozier(self):
        self.assertFalse(self.a("Dozier, M.")["is_me"])
        self.assertFalse(self.a("Mary Dozier")["is_me"])
        self.assertFalse(self.a("Dozier M")["is_me"])
        self.assertFalse(self.a("Dozier")["is_me"])                    # no given name

    def test_orcid_wins_when_present(self):
        # OpenAlex attaches the author ORCID; a name the rule would miss still matches.
        self.assertTrue(self.a("Butzin, Zachary", orcid="https://orcid.org/" + ORCID)["is_me"])
        # A different ORCID means a different person even with a matching name.
        self.assertFalse(self.a("Zachary Dozier", orcid="0000-0002-1825-0097")["is_me"])
        self.assertEqual(is_me({"given": "Z", "family": "Smith", "orcid": ORCID}, ME, ORCID), True)

    def test_group_is_never_me(self):
        self.assertFalse(self.a("Dozier Study Group")["is_me"])


class TestNameParsing(unittest.TestCase):
    def test_apa_forms(self):
        cases = {
            "Colford, J. M., Jr.": "Colford, J. M., Jr.",
            "John M. Colford Jr.": "Colford, J. M., Jr.",
            "Colford JM Jr": "Colford, J. M., Jr.",
            "Mark J. van der Laan": "van der Laan, M. J.",
            "van der Laan MJ": "van der Laan, M. J.",
            "Van Der Laan, M. J.": "Van Der Laan, M. J.",
            "Zachary Butzin-Dozier": "Butzin-Dozier, Z.",
            "Butzin-Dozier, Zachary": "Butzin-Dozier, Z.",
            "Lin-Chiun Wang": "Wang, L.-C.",
            "Shi, J. (Seraphina)": "Shi, J.",
            "Md. Ziaur Rahman": "Rahman, M. Z.",
            "Da Kyung Jung": "Jung, D. K.",
            "Essam Nkodo, E. N.": "Essam Nkodo, E. N.",
            "Il'yasova D": "Il'yasova, D.",
        }
        for raw, apa in cases.items():
            self.assertEqual(apa_name(author_from_string(raw)), apa, raw)

    def test_given_family_split(self):
        p = parse_name("Mark J. van der Laan")
        self.assertEqual((p["given"], p["family"]), ("Mark J.", "van der Laan"))
        p = parse_name("Colford, J. M., Jr.")
        self.assertEqual((p["given"], p["family"], p["suffix"]), ("J. M.", "Colford", "Jr."))
        p = parse_name("Le Chen")
        self.assertEqual((p["given"], p["family"]), ("Le", "Chen"))

    def test_crossref_style_suffix_in_given(self):
        a = make_author(given="John M., Jr.", family="Colford")
        self.assertEqual(a["apa"], "Colford, J. M., Jr.")
        self.assertEqual(a["name"], "John M. Colford Jr.")


class TestHyphenatedInitials(unittest.TestCase):
    def test_hyphen_survives_in_initials(self):
        self.assertEqual(common.given_initials("L.-C."), "L.-C.")
        self.assertEqual(common.given_initials("Lin-Chiun"), "L.-C.")
        self.assertEqual(common.given_initials("J.M."), "J. M.")
        self.assertEqual(common.author_from_string("Wang, L.-C.")["apa"], "Wang, L.-C.")


class TestGroupAuthors(unittest.TestCase):
    def test_groups(self):
        for s in ("N3C Consortium", "the National COVID Cohort Collaborative (N3C) Consortium",
                  "on behalf of the National Clinical Cohort Collaborative",
                  "National COVID Cohort Collaborative", "WASH Benefits Study Team",
                  "The RECOVER Initiative"):
            self.assertTrue(is_group_name(s), s)
            self.assertTrue(author_from_string(s)["is_group"], s)

    def test_people(self):
        for s in ("Butzin-Dozier, Z.", "Mary Dozier", "Groupman, A.", "Le Chen",
                  "Theodore Teamson"):
            self.assertFalse(is_group_name(s), s)


class TestTitlesAndIO(unittest.TestCase):
    def test_clean_title(self):
        self.assertEqual(common.clean_title("Stress biomarkers in <i>Bangladesh</i>."),
                         "Stress biomarkers in Bangladesh")
        self.assertEqual(common.clean_title("Predicting Long COVID (Preprint)"), "Predicting Long COVID")
        self.assertEqual(
            common.clean_title("EFFECTS OF WASH ON IMMUNE STATUS IN RURAL BANGLADESH"),
            "Effects of WASH on immune status in rural Bangladesh")
        self.assertEqual(common.clean_title("Mixed Case Stays: As Is"), "Mixed Case Stays: As Is")
        self.assertNotIn("\u2014", common.clean_title("Long COVID \u2014 a review"))

    def test_partial_dates(self):
        self.assertEqual(common.partial_date(2024, 10, 8), "2024-10-08")
        self.assertEqual(common.partial_date("2024", "02", None), "2024-02")
        self.assertEqual(common.clean_date("2024-10-08T00:00:00"), "2024-10-08")
        self.assertTrue(common.date_sort_key("2026") > common.date_sort_key("2026-12-01"))

    def test_atomic_write_and_missing_yaml(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "x.json")
        common.write_json_atomic(p, {"a": 1})
        self.assertEqual(common.load_json(p), {"a": 1})
        self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp")], [])
        self.assertEqual(common.load_yaml(os.path.join(d, "missing.yml"), {}), {})
        empty = os.path.join(d, "empty.yml")
        open(empty, "w").close()
        self.assertEqual(common.load_yaml(empty, {}), {})

    def _yaml_error(self, text):
        """Load `text` as a content file; return the one-line error it prints."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        bad = os.path.join(d, "publications.yml")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(text)
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            with self.assertRaises(SystemExit) as cm:
                common.load_yaml(bad, {})
            msg = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(msg.strip().splitlines()), 1, msg)
        self.assertNotIn("Traceback", msg)
        return msg

    def test_invalid_yaml_stops_with_one_line(self):
        msg = self._yaml_error("a: [unclosed\n")
        self.assertIn("publications.yml line 2", msg)
        msg = self._yaml_error('overrides:\n  "10.1/x":\n    contribution: Supported analysis: wrote code\n')
        self.assertIn("line 3", msg)
        self.assertIn("colon must be in quotes", msg)

    def test_repeated_keys_are_refused(self):
        # A second top-level block would silently replace the first one.
        msg = self._yaml_error('overrides:\n  "10.1/a":\n    featured: true\nhide: []\n'
                               'overrides:\n  "10.1/b":\n    contribution: "Note."\n')
        self.assertIn("duplicate key 'overrides' (first used on line 1)", msg)
        self.assertIn("line 5", msg)
        # The same DOI twice under overrides: is refused too.
        msg = self._yaml_error('overrides:\n  "10.1/a":\n    topics: [covid]\n'
                               '  "10.1/a":\n    contribution: "Note."\n')
        self.assertIn("duplicate key '10.1/a'", msg)

    def test_content_files_load(self):
        # No false positives on the real content files, and same result as safe_load.
        import yaml
        for name in ("site.yml", "cv.yml", "publications.yml", "funding.yml"):
            path = os.path.join(common.CONTENT_DIR, name)
            with open(path, encoding="utf-8") as fh:
                plain = yaml.safe_load(fh)
            self.assertEqual(common.load_yaml(path, {}), plain, name)

    def test_pretty_org(self):
        self.assertEqual(common.pretty_org("UNIVERSITY OF CALIFORNIA BERKELEY"),
                         "University of California, Berkeley")
        self.assertEqual(common.pretty_org("STANFORD UNIVERSITY"), "Stanford University")


class _Resp(object):
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session(object):
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


class TestHttp(unittest.TestCase):
    def test_retries_then_succeeds(self):
        s = _Session([_Resp(503), _Resp(429, headers={"Retry-After": "0"}), _Resp(200, {"ok": 1})])
        h = common.Http(session=s, backoff=0)
        self.assertEqual(h.get_json("https://api.example.org/x"), {"ok": 1})
        self.assertEqual(len(s.calls), 3)
        self.assertEqual(s.headers["User-Agent"], common.USER_AGENT)
        self.assertEqual(s.calls[0][2]["timeout"], (10, 30))  # (connect, read)

    def test_gives_up_after_three_retries(self):
        s = _Session([_Resp(500)] * 4)
        h = common.Http(session=s, backoff=0)
        with self.assertRaises(common.HttpError):
            h.get_json("https://api.example.org/x")
        self.assertEqual(len(s.calls), 4)

    def test_client_errors_do_not_retry(self):
        s = _Session([_Resp(400, {"error": "bad"})])
        with self.assertRaises(common.HttpError) as ctx:
            common.Http(session=s, backoff=0).get_json("https://api.example.org/x")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(len(s.calls), 1)
        s = _Session([_Resp(404)])
        self.assertIsNone(common.Http(session=s).get_json("https://api.example.org/x",
                                                          allow_404=True))


class TestFetchFallback(unittest.TestCase):
    def test_failing_source_keeps_previous_raw_file(self):
        from unittest import mock
        import importlib
        import json
        import fetch
        from sources import ORDER
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        old = {"source": "github", "fetched_at": "2026-10-01T00:00:00Z", "repos": [{"name": "a"}]}
        common.write_json_atomic(os.path.join(d, "github.json"), old)

        def ok(name):
            return lambda ctx: ({"source": name, "fetched_at": ctx["now"], "works": []}, 0)

        def boom(ctx):
            raise RuntimeError("network down")

        patches = [mock.patch.object(fetch, "raw_path", lambda n: os.path.join(d, n + ".json")),
                   mock.patch.object(fetch, "STATUS_PATH", os.path.join(d, "fetch_status.json")),
                   mock.patch.object(fetch, "load_site_config",
                                     lambda: {"identity": {"orcid": ORCID}}),
                   mock.patch.object(fetch, "load_yaml", lambda *a, **k: {})]
        for name in ORDER:
            mod = importlib.import_module("sources.%s" % name)
            patches.append(mock.patch.object(mod, "fetch", boom if name == "github" else ok(name)))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(fetch.main([]), 0)
        finally:
            sys.stderr = saved
        status = common.load_json(os.path.join(d, "fetch_status.json"))["sources"]
        self.assertFalse(status["github"]["ok"])
        self.assertTrue(status["github"]["used_cache"])
        self.assertIn("network down", status["github"]["error"])
        self.assertEqual(status["github"]["count"], 1)
        self.assertTrue(status["orcid"]["ok"])
        self.assertEqual(common.load_json(os.path.join(d, "github.json")), old)
        self.assertTrue(os.path.exists(os.path.join(d, "orcid.json")))


if __name__ == "__main__":
    unittest.main()
