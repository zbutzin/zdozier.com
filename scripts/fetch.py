#!/usr/bin/env python3
"""Network step: query every source and write data/raw/<source>.json.

Each source runs independently. If one fails, its previous raw file is kept,
the error is recorded with used_cache: true, and the run continues. The
per-source status goes to data/raw/fetch_status.json, which merge.py folds
into data/run_report.json. Exit status is 0 unless the arguments are invalid.

Safeguards, so that a bad week never replaces good data:
- Each source gets a time budget (SOURCE_BUDGET seconds, and all sources
  together TOTAL_BUDGET); when it runs out the source fails and its cache is
  used. The status file is rewritten after every source, so a run that is
  killed part way still records which sources did not finish.
- A response that empties a source, or halves one that had 5 or more
  records, is not written: the previous file is kept and the drop is reported
  as an error. A lower (non-zero) count that comes back in the next run too is
  accepted, since two identical answers a week apart are unlikely to be a
  glitch.
- Error messages are scrubbed of API keys and tokens before they are stored.

Usage: python3 scripts/fetch.py [--only orcid,openalex] [--force-crossref]
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (CONTENT_DIR, RAW_DIR, Http, identity_config, load_json, load_site_config,  # noqa: E402
                    load_yaml, log, raw_path, scrub, utc_now_iso, write_json_atomic)
from sources import ORDER  # noqa: E402

STATUS_PATH = os.path.join(RAW_DIR, "fetch_status.json")
SOURCE_BUDGET = 240  # seconds per source
TOTAL_BUDGET = 720   # seconds for all sources (the CI job stops at 30 minutes)
DROP_MIN = 5         # a source with at least this many records may not lose half at once


def as_dict(x):
    return x if isinstance(x, dict) else {}


def as_int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _cached_count(name, payload):
    if not isinstance(payload, dict):
        return 0
    if name == "orcid":
        return len({w.get("group") for w in payload.get("works") or []})
    if name == "crossref":
        return len((payload.get("orcid_filter") or {}).get("dois") or [])
    if name == "europepmc":
        return len(payload.get("results") or [])
    if name == "reporter":
        return len({p.get("core_project_num") for p in payload.get("projects") or []})
    return len(payload.get("works") or payload.get("repos") or [])


def suspicious_drop(prev_n, new_n):
    """True if new_n records replacing prev_n looks like a broken response."""
    if prev_n > 0 and new_n == 0:
        return True
    return prev_n >= DROP_MIN and new_n < 0.5 * prev_n


def _failed_status(prev, name, error, calls, extra=None):
    st = {"ok": False, "count": _cached_count(name, prev), "error": scrub(error),
          "used_cache": prev is not None, "fetched_at": (prev or {}).get("fetched_at"),
          "requests": calls}
    st.update(extra or {})
    return st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated subset of sources: %s" % ",".join(ORDER))
    ap.add_argument("--force-crossref", action="store_true",
                    help="ignore the 30-day Crossref cache")
    args = ap.parse_args(argv)
    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]
        bad = [s for s in only if s not in ORDER]
        if bad:
            ap.error("unknown source(s): %s" % ", ".join(bad))

    site = load_site_config()
    ident = identity_config(site)
    pubs = load_yaml(os.path.join(CONTENT_DIR, "publications.yml"), {})
    http = Http()
    started = utc_now_iso()
    t0 = time.time()
    prev_status = load_json(STATUS_PATH, {}) or {}
    prev_sources = dict(prev_status.get("sources") or {})
    statuses = dict(prev_sources) if only else {}
    raw = {}
    for name in ORDER:
        raw[name] = load_json(raw_path(name), None)
    todo = [n for n in ORDER if not only or n in only]
    # Until a source has run it counts as failed, so a run cut short leaves an
    # accurate status file behind.
    for name in todo:
        statuses[name] = _failed_status(raw.get(name), name, "fetch did not finish", 0)

    def save(finished=None):
        write_json_atomic(STATUS_PATH, {"started": started, "finished": finished,
                                        "sources": statuses})

    save()
    for name in todo:
        mod = importlib.import_module("sources.%s" % name)
        ctx = {"site": site, "ident": ident, "http": http, "prev": raw.get(name),
               "raw": raw, "pubs": pubs if isinstance(pubs, dict) else {},
               "now": utc_now_iso(), "options": {"force_crossref": args.force_crossref}}
        log("fetch: %s ..." % name)
        calls_before = http.calls
        http.deadline = min(time.time() + SOURCE_BUDGET, t0 + TOTAL_BUDGET)
        prev = raw.get(name)
        try:
            payload, count = mod.fetch(ctx)
            prev_n = _cached_count(name, prev)
            new_n = _cached_count(name, payload)
            candidate = as_int(as_dict(prev_sources.get(name)).get("candidate_count"))
            if prev is not None and suspicious_drop(prev_n, new_n) \
                    and not (new_n > 0 and candidate == new_n):
                msg = ("suspicious drop from %d to %d records; kept the previous copy "
                       "(a non-zero count that repeats next run is accepted)" % (prev_n, new_n))
                log("fetch: %s %s" % (name, msg))
                statuses[name] = _failed_status(prev, name, msg, http.calls - calls_before,
                                                {"candidate_count": new_n})
            else:
                write_json_atomic(raw_path(name), payload)
                raw[name] = payload
                statuses[name] = {"ok": True, "count": count,
                                  "error": scrub(payload.get("partial_error")), "used_cache": False,
                                  "fetched_at": payload.get("fetched_at"),
                                  "requests": http.calls - calls_before}
                log("fetch: %s ok (%s)" % (name, count))
        except Exception as exc:  # a failing source never fails the run
            log(scrub("fetch: %s FAILED: %s" % (name, exc)))
            log(scrub(traceback.format_exc().strip().splitlines()[-1]))
            statuses[name] = _failed_status(prev, name, "%s: %s" % (type(exc).__name__, exc),
                                            http.calls - calls_before)
        finally:
            http.deadline = None
        save()

    save(utc_now_iso())
    failed = [k for k, v in statuses.items() if not v.get("ok")]
    log("fetch: done; %d source(s) failed%s" % (len(failed), (": " + ", ".join(failed)) if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
