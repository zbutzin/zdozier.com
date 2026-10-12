"""Tests for scripts/ci_issue.py (the "New publications detected" issue body)."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ci_issue.py")


def load_module():
    spec = importlib.util.spec_from_file_location("ci_issue", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_issue = load_module()

REPORT = {
    "started": "2026-10-12T14:00:05Z",
    "finished": "2026-10-12T14:04:40Z",
    "ok": True,
    "sources": {"orcid": {"ok": True, "count": 45, "error": None, "used_cache": False}},
    "new_works": [
        {
            "id": "10.1101/2026.09.30.26300001",
            "title": "Example preprint with *starred* words in its title",
            "venue": "medRxiv",
            "sources": ["europepmc"],
        },
        {
            "id": "10.1186/s12916-024-03655-x",
            "title": "SSRI use during acute COVID-19 and risk of Long COVID among patients with depression",
            "venue": "BMC Medicine",
            "sources": ["orcid", "openalex", "crossref"],
        },
        {
            "id": "manual:example-entry",
            "title": "Example manual entry without a DOI",
            "venue": None,
            "sources": ["manual"],
        },
    ],
    "removed_works": [],
    "new_grants": [],
    "warnings": [
        "10.1101/2026.09.30.26300001 found only by name search (not ORCID-linked)",
        "crossref: 1 DOI returned 404",
    ],
}

PUBLICATIONS = {
    "generated": "2026-10-12T14:04:00Z",
    "works": [
        {
            "id": "10.1186/s12916-024-03655-x",
            "doi": "10.1186/s12916-024-03655-x",
            "year": 2024,
            "status": "published",
            "role": "first",
            "venue": "BMC Medicine",
        },
        {
            "id": "10.1101/2026.09.30.26300001",
            "doi": "10.1101/2026.09.30.26300001",
            "year": 2026,
            "status": "preprint",
            "role": "coauthor",
        },
        {"id": "manual:example-entry", "doi": None, "year": 2026, "status": "under-review",
         "role": "senior"},
    ],
}


class CiIssueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ci_issue_test_")
        self.report_path = os.path.join(self.tmp, "run_report.json")
        self.pubs_path = os.path.join(self.tmp, "publications.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, obj):
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)

    def run_script(self, *extra):
        cmd = [sys.executable, SCRIPT, "--report", self.report_path,
               "--publications", self.pubs_path] + list(extra)
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def full_body(self):
        self.write(self.report_path, REPORT)
        self.write(self.pubs_path, PUBLICATIONS)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    # Nothing new: no output, exit 0.

    def test_empty_new_works_prints_nothing(self):
        report = dict(REPORT, new_works=[])
        self.write(self.report_path, report)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        proc = self.run_script("--count")
        self.assertEqual(proc.stdout.strip(), "0")

    def test_missing_report_prints_nothing(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.run_script("--count").stdout.strip(), "0")

    def test_malformed_report_still_exits_zero(self):
        self.write(self.report_path, "{not json")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.write(self.report_path, {"new_works": "oops"})
        self.assertEqual(self.run_script().stdout, "")
        self.assertEqual(self.run_script("--count").stdout.strip(), "0")

    def test_bad_arguments_exit_zero(self):
        proc = subprocess.run([sys.executable, SCRIPT, "--no-such-flag"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0)

    # New works present.

    def test_count(self):
        self.write(self.report_path, REPORT)
        self.assertEqual(self.run_script("--count").stdout.strip(), "3")

    def test_body_lists_each_work(self):
        body = self.full_body()
        self.assertIn("found 3 new publications", body)
        self.assertIn("2026-10-12", body)
        self.assertIn("SSRI use during acute COVID-19", body)
        self.assertIn("*BMC Medicine*, 2024", body)
        self.assertIn("[10.1186/s12916-024-03655-x](https://doi.org/10.1186/s12916-024-03655-x)", body)
        self.assertIn("Found by: ORCID, OpenAlex, Crossref", body)
        self.assertIn("First author", body)
        self.assertIn("Preprint", body)
        self.assertIn("Senior author", body)
        self.assertIn("Under review", body)
        self.assertIn("manual:example-entry", body)
        self.assertIn("From the `manual:` list", body)

    def test_newest_first(self):
        body = self.full_body()
        self.assertLess(body.index("Example preprint"), body.index("SSRI use"))

    def test_markdown_is_escaped(self):
        body = self.full_body()
        self.assertIn(r"\*starred\*", body)

    def test_name_search_only_flag_and_related_warning(self):
        body = self.full_body()
        self.assertIn("Only Europe PMC found this one", body)
        self.assertIn("found only by name search", body)
        # Warnings that do not mention a new work are left out.
        self.assertNotIn("returned 404", body)

    def test_what_you_can_do_section(self):
        body = self.full_body()
        self.assertIn("### What you can do", body)
        self.assertIn("**Nothing.**", body)
        self.assertIn("content/publications.yml", body)

    def test_yaml_snippet_parses_and_is_keyed_by_doi(self):
        body = self.full_body()
        blocks = re.findall(r"```yaml\n(.*?)```", body, flags=re.S)
        self.assertEqual(len(blocks), 1)
        data = yaml.safe_load(blocks[0])
        # Manual entries are edited in place, so they are not in the snippet.
        self.assertEqual(
            sorted(data["hide"]),
            ["10.1101/2026.09.30.26300001", "10.1186/s12916-024-03655-x"],
        )
        # Contribution notes are offered only for middle-author papers.
        self.assertEqual(list(data["overrides"]), ["10.1101/2026.09.30.26300001"])
        self.assertIn("contribution", data["overrides"]["10.1101/2026.09.30.26300001"])

    def test_no_overrides_block_when_all_first_or_senior(self):
        report = {"new_works": [{"id": "10.1000/a", "title": "A", "sources": ["orcid"]}]}
        pubs = {"works": [{"id": "10.1000/a", "doi": "10.1000/a", "role": "first", "year": 2026}]}
        body = ci_issue.build_body(report, pubs)
        block = re.findall(r"```yaml\n(.*?)```", body, flags=re.S)[0]
        self.assertEqual(yaml.safe_load(block), {"hide": ["10.1000/a"]})
        self.assertNotIn("overrides", body)
        self.assertIn("found 1 new publication.", body)
        self.assertIn("It is already live", body)

    def test_existing_contribution_not_offered_again(self):
        report = {"new_works": [{"id": "10.1000/b", "title": "B", "sources": ["orcid"]}]}
        pubs = {"works": [{"id": "10.1000/b", "doi": "10.1000/b", "role": "coauthor",
                           "contribution": "Ran the analysis."}]}
        self.assertNotIn("overrides", ci_issue.build_body(report, pubs))

    def test_only_manual_entries_skip_snippet(self):
        report = {"new_works": [{"id": "manual:x", "title": "X", "sources": ["manual"]}]}
        body = ci_issue.build_body(report, None)
        self.assertIn("**Nothing.**", body)
        self.assertNotIn("```yaml", body)

    def test_works_without_publications_file(self):
        self.write(self.report_path, REPORT)
        proc = self.run_script()  # publications.json does not exist
        self.assertEqual(proc.returncode, 0)
        self.assertIn("https://doi.org/10.1101/2026.09.30.26300001", proc.stdout)

    def test_doi_urls_are_normalized(self):
        report = {"new_works": [{"id": "x", "doi": "https://doi.org/10.1000/ABC.1",
                                 "title": "T", "sources": ["openalex"]}]}
        body = ci_issue.build_body(report, None)
        self.assertIn("(https://doi.org/10.1000/abc.1)", body)

    def test_long_lists_are_capped(self):
        works = [{"id": "10.1000/%d" % i, "title": "Paper %d" % i, "sources": ["orcid"]}
                 for i in range(ci_issue.MAX_LISTED + 5)]
        body = ci_issue.build_body({"new_works": works}, None)
        self.assertIn("and 5 more", body)
        self.assertLess(len(body), 65536)

    def test_house_style(self):
        body = self.full_body()
        em_dash = chr(0x2014)
        self.assertNotIn(em_dash, body)
        with open(SCRIPT, encoding="utf-8") as fh:
            self.assertNotIn(em_dash, fh.read())


class HealthIssueTest(unittest.TestCase):
    """The "Site data is going stale" issue (--health, --health-state)."""
    setUp = CiIssueTest.setUp
    tearDown = CiIssueTest.tearDown
    write = CiIssueTest.write
    run_script = CiIssueTest.run_script

    def report(self, **sources):
        base = {"orcid": {"ok": True, "used_cache": False, "fetched_at": "2026-10-12T14:00:10Z"},
                "openalex": {"ok": True, "used_cache": False, "fetched_at": "2026-10-12T14:01:00Z"}}
        base.update(sources)
        return dict(REPORT, sources=base, sanity_guard={"tripped": False, "previous": 40, "current": 40})

    def health(self, report):
        self.write(self.report_path, report)
        body = self.run_script("--health")
        state = self.run_script("--health-state")
        self.assertEqual((body.returncode, state.returncode), (0, 0))
        return body.stdout, state.stdout.strip()

    def test_healthy_run_prints_nothing(self):
        self.assertEqual(self.health(self.report()), ("", "ok"))

    def test_short_outage_is_not_reported_yet(self):
        failing = {"ok": False, "used_cache": True, "error": "HTTP 503",
                   "fetched_at": "2026-10-05T14:01:00Z"}  # 7 days old
        self.assertEqual(self.health(self.report(openalex=failing)), ("", "degraded"))

    def test_source_failing_for_two_weeks_is_reported(self):
        failing = {"ok": False, "used_cache": True, "error": "HttpError: HTTP 429 *rate*",
                   "fetched_at": "2026-09-21T14:01:00Z"}
        body, state = self.health(self.report(openalex=failing))
        self.assertEqual(state, "stale")
        self.assertIn("**OpenAlex**: last good copy from 2026-09-21 (21 days old).", body)
        self.assertIn("HTTP 429 \\*rate\\*", body)
        self.assertIn("closes itself", body)
        self.assertNotIn("ORCID", body.split("### What you can do")[0])
        self.assertNotIn(chr(0x2014), body)

    def test_source_never_fetched_is_reported(self):
        body, state = self.health(self.report(github={"ok": False, "used_cache": False,
                                                      "error": "no raw data"}))
        self.assertEqual(state, "stale")
        self.assertIn("no successful fetch on record", body)

    def test_sanity_guard_is_reported(self):
        report = self.report()
        report["sanity_guard"] = {"tripped": True, "previous": 40, "current": None}
        report["warnings"] = ["sanity guard: visible works fell from 40 to 20 (more than 15%); "
                              "kept the previous data/publications.json"]
        body, state = self.health(report)
        self.assertEqual(state, "stale")
        self.assertIn("Publication list kept from the previous run", body)
        self.assertIn("fell from 40 to 20", body)

    def test_partial_error_keeps_state_degraded(self):
        # Crossref answered but reported a partial failure: not "ok", so an
        # open site-health issue is not closed on a run like this.
        partial = {"ok": True, "used_cache": False, "error": "GET https://api.crossref.org/works: HTTP 429",
                   "fetched_at": "2026-10-12T14:01:00Z"}
        self.assertEqual(self.health(self.report(crossref=partial)), ("", "degraded"))

    def test_api_key_is_scrubbed_from_the_body(self):
        failing = {"ok": False, "used_cache": True, "fetched_at": "2026-09-21T14:01:00Z",
                   "error": "HttpError: GET https://api.openalex.org/authors/A1: Max retries "
                            "exceeded with url: /authors/A1?api_key=SECRETKEY_TEST123 (x)"}
        body, state = self.health(self.report(openalex=failing))
        self.assertEqual(state, "stale")
        self.assertNotIn("SECRETKEY", body)
        self.assertIn("api\\_key=REDACTED", body)

    def test_guard_body_explains_how_it_ends(self):
        report = self.report()
        report["sanity_guard"] = {"tripped": True, "previous": 40, "current": None}
        body, _ = self.health(report)
        self.assertIn("exactly the same shorter list", body)
        self.assertIn("accept_count_drop:", body)

    def test_missing_or_malformed_report(self):
        self.assertEqual(self.run_script("--health").stdout, "")
        self.assertEqual(self.run_script("--health-state").stdout.strip(), "degraded")
        self.write(self.report_path, "{not json")
        self.assertEqual(self.run_script("--health").stdout, "")


if __name__ == "__main__":
    unittest.main()
