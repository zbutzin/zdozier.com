"""Offline tests for the network layer: scripts/fetch.py, common.Http, and the
Crossref and OpenAlex sources. Fake sessions only; no network."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402

ORCID = "0000-0001-6419-0008"
SECRET = "SECRETKEY_TEST123"


class Resp(object):
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return self._body


class Session(object):
    """Answers each request with handler(method, url, params) or raises."""

    def __init__(self, handler):
        self.headers = {}
        self.handler = handler
        self.calls = []

    def request(self, method, url, params=None, **kw):
        self.calls.append((method, url, params))
        return self.handler(method, url, params or {})


def quiet():
    saved, sys.stderr = sys.stderr, io.StringIO()
    return saved


class TestScrub(unittest.TestCase):
    def test_params_and_env_values(self):
        text = "url: /authors/x?api_key=abc123&mailto=z@x.org&per-page=200 (Caused by X)"
        self.assertEqual(common.scrub(text),
                         "url: /authors/x?api_key=REDACTED&mailto=REDACTED&per-page=200 (Caused by X)")
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": SECRET}):
            self.assertEqual(common.scrub("token " + SECRET + " end"), "token REDACTED end")
        self.assertIsNone(common.scrub(None))

    def test_connection_error_message_never_holds_the_key(self):
        import requests

        def boom(method, url, params):
            raise requests.ConnectionError(
                "HTTPSConnectionPool(host='api.openalex.org', port=443): Max retries exceeded "
                "with url: /authors/A1?api_key=%s (Caused by ProxyError)" % SECRET)
        h = common.Http(session=Session(boom), backoff=0)
        with self.assertRaises(common.HttpError) as ctx:
            h.get_json("https://api.openalex.org/authors/A1", params={"api_key": SECRET})
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIn("api_key=REDACTED", str(ctx.exception))
        self.assertTrue(ctx.exception.transient)


class TestHttpPolicy(unittest.TestCase):
    def test_429_is_retried_once_and_retry_after_is_capped(self):
        waits = []
        s = Session(lambda m, u, p: Resp(429, headers={"Retry-After": "120"}))
        with mock.patch.object(common.time, "sleep", waits.append):
            with self.assertRaises(common.HttpError) as ctx:
                common.Http(session=s, backoff=1.5).get_json("https://api.example.org/x")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(s.calls), 2)
        self.assertEqual(waits, [common.RETRY_AFTER_MAX])

    def test_deadline_stops_requests(self):
        s = Session(lambda m, u, p: Resp(503))
        h = common.Http(session=s, backoff=5)
        h.deadline = time.time() + 1  # the first backoff (5s) would pass it
        with mock.patch.object(common.time, "sleep", lambda x: None):
            with self.assertRaises(common.HttpError) as ctx:
                h.get_json("https://api.example.org/x")
        self.assertTrue(ctx.exception.budget)
        self.assertEqual(len(s.calls), 1)
        h.deadline = time.time() - 1
        with self.assertRaises(common.HttpError):
            h.get_json("https://api.example.org/x")
        self.assertEqual(len(s.calls), 1)


class CrossrefBase(unittest.TestCase):
    def setUp(self):
        from sources import crossref
        self.crossref = crossref
        p = mock.patch.object(crossref.time, "sleep", lambda x: None)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(common.time, "sleep", lambda x: None)
        q.start()
        self.addCleanup(q.stop)

    def ctx(self, handler, cache, dois):
        http = common.Http(session=Session(handler), backoff=0)
        prev = {"orcid_filter": {"dois": dois}, "works": cache}
        raw = {"orcid": {"works": [{"dois": dois}]}}
        return {"http": http, "site": {}, "ident": {"orcid": ORCID}, "prev": prev, "raw": raw,
                "pubs": {}, "now": "2026-10-12T14:05:00Z", "options": {}}

    def aged_cache(self, dois):
        return {d: {"fetched_at": "2026-09-01T14:00:00Z", "status": 200,
                    "message": {"DOI": d, "title": ["T " + d], "author": []}} for d in dois}


class TestCrossrefFailures(CrossrefBase):
    def test_rate_limit_keeps_the_cache_and_fails_the_source(self):
        dois = ["10.1000/%d" % i for i in range(20)]
        cache = self.aged_cache(dois)
        before = repr(cache)
        ctx = self.ctx(lambda m, u, p: Resp(429), cache, dois)
        with self.assertRaises(common.HttpError) as exc:
            self.crossref.fetch(ctx)
        self.assertIn("rate limited", str(exc.exception))
        self.assertEqual(repr(cache), before)
        # The breaker stops after 3 lookups (filter: 2 calls, lookups: 2 each).
        self.assertLessEqual(len(ctx["http"].session.calls), 2 + 3 * 2)

    def test_only_unknown_dois_are_cached_as_misses(self):
        dois = ["10.1000/known", "10.1000/gone", "10.1000/new"]
        cache = self.aged_cache(["10.1000/known", "10.1000/gone"])

        def handler(m, u, p):
            if u.endswith("/works"):
                return Resp(200, {"message": {"items": [], "next-cursor": None}})
            if u.endswith("10.1000/known"):
                return Resp(403, "forbidden")
            if u.endswith("10.1000/gone"):
                return Resp(410, "gone")
            return Resp(404)
        payload, _ = self.crossref.fetch(self.ctx(handler, cache, dois))
        works = payload["works"]
        self.assertEqual(works["10.1000/known"]["status"], 200)   # 403: record kept
        self.assertEqual(works["10.1000/gone"]["status"], 200)    # 410 never replaces a good record
        self.assertEqual(works["10.1000/new"]["status"], 404)
        self.assertIn("lookups failed", payload["partial_error"])

    def test_filter_failure_with_no_lookup_fails_the_source(self):
        dois = ["10.1000/a"]
        cache = {"10.1000/a": {"fetched_at": "2026-10-10T00:00:00Z", "status": 200,
                               "message": {"DOI": "10.1000/a"}}}
        with self.assertRaises(common.HttpError) as exc:
            self.crossref.fetch(self.ctx(lambda m, u, p: Resp(503), cache, dois))
        self.assertIn("unreachable", str(exc.exception))

    def test_weekly_miss_retry_allows_for_start_time_drift(self):
        now = common.parse_iso_datetime("2026-10-12T14:05:00Z")
        miss = {"fetched_at": "2026-10-05T14:20:00Z", "status": 404}
        self.assertFalse(self.crossref._fresh(miss, now))
        hit = {"fetched_at": "2026-09-12T14:20:00Z", "status": 200}
        self.assertFalse(self.crossref._fresh(hit, now))
        self.assertTrue(self.crossref._fresh({"fetched_at": "2026-10-10T00:00:00Z", "status": 404}, now))


class TestOpenAlexCounts(unittest.TestCase):
    def test_partial_list_raises(self):
        from sources import openalex

        def handler(m, u, p):
            if "/authors/" in u:
                return Resp(200, {"id": "https://openalex.org/A1", "works_count": 56})
            if p.get("cursor") == "*":
                return Resp(200, {"meta": {"count": 56, "next_cursor": "c2"},
                                  "results": [{"id": "W%d" % i} for i in range(20)]})
            return Resp(200, {"meta": {"count": 56, "next_cursor": None}, "results": []})
        ctx = {"http": common.Http(session=Session(handler), backoff=0), "site": {},
               "ident": {"orcid": ORCID}, "now": "2026-10-12T14:05:00Z"}
        with self.assertRaises(common.HttpError) as exc:
            openalex.fetch(ctx)
        self.assertIn("20 of 56", str(exc.exception))


class FetchRun(unittest.TestCase):
    """Runs fetch.main against a temporary data/raw with patched sources."""

    def setUp(self):
        import fetch
        self.fetch = fetch
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "raw"))
        self.raw = os.path.join(self.d, "raw")
        for p in [mock.patch.object(fetch, "raw_path", lambda n: os.path.join(self.raw, n + ".json")),
                  mock.patch.object(fetch, "STATUS_PATH", os.path.join(self.raw, "fetch_status.json")),
                  mock.patch.object(fetch, "load_site_config",
                                    lambda: {"identity": {"orcid": ORCID, "github_user": "zbutzin"}}),
                  mock.patch.object(fetch, "load_yaml", lambda *a, **k: {})]:
            p.start()
            self.addCleanup(p.stop)

    def patch_sources(self, fns):
        import importlib
        from sources import ORDER

        def ok(name):
            return lambda ctx: ({"source": name, "fetched_at": ctx["now"], "works": []}, 0)
        for name in ORDER:
            mod = importlib.import_module("sources.%s" % name)
            p = mock.patch.object(mod, "fetch", fns.get(name) or ok(name))
            p.start()
            self.addCleanup(p.stop)

    def main(self, argv=()):
        saved = quiet()
        try:
            self.assertEqual(self.fetch.main(list(argv)), 0)
        finally:
            sys.stderr = saved
        return common.load_json(os.path.join(self.raw, "fetch_status.json"))

    def test_api_key_never_reaches_committed_files(self):
        import requests
        import merge
        from sources import openalex

        def boom(method, url, params):
            raise requests.ConnectionError(
                "HTTPSConnectionPool(host='api.openalex.org', port=443): Max retries exceeded "
                "with url: /authors/orcid:%s?api_key=%s (Caused by ProxyError)" % (ORCID, SECRET))
        self.patch_sources({"openalex": openalex.fetch})
        fake = mock.patch.object(self.fetch, "Http",
                                 lambda: common.Http(session=Session(boom), backoff=0))
        fake.start()
        self.addCleanup(fake.stop)
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": SECRET}):
            status = self.main(["--only", "openalex"])
            self.assertFalse(status["sources"]["openalex"]["ok"])
            saved = quiet()
            try:
                merge.main(["--data-dir", self.d, "--content-dir", os.path.join(ROOT, "content"),
                            "--today", "2026-10-12"])
            finally:
                sys.stderr = saved
        for name in ("raw/fetch_status.json", "run_report.json"):
            with open(os.path.join(self.d, name)) as fh:
                text = fh.read()
            self.assertNotIn(SECRET, text, name)
        self.assertIn("api_key=REDACTED", status["sources"]["openalex"]["error"])

    def test_suspicious_drop_keeps_previous_copy_until_it_repeats(self):
        prev = {"source": "github", "fetched_at": "2026-10-05T14:00:00Z",
                "repos": [{"name": "r%d" % i} for i in range(6)]}
        common.write_json_atomic(os.path.join(self.raw, "github.json"), prev)
        answer = {"repos": []}

        def gh(ctx):
            return {"source": "github", "fetched_at": ctx["now"], "repos": answer["repos"]}, \
                len(answer["repos"])
        self.patch_sources({"github": gh})
        # An empty answer is never accepted.
        for _ in range(2):
            st = self.main()["sources"]["github"]
            self.assertEqual((st["ok"], st["used_cache"], st["count"]), (False, True, 6))
            self.assertIn("suspicious drop from 6 to 0", st["error"])
            self.assertEqual(common.load_json(os.path.join(self.raw, "github.json")), prev)
        # A smaller non-zero answer is held once, then accepted when it repeats.
        answer["repos"] = [{"name": "r0"}, {"name": "r1"}]
        st = self.main()["sources"]["github"]
        self.assertEqual((st["ok"], st["candidate_count"]), (False, 2))
        st = self.main()["sources"]["github"]
        self.assertTrue(st["ok"])
        self.assertEqual(len(common.load_json(os.path.join(self.raw, "github.json"))["repos"]), 2)

    def test_status_file_is_written_while_running(self):
        seen = {}

        def europepmc(ctx):
            seen["status"] = common.load_json(os.path.join(self.raw, "fetch_status.json"))
            return {"source": "europepmc", "fetched_at": ctx["now"], "results": []}, 0
        self.patch_sources({"europepmc": europepmc})
        final = self.main()
        mid = seen["status"]
        self.assertIsNone(mid["finished"])
        self.assertTrue(mid["sources"]["orcid"]["ok"])           # ran before Europe PMC
        self.assertEqual(mid["sources"]["github"]["error"], "fetch did not finish")
        self.assertTrue(final["finished"])
        self.assertTrue(final["sources"]["github"]["ok"])


if __name__ == "__main__":
    unittest.main()
