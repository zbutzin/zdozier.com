"""Offline tests for scripts/merge.py. All fixtures are inline; no network."""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402
import merge  # noqa: E402
from sources.reporter import core_project  # noqa: E402

ORCID = "0000-0001-6419-0008"
TODAY = "2026-10-09"
NOW = "2026-10-09T20:00:00Z"
SITE = {"identity": {"orcid": ORCID,
                     "me": {"family_names": ["Butzin-Dozier", "Butzin Dozier", "Dozier"],
                            "given_initial": "Z"},
                     "nih_reporter": {"core_projects": ["K01AI182501"]}}}
TEAM = [("Yunwen", "Ji"), ("Sarang", "Deshpande"), ("Eric", "Hurwitz"), ("Alan E.", "Hubbard")]


def content(pubs=None, funding=None):
    return {"site": SITE, "ident": common.identity_config(SITE),
            "publications": pubs or {}, "funding": funding or {}}


def au(given, family, orcid=None):
    a = {"given": given, "family": family}
    if orcid:
        a["ORCID"] = "https://orcid.org/" + orcid
    return a


def me_first(team=TEAM):
    return [au("Zachary", "Butzin-Dozier", ORCID)] + [au(g, f) for g, f in team]


def cr(doi, title, authors, date, venue="Journal of Tests", preprint=False, server="medRxiv",
       relation=None, ctype=None):
    msg = {"DOI": doi, "title": [title], "author": authors}
    if preprint:
        msg.update({"type": ctype or "posted-content", "subtype": "preprint",
                    "posted": {"date-parts": [date]}, "institution": [{"name": server}]})
    else:
        msg.update({"type": ctype or "journal-article", "container-title": [venue],
                    "published": {"date-parts": [date]}, "volume": "1", "page": "10"})
    if relation:
        msg["relation"] = relation
    return msg


def raw_from(msgs, openalex_works=None, orcid_works=None, epmc=None, reporter=None):
    """Discovery through Crossref's ORCID filter; metadata from the same records."""
    return {
        "orcid": {"works": orcid_works or [], "fundings": []},
        "openalex": {"author": {"id": "A1", "cited_by_count": 10,
                                "summary_stats": {"h_index": 2, "i10_index": 0},
                                "counts_by_year": []},
                     "works": openalex_works or []},
        "crossref": {"orcid_filter": {"dois": [m["DOI"] for m in msgs]},
                     "works": {m["DOI"]: {"status": 200, "fetched_at": NOW, "message": m}
                               for m in msgs}},
        "europepmc": {"results": epmc or []},
        "reporter": reporter,
        "github": None,
    }


def run(raw, pubs=None, prev=None, funding=None, today=TODAY):
    return merge.run(content(pubs, funding), raw, prev or {"publications": None, "grants": None},
                     today, NOW)


def works(res):
    return res["publications"]["works"]


def by_id(res, wid):
    for w in works(res):
        if w["id"] == wid:
            return w
    raise AssertionError("no work %s in %s" % (wid, [w["id"] for w in works(res)]))


# -------------------------------------------------------------------------

class TestJmirLinking(unittest.TestCase):
    def test_jmir_preprint_attaches_to_article(self):
        orcid_works = [
            {"put_code": 1, "group": 0, "type": "journal-article",
             "title": "Predicting Long COVID Using Super Learner: Cohort Study",
             "dois": ["10.2196/53322"], "date": "2024-05-16",
             "journal_title": "JMIR Public Health and Surveillance", "contributors": []},
            {"put_code": 2, "group": 1, "type": "preprint",
             "title": "Predicting Long COVID Using Super Learner: Cohort Study (Preprint)",
             "dois": ["10.2196/preprints.53322"], "date": "2023-10-01", "contributors": []},
        ]
        res = run(raw_from([], orcid_works=orcid_works))
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual(w["id"], "10.2196/53322")
        self.assertEqual(w["status"], "published")
        self.assertEqual([(v["doi"], v["server"]) for v in w["versions"]],
                         [("10.2196/preprints.53322", "JMIR Preprints")])
        self.assertIn("jmir", [l["rule"] for l in res["links"]])


class TestVersionLinking(unittest.TestCase):
    def test_fuzzy_title_links_medrxiv_to_journal(self):
        pub = cr("10.1016/j.psyneuen.2024.107023",
                 "Stress biomarkers and child development in young children in Bangladesh",
                 me_first(), [2024, 3, 11], venue="Psychoneuroendocrinology")
        pre = cr("10.1101/2023.09.12.23295429",
                 "Stress Biomarkers and Child Development in Young Children in Bangladesh.",
                 me_first(), [2023, 9, 14], preprint=True)
        res = run(raw_from([pub, pre]))
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual(w["id"], "10.1016/j.psyneuen.2024.107023")
        self.assertEqual(w["type"], "article")
        self.assertEqual(w["versions"][0]["server"], "medRxiv")
        self.assertEqual(res["links"][0]["rule"], "fuzzy-title")

    def test_reworded_preprint_title_links_on_shared_authors(self):
        pub = cr("10.1186/s12916-024-03655-x",
                 "SSRI use during acute COVID-19 and risk of Long COVID among patients with depression",
                 me_first(), [2024, 10, 8], venue="BMC Medicine")
        pre = cr("10.1101/2024.02.05.24302352",
                 "SSRI Use During Acute COVID-19 Infection Associated with Lower Risk of Long COVID "
                 "Among Patients with Depression", me_first(), [2024, 2, 6], preprint=True)
        self.assertLess(common.title_ratio(pub["title"][0], pre["title"][0]), merge.FUZZY_RATIO)
        res = run(raw_from([pub, pre]))
        self.assertEqual([w["id"] for w in works(res)], ["10.1186/s12916-024-03655-x"])
        self.assertEqual(res["links"][0]["rule"], "fuzzy-authors")

    def test_later_preprint_of_a_new_study_is_not_linked(self):
        pub = cr("10.1186/s12916-024-03655-x",
                 "SSRI use during acute COVID-19 and risk of Long COVID among patients with depression",
                 me_first(), [2024, 10, 8], venue="BMC Medicine")
        later = cr("10.64898/2026.07.06.26357401",
                   "SSRI prescription during acute COVID-19 and risk of Long COVID symptoms and "
                   "conditions among patients with depression", me_first(), [2026, 7, 9],
                   preprint=True)
        res = run(raw_from([pub, later]))
        self.assertEqual(len(works(res)), 2)
        self.assertEqual(by_id(res, "10.64898/2026.07.06.26357401")["type"], "preprint")

    def test_two_preprints_on_one_server_never_merge(self):
        a = cr("10.64898/2026.07.06.26357398",
               "Metformin and severe post-COVID-19 outcomes among individuals with diabetes",
               me_first(), [2026, 7, 9], preprint=True)
        b = cr("10.64898/2026.07.03.26357246",
               "Metformin and severe post-COVID-19 outcomes among individuals with diabetes mellitus",
               me_first(), [2026, 7, 6], preprint=True)
        self.assertGreater(common.title_ratio(a["title"][0], b["title"][0]), 0.9)
        res = run(raw_from([a, b]))
        self.assertEqual(len(works(res)), 2)

    def test_two_published_dois_never_merge(self):
        a = cr("10.1000/a", "Identical title for two papers", me_first(), [2024, 1, 1])
        b = cr("10.1000/b", "Identical title for two papers", me_first(), [2024, 6, 1])
        self.assertEqual(len(works(run(raw_from([a, b])))), 2)

    def test_non_published_versions_collapse_preferring_medrxiv(self):
        title = ("Effects of drinking water, sanitation, handwashing, and nutritional "
                 "interventions on immune status in young children")
        med = cr("10.1101/2021.11.10.21266206", title, me_first(), [2021, 11, 14], preprint=True)
        ssrn = cr("10.2139/ssrn.3918273", title.title(), me_first(), [2021],
                  ctype="journal-article", venue="SSRN Electronic Journal")
        abstract = {"put_code": 9, "group": 5, "type": "conference-paper", "title": title.upper(),
                    "dois": [], "date": "2021-11-01", "journal_title": "Am J Trop Med Hyg",
                    "contributors": [{"name": "Butzin-Dozier, Zachary"}, {"name": "Ji, Yunwen"},
                                     {"name": "Hurwitz, Eric"}]}
        res = run(raw_from([med, ssrn], orcid_works=[abstract]))
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual(w["id"], "10.1101/2021.11.10.21266206")
        self.assertEqual(w["status"], "preprint")
        self.assertEqual(w["venue"], "medRxiv")
        self.assertEqual([v["server"] for v in w["versions"]], ["SSRN"])
        self.assertEqual(w["alt_ids"], ["orcid:9"])

    def test_crossref_relation_and_repository_copy(self):
        pub = cr("10.1371/journal.pntd.0012881", "Treatment heterogeneity of WASH interventions",
                 me_first(), [2025, 2, 18], venue="PLOS Neglected Tropical Diseases",
                 relation={"has-preprint": [{"id-type": "doi", "id": "10.1101/2024.03.21.24304684"}]})
        pre = cr("10.1101/2024.03.21.24304684", "A quite different preprint title here",
                 me_first(), [2024, 3, 22], preprint=True)
        oa_copy = {"id": "W9", "doi": "10.17615/58j5-x854", "type": "article",
                   "title": "Treatment heterogeneity of WASH interventions",
                   "publication_date": "2025-03-01",
                   "authorships": [{"raw_author_name": "Butzin-Dozier, Zachary"},
                                   {"raw_author_name": "Yunwen Ji"},
                                   {"raw_author_name": "Eric Hurwitz"}],
                   "primary_location": {"source": "Carolina Digital Repository"}}
        res = run(raw_from([pub, pre], openalex_works=[oa_copy]))
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual(w["id"], "10.1371/journal.pntd.0012881")
        self.assertEqual([v["doi"] for v in w["versions"]], ["10.1101/2024.03.21.24304684"])
        self.assertEqual(w["alt_ids"], ["10.17615/58j5-x854"])

    def test_same_as_links_explicitly(self):
        a = cr("10.1016/s2352-3018(25)00302-9", "Racial, ethnic, and regional disparities in HIV "
               "testing: a nationwide study", me_first(), [2026, 2, 13], venue="The Lancet HIV")
        b = cr("10.2139/ssrn.5273921", "Something else entirely", [au("Yang", "Haishan")],
               [2025], preprint=True, server="SSRN")
        res = run(raw_from([a, b]), pubs={"same_as": [["10.1016/S2352-3018(25)00302-9",
                                                       "10.2139/ssrn.5273921"]]})
        self.assertEqual(len(works(res)), 1)
        self.assertEqual(works(res)[0]["versions"][0]["server"], "SSRN")


class TestMeAndRole(unittest.TestCase):
    ARCH = ("10.1001/archpediatrics.2010.54", "Cortisol production patterns in young children")

    def test_mary_dozier_is_never_me(self):
        msg = cr(self.ARCH[0], self.ARCH[1],
                 [au("Kristin", "Bernard"), au("Zachary", "Butzin-Dozier"),
                  au("Joseph", "Rittenhouse"), au("Mary", "Dozier")], [2010, 5, 1])
        w = works(run(raw_from([msg])))[0]
        self.assertEqual(w["my_position"], 2)
        self.assertEqual(w["role"], "coauthor")
        self.assertEqual([a["name"] for a in w["authors"] if a["is_me"]], ["Zachary Butzin-Dozier"])
        self.assertFalse(w["authors"][3]["is_me"])

    def test_mary_dozier_with_a_misattributed_orcid(self):
        msg = cr(self.ARCH[0], self.ARCH[1],
                 [au("Kristin", "Bernard"), au("Zachary", "Butzin-Dozier"),
                  au("Joseph", "Rittenhouse"), au("Mary", "Dozier", ORCID)], [2010, 5, 1])
        res = run(raw_from([msg]))
        w = works(res)[0]
        self.assertEqual(w["my_position"], 2)
        self.assertFalse(w["authors"][3]["is_me"])
        self.assertTrue(any("fails the name rule" in x for x in res["warnings"]))

    def test_mary_dozier_alone_is_not_him(self):
        msg = cr(self.ARCH[0], self.ARCH[1], [au("Kristin", "Bernard"), au("M.", "Dozier")],
                 [2010, 5, 1])
        res = run(raw_from([msg]))
        self.assertIsNone(works(res)[0]["my_position"])
        self.assertTrue(any("could not find him" in x for x in res["warnings"]))

    def test_openalex_orcid_match(self):
        oa = {"id": "W1", "doi": "10.52202/085713-4650", "type": "conference-paper",
              "title": "AI progress should be measured by capability per resource",
              "publication_date": "2025-12-01",
              "authorships": [{"raw_author_name": "David McCoy"}, {"raw_author_name": "Yulun Wu"},
                              {"raw_author_name": "Zachary Butzin", "orcid": ORCID}],
              "primary_location": {"source": "Advances in Neural Information Processing Systems"},
              "cited_by_count": 3}
        res = run(raw_from([], openalex_works=[oa]))
        w = works(res)[0]
        self.assertEqual(w["my_position"], 3)
        self.assertEqual(w["role"], "senior")
        self.assertEqual(w["type"], "conference")
        self.assertEqual(w["sources"], ["openalex"])
        self.assertEqual(w["cited_by"], 3)

    def test_role_rules(self):
        group = {"name": "N3C Consortium"}
        m1 = cr("10.1000/a", "Paper A", [au("A", "One"), au("B", "Two"), au("Zachary", "Butzin-Dozier"),
                                      group], [2024, 1, 1])
        m2 = cr("10.1000/b", "Paper B", [au("A", "One"), au("Zachary", "Butzin-Dozier")], [2024, 1, 2])
        m3 = cr("10.1000/c", "Paper C", me_first(), [2024, 1, 3])
        res = run(raw_from([m1, m2, m3]))
        self.assertEqual(by_id(res, "10.1000/a")["role"], "senior")
        self.assertTrue(by_id(res, "10.1000/a")["authors"][3]["is_group"])
        self.assertEqual(by_id(res, "10.1000/b")["role"], "coauthor")
        self.assertEqual(by_id(res, "10.1000/c")["role"], "first")

    def test_europepmc_name_hits_must_pass_the_rule(self):
        hit = {"id": "1", "source": "MED", "pmid": "1", "doi": "10.1000/epmc",
               "title": "A Dozier paper", "authors": [{"lastName": "Dozier", "firstName": "Mary",
                                                       "initials": "M"}], "pubYear": "2012"}
        ok = {"id": "2", "source": "MED", "pmid": "2", "doi": "10.1000/epmc2",
              "title": "His paper", "authors": [{"lastName": "Butzin-Dozier", "firstName": "Zachary",
                                                  "initials": "Z"}], "pubYear": "2025",
              "firstPublicationDate": "2025-01-02", "journal": "J"}
        res = run(raw_from([], epmc=[hit, ok]))
        self.assertEqual([w["id"] for w in works(res)], ["10.1000/epmc2"])
        self.assertTrue(any("found only by name search" in x for x in res["warnings"]))


class TestOverridesAndHide(unittest.TestCase):
    PRE = "10.64898/2026.07.06.26357398"
    PUB = "10.1111/dom.99999"

    def pubs(self, extra=None):
        p = {"overrides": {self.PRE: {"status": "accepted",
                                      "status_note": "Diabetes, Obesity and Metabolism",
                                      "contribution": "Led the analysis.", "topics": ["covid"]}},
             "topics": [{"id": "covid", "label": "COVID"}, {"id": "causal-ml", "label": "CML"}],
             "topic_rules": {"causal-ml": ["metformin"]}}
        p.update(extra or {})
        return p

    def test_accepted_preprint(self):
        pre = cr(self.PRE, "Metformin and severe post-COVID-19 outcomes", me_first(), [2026, 7, 9],
                 preprint=True)
        w = works(run(raw_from([pre]), pubs=self.pubs()))[0]
        self.assertEqual((w["type"], w["status"], w["status_note"]),
                         ("preprint", "accepted", "Diabetes, Obesity and Metabolism"))
        self.assertEqual(w["topics"], ["covid", "causal-ml"])

    def test_published_version_wins_and_note_is_dropped(self):
        pre = cr(self.PRE, "Metformin and severe post-COVID-19 outcomes", me_first(), [2026, 7, 9],
                 preprint=True)
        pub = cr(self.PUB, "Metformin and severe post-COVID-19 outcomes", me_first(), [2026, 11, 1],
                 venue="Diabetes, Obesity and Metabolism")
        res = run(raw_from([pre, pub]), pubs=self.pubs(), today="2026-11-15")
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual((w["id"], w["status"], w["status_note"]), (self.PUB, "published", None))
        self.assertEqual(w["contribution"], "Led the analysis.")
        self.assertIn("covid", w["topics"])

    def test_hide_primary_and_version(self):
        pre = cr(self.PRE, "Metformin and severe post-COVID-19 outcomes", me_first(), [2026, 7, 9],
                 preprint=True)
        pub = cr(self.PUB, "Metformin and severe post-COVID-19 outcomes", me_first(), [2026, 11, 1])
        other = cr("10.1000/other", "Another paper", me_first(), [2025, 1, 1])
        res = run(raw_from([pre, pub, other]), pubs={"hide": [self.PRE.upper(), "10.1000/other"]})
        self.assertEqual([w["id"] for w in works(res)], [self.PUB])
        self.assertEqual(works(res)[0]["versions"], [])
        self.assertEqual(res["hidden_count"], 1)

    def test_bad_override_values_are_ignored(self):
        m = cr("10.1000/x", "Paper", me_first(), [2025, 1, 1])
        res = run(raw_from([m]), pubs={"overrides": {"10.1000/x": {"type": "journal", "role": "lead"}}})
        self.assertEqual(works(res)[0]["type"], "article")
        self.assertEqual(works(res)[0]["role"], "first")
        self.assertEqual(len([x for x in res["warnings"] if "ignored" in x]), 2)


class TestManualAndMissingContent(unittest.TestCase):
    def test_missing_content_and_raw(self):
        c = merge.load_content(os.path.join(ROOT, "does-not-exist"))
        self.assertEqual((c["publications"], c["funding"]), ({}, {}))
        res = merge.run(c, {k: None for k in ("orcid", "openalex", "crossref", "europepmc",
                                              "reporter", "github")},
                        {"publications": None, "grants": None}, TODAY, NOW)
        self.assertEqual(works(res), [])
        self.assertEqual(res["grants"]["grants"], [])
        self.assertEqual(res["software"]["repos"], [])
        self.assertEqual(res["metrics"]["works_count"], 0)

    def test_manual_entry(self):
        pubs = {"manual": [{"id": "manual:singh", "title": "Higher risk of Long COVID",
                            "authors": ["Singh, N.", "Butzin-Dozier, Z.", "Patel, R. C.",
                                        "N3C Consortium"],
                            "year": 2026, "venue": "Arthritis & Rheumatology", "type": "article",
                            "status": "accepted", "status_note": "In press",
                            "contribution": "Methods input."}]}
        w = works(run(raw_from([]), pubs=pubs))[0]
        self.assertEqual(w["id"], "manual:singh")
        self.assertEqual(w["my_position"], 2)
        self.assertEqual(w["role"], "coauthor")
        self.assertTrue(w["authors"][3]["is_group"])
        self.assertEqual(w["sources"], ["manual"])
        self.assertEqual(w["date"], "2026")

    def test_manual_entry_absorbed_once_published(self):
        pubs = {"manual": [{"id": "manual:singh",
                            "title": "Higher risk of Long COVID among patients with autoimmune "
                                     "rheumatic diseases",
                            "authors": ["Singh, N.", "Anzalone, A. J.", "Butzin-Dozier, Z."],
                            "year": 2026, "status": "accepted", "contribution": "Methods input."}]}
        pub = cr("10.1002/art.12345", "Higher risk of Long COVID among patients with autoimmune "
                 "rheumatic diseases", [au("Neha", "Singh"), au("A. Jerrod", "Anzalone"),
                                        au("Zachary", "Butzin-Dozier")], [2026, 12, 1])
        res = run(raw_from([pub]), pubs=pubs, today="2026-12-05")
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual((w["id"], w["status"], w["contribution"]),
                         ("10.1002/art.12345", "published", "Methods input."))
        self.assertTrue(any("manual entry manual:singh matches" in x for x in res["warnings"]))


class TestSanityGuard(unittest.TestCase):
    def prev(self, n):
        return [{"id": "10.1000/%d" % i, "doi": "10.1000/%d" % i, "versions": [], "alt_ids": [],
                 "first_seen": "2026-01-01", "title": "t%d" % i} for i in range(n)]

    def test_threshold(self):
        prev = self.prev(20)
        self.assertTrue(merge.sanity_check(16, prev, set()))     # 20% drop
        self.assertFalse(merge.sanity_check(17, prev, set()))    # exactly 15%
        self.assertFalse(merge.sanity_check(16, prev, {"10.1000/0", "10.1000/1", "10.1000/2", "10.1000/3"}))
        self.assertFalse(merge.sanity_check(0, None, set()))
        self.assertFalse(merge.sanity_check(0, [], set()))

    def test_guard_keeps_previous_file(self):
        prev = {"publications": {"generated": "x", "works": self.prev(20)}, "grants": None}
        res = run(raw_from([cr("10.1000/new", "Only one", me_first(), [2026, 1, 1])]), prev=prev)
        self.assertTrue(res["guard"])
        self.assertEqual(len(works(res)), 20)
        self.assertTrue(res["warnings"][0].startswith("sanity guard"))
        self.assertEqual(res["new_works"], [])


class TestFirstSeen(unittest.TestCase):
    def test_carry_forward_and_diff(self):
        prev_works = [
            {"id": "10.1101/2023.09.12.23295429", "doi": "10.1101/2023.09.12.23295429",
             "status": "preprint", "versions": [], "alt_ids": [], "first_seen": "2026-01-05",
             "title": "Stress biomarkers"},
            {"id": "10.1000/gone", "doi": "10.1000/gone", "status": "published", "versions": [],
             "alt_ids": [], "first_seen": "2025-06-01", "title": "Gone"},
        ]
        pub = cr("10.1016/j.psyneuen.2024.107023", "Stress biomarkers and child development",
                 me_first(), [2024, 3, 11])
        pre = cr("10.1101/2023.09.12.23295429", "Stress biomarkers and child development",
                 me_first(), [2023, 9, 14], preprint=True)
        new = cr("10.1000/brand-new", "A brand new paper", me_first(), [2026, 9, 1])
        res = run(raw_from([pub, pre, new]),
                  prev={"publications": {"works": prev_works}, "grants": None})
        self.assertEqual(by_id(res, "10.1016/j.psyneuen.2024.107023")["first_seen"], "2026-01-05")
        self.assertEqual(by_id(res, "10.1000/brand-new")["first_seen"], TODAY)
        self.assertEqual([w["id"] for w in res["new_works"]], ["10.1000/brand-new"])
        self.assertEqual([w["id"] for w in res["removed_works"]], ["10.1000/gone"])
        self.assertEqual(res["promoted_works"][0]["previous_id"], "10.1101/2023.09.12.23295429")

    def test_first_run_reports_no_new_works(self):
        res = run(raw_from([cr("10.1000/a", "A", me_first(), [2026, 1, 1])]))
        self.assertEqual(res["new_works"], [])
        self.assertTrue(res["first_run"])
        self.assertEqual(works(res)[0]["first_seen"], TODAY)


class TestGrantsAndMetrics(unittest.TestCase):
    def reporter(self):
        pi = [{"full_name": "Zachary  Butzin-Dozier", "first_name": "Zachary",
               "last_name": "Butzin-Dozier", "profile_id": 78196486, "is_contact_pi": True}]

        def row(appl, num, fy, org, start, end, budget, amt, title):
            return {"appl_id": appl, "project_num": num, "core_project_num": "K01AI182501",
                    "fiscal_year": fy, "project_title": title,
                    "project_start_date": start, "project_end_date": end,
                    "budget_start": budget, "award_amount": amt, "activity_code": "K01",
                    "agency_ic_admin": {"abbreviation": "NIAID", "name": "NIAID"},
                    "organization": {"org_name": org}, "principal_investigators": pi}
        old = "Applying a Targeted Machine Learning Approach."
        new = "Applying a Machine Learning Approach."
        return {"pi_profile_ids": [78196486], "projects": [
            row(10865395, "1K01AI182501-01", 2024, "UNIVERSITY OF CALIFORNIA BERKELEY",
                "2024-09-04T00:00:00", "2029-08-31T00:00:00", "2024-09-04", 134923, old),
            row(11182551, "5K01AI182501-02", 2025, "UNIVERSITY OF CALIFORNIA BERKELEY",
                "2024-09-04T00:00:00", "2026-05-31T00:00:00", "2025-09-01", 55765, old),
            row(11551077, "7K01AI182501-03", 2025, "STANFORD UNIVERSITY",
                "2024-09-04T00:00:00", "2029-08-31T00:00:00", "2026-06-01", 80004, new),
            row(11406206, "5K01AI182501-04", 2026, "STANFORD UNIVERSITY",
                "2024-09-04T00:00:00", "2029-08-31T00:00:00", "2026-09-01", 135769, new),
        ]}

    def test_k01_shape(self):
        res = run(raw_from([], reporter=self.reporter()),
                  funding={"enrich": {"K01AI182501": {"description": "Plain description."}}})
        g = res["grants"]["grants"][0]
        self.assertEqual(g["id"], "K01AI182501")
        self.assertEqual(g["title"], "Applying a Machine Learning Approach")
        self.assertEqual(g["institutions"], ["University of California, Berkeley",
                                             "Stanford University"])
        self.assertEqual(g["url"], "https://reporter.nih.gov/project-details/11406206")
        self.assertEqual((g["start"], g["end"], g["status"]), ("2024-09", "2029-08", "active"))
        self.assertEqual((g["role"], g["mechanism"], g["agency"]), ("PI", "K01", "NIAID"))
        self.assertEqual(g["pis"], ["Zachary Butzin-Dozier"])
        self.assertEqual(g["fiscal_years"], [2024, 2025, 2026])
        self.assertIsNone(g["amount_total"])
        self.assertEqual(g["description"], "Plain description.")

    def test_amounts_and_completed_status(self):
        res = run(raw_from([], reporter=self.reporter()), funding={"show_amounts": True},
                  today="2030-01-01")
        g = res["grants"]["grants"][0]
        self.assertEqual(g["amount_total"], 406461)
        self.assertEqual(g["status"], "completed")

    def test_manual_grant_and_hide(self):
        funding = {"manual": [{"id": "gates", "title": "Gates subcontract", "funder": "Gates",
                               "start": "2025-06", "end": "2026-06"},
                              {"id": "open", "title": "Ongoing", "start": "2026-01", "end": None}],
                   "hide": ["K01AI182501"]}
        grants = run(raw_from([], reporter=self.reporter()), funding=funding)["grants"]["grants"]
        self.assertEqual([(g["id"], g["status"], g["source"]) for g in grants],
                         [("open", "active", "manual"), ("gates", "completed", "manual")])

    def test_core_project(self):
        self.assertEqual(core_project("1K01AI182501-01"), "K01AI182501")
        self.assertEqual(core_project("5K01AI182501-04"), "K01AI182501")
        self.assertEqual(core_project("1DP2AI123456-01A1"), "DP2AI123456")

    def test_metrics(self):
        oa = [{"id": "W1", "doi": "10.1000/a", "type": "article", "title": "A",
               "publication_date": "2024-01-01", "cited_by_count": 12,
               "counts_by_year": [{"year": 2025, "cited_by_count": 7},
                                  {"year": 2026, "cited_by_count": 5}],
               "authorships": [{"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}]},
              {"id": "W2", "doi": "10.1000/b", "type": "article", "title": "B",
               "publication_date": "2025-01-01", "cited_by_count": 4,
               "counts_by_year": [{"year": 2026, "cited_by_count": 4}],
               "authorships": [{"raw_author_name": "Someone Else"},
                               {"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}]}]
        res = run(raw_from([], openalex_works=oa))
        m = res["metrics"]
        self.assertEqual((m["works_count"], m["cited_by_count"], m["h_index"]), (2, 10, 2))
        row = {r["year"]: r for r in m["counts_by_year"]}
        self.assertEqual(row[2026]["cited_by_count"], 9)
        self.assertEqual(row[2024]["works_count"], 1)
        roles = {r["year"]: r for r in m["by_year_role"]}
        # Second of two authors is a coauthor: "senior" needs three or more people.
        self.assertEqual((roles[2024]["first_or_senior"], roles[2025]["coauthor"]), (1, 1))
        # Hiding a cited work adjusts the OpenAlex totals.
        res = run(raw_from([], openalex_works=copy.deepcopy(oa)), pubs={"hide": ["10.1000/a"]})
        m = res["metrics"]
        self.assertEqual((m["works_count"], m["cited_by_count"], m["h_index"]), (1, 0, 1))
        self.assertTrue(m["adjusted_for_hidden"])


class TestFieldCleanup(unittest.TestCase):
    def test_dates(self):
        # Year-only "published" must not beat the exact online date.
        self.assertEqual(merge._earliest(["2022", "2022-05-19", "2022-12-21"]), "2022-05-19")
        self.assertEqual(merge._earliest(["2025-12-26", "2026-01"]), "2025-12-26")
        self.assertIsNone(merge._earliest([None, ""]))

    def test_print_month_refined_by_online_date_but_not_placeholder(self):
        m = cr("10.1016/j.psyneuen.2024.107023", "Stress biomarkers", me_first(), [2024, 6])
        oa = {"id": "W1", "doi": "10.1016/j.psyneuen.2024.107023", "type": "article",
              "title": "Stress biomarkers", "publication_date": "2024-03-11",
              "authorships": [{"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}]}
        self.assertEqual(works(run(raw_from([m], openalex_works=[oa])))[0]["date"], "2024-03-11")
        m = cr("10.52202/085713-4650", "NeurIPS paper", me_first(), [2025])
        oa = dict(oa, doi="10.52202/085713-4650", title="NeurIPS paper",
                  publication_date="2025-01-01")
        self.assertEqual(works(run(raw_from([m], openalex_works=[oa])))[0]["date"], "2025")

    def test_pages(self):
        self.assertEqual(merge.expand_pages("438-43"), "438-443")
        self.assertEqual(merge.expand_pages("e185-e193"), "e185-e193")
        self.assertEqual(merge.expand_pages("445"), "445")
        self.assertEqual(merge.expand_pages("29-54"), "29-54")

    def test_unconfirmed_crossref_subtitle_is_dropped(self):
        m = cr("10.1377/hlthaff.2025.00179", "Black Patients Underdiagnosed With Long COVID",
               me_first(), [2026, 7, 1], venue="Health Affairs")
        m["subtitle"] = ["Article examines Long COVID diagnosis"]
        oa = {"id": "W1", "doi": "10.1377/hlthaff.2025.00179", "type": "article",
              "title": "Black Patients Underdiagnosed With Long COVID",
              "publication_date": "2026-07-01",
              "authorships": [{"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}]}
        w = works(run(raw_from([m], openalex_works=[oa])))[0]
        self.assertEqual(w["title"], "Black Patients Underdiagnosed With Long COVID")
        oa["title"] = "Black Patients Underdiagnosed With Long COVID: Article examines Long COVID diagnosis"
        w = works(run(raw_from([m], openalex_works=[oa])))[0]
        self.assertTrue(w["title"].endswith(": Article examines Long COVID diagnosis"))


class TestPartialContent(unittest.TestCase):
    def test_malformed_values_do_not_crash(self):
        pubs = {"overrides": None, "topics": None, "topic_rules": {"covid": "covid", "bad": ["("]},
                "manual": [None, "just a string", {"title": None}], "same_as": "10.1000/a",
                "hide": "10.1000/zzz", "include_dois": None}
        funding = {"enrich": None, "manual": [None, {"title": "T"}], "hide": None}
        m = cr("10.1000/a", "A COVID paper", me_first(), [2026, 1, 1])
        res = run(raw_from([m]), pubs=pubs, funding=funding)
        self.assertEqual(works(res)[0]["topics"], ["covid"])
        self.assertTrue(any("bad regex" in x for x in res["warnings"]))
        self.assertEqual([g["title"] for g in res["grants"]["grants"]], ["T"])


class TestAuditRegressions(unittest.TestCase):
    """Problems found in the 2026-10-09 audit of the real data (docs/data-audit.md)."""
    PUB = "10.1016/j.ebiom.2026.106382"
    PRE = "10.1101/2025.04.22.25326224"
    TITLE = "COVID-19 vaccination timing, relative to acute COVID-19, and subsequent risk of long COVID"

    def pair(self):
        pub = cr(self.PUB, self.TITLE, me_first(), [2026, 7, 24], venue="eBioMedicine")
        pre = cr(self.PRE, self.TITLE, me_first(), [2025, 4, 23], preprint=True)
        # OpenAlex merged the preprint into the journal work and reports the
        # preprint's PubMed record for it.
        oa = {"id": "W1", "doi": self.PUB, "type": "article", "title": self.TITLE,
              "publication_date": "2026-07-24",
              "pmid": "https://pubmed.ncbi.nlm.nih.gov/40313290", "pmcid": "PMC12045423",
              "authorships": [{"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}]}
        orcid_pre = {"put_code": 5, "group": 2, "type": "other", "title": self.TITLE,
                     "dois": [self.PRE], "pmid": "40313290", "pmcid": "PMC12045423",
                     "contributors": []}
        return pub, pre, oa, orcid_pre

    def test_pmid_of_merged_preprint_is_not_used_for_the_article(self):
        pub, pre, oa, orcid_pre = self.pair()
        epmc = {"id": "42497651", "source": "MED", "pmid": "42497651", "pmcid": "PMC13445364",
                "doi": self.PUB, "title": self.TITLE, "journal": "EBioMedicine",
                "firstPublicationDate": "2026-07-24",
                "authors": [{"lastName": "Butzin-Dozier", "firstName": "Zachary"}],
                "commentCorrections": [{"type": "Update of", "id": "40313290", "source": "MED"}]}
        res = run(raw_from([pub, pre], openalex_works=[oa], orcid_works=[orcid_pre], epmc=[epmc]))
        w = by_id(res, self.PUB)
        self.assertEqual((w["pmid"], w["pmcid"]), ("42497651", "PMC13445364"))

    def test_without_europepmc_a_known_preprint_pmid_is_dropped(self):
        pub, pre, oa, orcid_pre = self.pair()
        w = by_id(run(raw_from([pub, pre], openalex_works=[oa], orcid_works=[orcid_pre])), self.PUB)
        self.assertEqual((w["pmid"], w["pmcid"]), (None, None))
        # With nothing tying the id to the preprint, OpenAlex's value is kept.
        w = by_id(run(raw_from([pub], openalex_works=[oa])), self.PUB)
        self.assertEqual((w["pmid"], w["pmcid"]), ("40313290", "PMC12045423"))

    def test_repository_copy_alone_is_not_shown(self):
        copy_ = {"id": "W9", "doi": "10.17615/new-deposit", "type": "article",
                 "title": "A paper deposited at UNC before the journal record is found",
                 "publication_date": "2026-09-01",
                 "authorships": [{"raw_author_name": "Zachary Butzin-Dozier", "orcid": ORCID}],
                 "primary_location": {"source": "Carolina Digital Repository"}}
        res = run(raw_from([], openalex_works=[copy_]))
        self.assertEqual(works(res), [])
        self.assertTrue(any("repository copy with no matching publication" in x
                            for x in res["warnings"]))
        res = run(raw_from([], openalex_works=[dict(copy_)]),
                  pubs={"include_dois": ["10.17615/new-deposit"]})
        self.assertEqual([w["id"] for w in works(res)], ["10.17615/new-deposit"])

    def test_research_square_versions_are_one_preprint(self):
        title = "Antibiotic use by backyard food animal producers in Ecuador: a qualitative study"
        v1 = cr("10.21203/rs.3.rs-455696/v1", title, me_first(), [2021, 5, 1], preprint=True,
                server="Research Square")
        v2 = cr("10.21203/rs.3.rs-455696/v2", title, me_first(), [2021, 9, 1], preprint=True,
                server="Research Square")
        res = run(raw_from([v1, v2]))
        self.assertEqual(len(works(res)), 1)
        self.assertEqual(works(res)[0]["versions"], [])
        pub = cr("10.1186/s12889-022-13073-4", title, me_first(), [2022, 4, 8],
                 venue="BMC Public Health")
        res = run(raw_from([v1, v2, pub]))
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual(w["id"], "10.1186/s12889-022-13073-4")
        self.assertEqual([v["doi"] for v in w["versions"]], ["10.21203/rs.3.rs-455696/v2"])
        self.assertEqual(w["alt_ids"], ["10.21203/rs.3.rs-455696/v1"])
        self.assertEqual(common.preprint_base("10.21203/rs.2.rs-455696/v3"), "10.21203/rs-455696")
        self.assertIsNone(common.preprint_base("10.1101/2024.10.18.24315778"))

    def test_manual_entry_wins_over_doi_less_orcid_record(self):
        title = "Strengthening causal inference of real-world data"
        orcid_item = {"put_code": 7, "group": 3, "type": "book-chapter", "title": title,
                      "dois": [], "date": "2027", "contributors": []}
        pubs = {"manual": [{"id": "manual:chapter", "title": title, "year": 2027,
                            "authors": ["Butzin-Dozier, Z.", "Anzalone, J. A."],
                            "venue": "Environmental Health Nursing", "type": "chapter",
                            "status": "accepted", "status_note": "Forthcoming"}]}
        res = run(raw_from([], orcid_works=[orcid_item]), pubs=pubs)
        self.assertEqual(len(works(res)), 1)
        w = works(res)[0]
        self.assertEqual((w["id"], w["status"], w["status_note"], w["my_position"]),
                         ("manual:chapter", "accepted", "Forthcoming", 1))
        self.assertEqual(w["sources"], ["orcid", "manual"])
        self.assertEqual(w["alt_ids"], ["orcid:7"])

    def test_doi_less_orcid_item_shown_alone_is_flagged(self):
        item = {"put_code": 8, "group": 4, "type": "conference-paper", "title": "A poster abstract",
                "dois": [], "date": "2021-11-01", "contributors": []}
        res = run(raw_from([], orcid_works=[item]))
        self.assertEqual([w["id"] for w in works(res)], ["orcid:8"])
        self.assertTrue(any("ORCID item without a DOI" in x for x in res["warnings"]))
        res = run(raw_from([], orcid_works=[dict(item)]), pubs={"hide": ["orcid:8"]})
        self.assertEqual(works(res), [])

    def test_topic_and_contribution_warnings(self):
        m = cr("10.52202/085713-4650", "AI progress should be measured by capability per resource",
               [au("David", "McCoy"), au("Yulun", "Wu"), au("Zachary", "Butzin-Dozier", ORCID)],
               [2025])
        pubs = {"topics": [{"id": "covid", "label": "COVID"}], "topic_rules": {"covid": ["covid"]},
                "overrides": {"10.52202/085713-4650": {"contribution": "Senior author."}}}
        res = run(raw_from([m]), pubs=pubs)
        self.assertTrue(any("matches no topic" in x for x in res["warnings"]))
        self.assertTrue(any("contribution note but he is senior" in x for x in res["warnings"]))
        pubs["overrides"]["10.52202/085713-4650"] = {"topics": ["covid"]}
        res = run(raw_from([copy.deepcopy(m)]), pubs=pubs)
        self.assertFalse(any("matches no topic" in x or "contribution note" in x
                             for x in res["warnings"]))

    def test_year_charts_count_public_works_only(self):
        pub = cr("10.1000/pub", "A published paper", me_first(), [2026, 3, 1])
        acc = cr("10.64898/2026.07.06.26357398", "An accepted paper with a preprint", me_first(),
                 [2026, 7, 9], preprint=True)
        pubs = {"overrides": {"10.64898/2026.07.06.26357398": {"type": "article",
                                                               "status": "accepted"}},
                "manual": [{"id": "manual:chapter", "title": "Forthcoming chapter", "year": 2027,
                            "authors": ["Butzin-Dozier, Z."], "type": "chapter",
                            "status": "accepted"},
                           {"id": "manual:review", "title": "Invited review", "year": 2026,
                            "authors": ["Butzin-Dozier, Z.", "Wong, R."], "type": "review",
                            "status": "under-review"},
                           {"id": "manual:report", "title": "A 2020 report", "year": 2020,
                            "authors": ["Auerswald, C.", "Butzin-Dozier, Z."], "type": "report",
                            "status": "published"}]}
        m = run(raw_from([pub, acc]), pubs=pubs)["metrics"]
        self.assertEqual(m["works_count"], 5)
        self.assertEqual(m["charted_works_count"], 3)
        rows = {r["year"]: r for r in m["by_year_role"]}
        self.assertEqual(sum(r["first_or_senior"] + r["coauthor"] for r in rows.values()), 3)
        self.assertEqual(max(rows), 2026)  # no bar for the 2027 chapter
        self.assertEqual((rows[2026]["first_or_senior"], rows[2020]["coauthor"]), (2, 1))
        self.assertEqual(sum(r["works_count"] for r in m["counts_by_year"]), 3)
        # A work with a DOI dated after this year is not charted either.
        nxt = cr("10.1000/next", "Dated next year", me_first(), [2027, 1, 1])
        m = run(raw_from([copy.deepcopy(pub), copy.deepcopy(acc), nxt]), pubs=pubs)["metrics"]
        self.assertEqual(m["charted_works_count"], 3)
        self.assertEqual(max(r["year"] for r in m["by_year_role"]), 2026)

    def test_published_paper_links_to_its_own_preprint_not_a_sibling(self):
        # Two medRxiv preprints from the same team with near-identical titles
        # (title ratio about 0.88); the journal version must take only its own.
        met = cr("10.64898/2026.07.06.26357398", "Metformin and Severe Post-COVID-19 Outcomes Among "
                 "Individuals with Diabetes Mellitus", me_first(), [2026, 7, 9], preprint=True)
        glp = cr("10.64898/2026.07.03.26357246", "GLP Medications and Severe Post-COVID-19 Outcomes "
                 "Among Individuals with Type 2 Diabetes Mellitus", me_first(), [2026, 7, 6],
                 preprint=True)
        pub = cr("10.1111/dom.99999", "Metformin and severe post-COVID-19 outcomes among "
                 "individuals with diabetes mellitus", me_first(), [2026, 11, 1],
                 venue="Diabetes, Obesity and Metabolism")
        self.assertGreaterEqual(common.title_ratio(met["title"][0], glp["title"][0]), 0.87)
        res = run(raw_from([met, glp, pub]), today="2026-11-15")
        self.assertEqual(sorted(w["id"] for w in works(res)),
                         ["10.1111/dom.99999", "10.64898/2026.07.03.26357246"])
        self.assertEqual([v["doi"] for v in by_id(res, "10.1111/dom.99999")["versions"]],
                         ["10.64898/2026.07.06.26357398"])


class TestConsortiumBylines(unittest.TestCase):
    """Crossref member lists versus printed bylines (eBioMedicine 2026, 2024)."""
    DOI = "10.1016/j.ebiom.2026.106382"

    def epmc(self, doi, people, groups):
        authors = [{"fullName": "%s %s" % (f, g[0]), "firstName": g, "lastName": f,
                    "initials": g[0], "collectiveName": None,
                    "orcid": ORCID if f == "Butzin-Dozier" else None} for g, f in people]
        authors += [{"fullName": None, "lastName": None, "collectiveName": n} for n in groups]
        return {"id": "1", "source": "MED", "doi": doi, "title": "COVID-19 vaccination timing",
                "authors": authors, "pubYear": "2026", "journal": "eBioMedicine"}

    def test_member_list_replaced_by_europepmc_byline(self):
        people = [("Zachary", "Butzin-Dozier")] + TEAM
        # Distinct letter-only names (name keys ignore digits).
        members = [au("Ann" + "abcdefghij"[i % 10], "Member" + "abcd"[i // 10]) for i in range(40)]
        m = cr(self.DOI, "COVID-19 vaccination timing", me_first() + members, [2026, 9, 1])
        ep = self.epmc(self.DOI, people, ["N3C Consortium"])
        w = by_id(run(raw_from([m], epmc=[ep])), self.DOI)
        self.assertEqual(w["author_count"], 6)
        self.assertEqual(w["authors"][-1]["name"], "N3C Consortium")
        self.assertTrue(w["authors"][-1]["is_group"])
        self.assertEqual((w["my_position"], w["role"]), (1, "first"))
        # authors_source pins Crossref's list (as his CV does for one OFID paper).
        pubs = {"overrides": {self.DOI: {"authors_source": "crossref"}}}
        w = by_id(run(raw_from([copy.deepcopy(m)], epmc=[copy.deepcopy(ep)]), pubs=pubs), self.DOI)
        self.assertEqual(w["author_count"], 45)
        self.assertNotIn("authors_source", w)

    def test_similar_lengths_keep_crossref(self):
        # Without a group, or with lists of similar length, Crossref still wins.
        m = cr(self.DOI, "COVID-19 vaccination timing", me_first(), [2026, 9, 1])
        ep = self.epmc(self.DOI, [("Zachary", "Butzin-Dozier"), ("Yunwen", "Ji")], [])
        w = by_id(run(raw_from([m], epmc=[ep])), self.DOI)
        self.assertEqual(w["author_count"], 5)

    def test_trailing_group_dropped_by_crossref_is_restored(self):
        people = [("Zachary", "Butzin-Dozier")] + TEAM
        m = cr(self.DOI, "Glycemic control", me_first(), [2025, 2, 1])
        ep = self.epmc(self.DOI, people, ["N3C Consortium"])
        w = by_id(run(raw_from([m], epmc=[ep])), self.DOI)
        self.assertEqual([a["name"] for a in w["authors"]][-2:], ["Alan E. Hubbard", "N3C Consortium"])
        self.assertEqual(w["authors"][-1]["is_group"], True)
        # A different set of people: no group is borrowed.
        ep2 = self.epmc(self.DOI, [("Zachary", "Butzin-Dozier"), ("Ann", "Other"), ("Bo", "Else"),
                                   ("Cy", "More"), ("Di", "Last")], ["N3C Consortium"])
        w = by_id(run(raw_from([copy.deepcopy(m)], epmc=[ep2])), self.DOI)
        self.assertFalse(any(a["is_group"] for a in w["authors"]))

    def test_group_author_override(self):
        m = cr(self.DOI, "Racial bias in Long COVID diagnosis", me_first(), [2026, 1, 5])
        name = "the National COVID Cohort Collaborative (N3C) Consortium"
        pubs = {"overrides": {self.DOI: {"group_author": name}}}
        w = by_id(run(raw_from([m]), pubs=pubs), self.DOI)
        self.assertEqual(w["authors"][-1]["name"], name)
        self.assertTrue(w["authors"][-1]["is_group"])
        self.assertEqual(w["author_count"], 6)
        self.assertEqual(w["role"], "first")
        self.assertNotIn("group_author", w)

    def test_bad_authors_source_is_ignored(self):
        m = cr(self.DOI, "A paper", me_first(), [2026, 1, 5])
        res = run(raw_from([m]), pubs={"overrides": {self.DOI: {"authors_source": "scholar"}}})
        self.assertEqual(by_id(res, self.DOI)["author_count"], 5)
        self.assertTrue(any("authors_source" in x for x in res["warnings"]))

    def test_exact_repeats_are_dropped(self):
        a = [au("Zachary", "Butzin-Dozier", ORCID), au("Yi", "Wang"), au("Yu", "Wang"),
             au("Yi", "Wang"), au("Zachary", "Butzin-Dozier"), au("Jane", "Roe")]
        w = by_id(run(raw_from([cr(self.DOI, "A paper", a, [2024, 1, 1])])), self.DOI)
        self.assertEqual([x["name"] for x in w["authors"]],
                         ["Zachary Butzin-Dozier", "Yi Wang", "Yu Wang", "Jane Roe"])
        self.assertEqual(sum(1 for x in w["authors"] if x["is_me"]), 1)

    def test_europepmc_suffix(self):
        x = {"authors": [{"fullName": "Colford JM Jr", "firstName": "John M", "lastName": "Colford",
                          "initials": "JM"},
                         {"fullName": "Smith JR", "firstName": "John R", "lastName": "Smith",
                          "initials": "JR"}]}
        r = merge.norm_europepmc(x, common.DEFAULT_ME, ORCID)
        self.assertEqual([a["apa"] for a in r["authors"]], ["Colford, J. M., Jr.", "Smith, J. R."])


if __name__ == "__main__":
    unittest.main()
