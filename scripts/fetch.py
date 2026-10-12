#!/usr/bin/env python3
"""Network step: query every source and write data/raw/<source>.json.

Each source runs independently. If one fails, its previous raw file is kept,
the error is recorded with used_cache: true, and the run continues. The
per-source status goes to data/raw/fetch_status.json, which merge.py folds
into data/run_report.json. Exit status is 0 unless the arguments are invalid.

Usage: python3 scripts/fetch.py [--only orcid,openalex] [--force-crossref]
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (CONTENT_DIR, RAW_DIR, Http, identity_config, load_json, load_site_config,  # noqa: E402
                    load_yaml, log, raw_path, utc_now_iso, write_json_atomic)
from sources import ORDER  # noqa: E402

STATUS_PATH = os.path.join(RAW_DIR, "fetch_status.json")


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
    prev_status = load_json(STATUS_PATH, {}) or {}
    statuses = dict(prev_status.get("sources") or {}) if only else {}
    raw = {}
    for name in ORDER:
        raw[name] = load_json(raw_path(name), None)

    for name in ORDER:
        if only and name not in only:
            continue
        mod = importlib.import_module("sources.%s" % name)
        ctx = {"site": site, "ident": ident, "http": http, "prev": raw.get(name),
               "raw": raw, "pubs": pubs if isinstance(pubs, dict) else {},
               "now": utc_now_iso(), "options": {"force_crossref": args.force_crossref}}
        log("fetch: %s ..." % name)
        calls_before = http.calls
        try:
            payload, count = mod.fetch(ctx)
            write_json_atomic(raw_path(name), payload)
            raw[name] = payload
            statuses[name] = {"ok": True, "count": count,
                              "error": payload.get("partial_error"), "used_cache": False,
                              "fetched_at": payload.get("fetched_at"),
                              "requests": http.calls - calls_before}
            log("fetch: %s ok (%s)" % (name, count))
        except Exception as exc:  # a failing source never fails the run
            log("fetch: %s FAILED: %s" % (name, exc))
            log(traceback.format_exc().strip().splitlines()[-1])
            prev = raw.get(name)
            statuses[name] = {"ok": False, "count": _cached_count(name, prev),
                              "error": "%s: %s" % (type(exc).__name__, exc),
                              "used_cache": prev is not None,
                              "fetched_at": (prev or {}).get("fetched_at"),
                              "requests": http.calls - calls_before}

    status = {"started": started, "finished": utc_now_iso(), "sources": statuses}
    write_json_atomic(STATUS_PATH, status)
    failed = [k for k, v in statuses.items() if not v.get("ok")]
    log("fetch: done; %d source(s) failed%s" % (len(failed), (": " + ", ".join(failed)) if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
