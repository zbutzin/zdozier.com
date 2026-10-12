#!/usr/bin/env python3
"""Write the bodies of the GitHub issues the weekly update opens.

Reads data/run_report.json (and data/publications.json for year, DOI, and
status). By default prints a Markdown issue body listing the works that
appeared for the first time in this run, or nothing when there are none.

With --health, prints the body of the "Site data is going stale" issue
instead: one is due when a source has been failing (the site shows its cached
copy) for STALE_DAYS days or more, or when the sanity guard kept the previous
publication list. Prints nothing when the data is current.

Usage:
    python3 scripts/ci_issue.py              # print the new-publications body
    python3 scripts/ci_issue.py --count      # print the number of new works
    python3 scripts/ci_issue.py --health     # print the site-health body
    python3 scripts/ci_issue.py --health-state   # stale, degraded, or ok
    python3 scripts/ci_issue.py --report PATH --publications PATH

Always exits 0 so that it can never fail the weekly update. Problems reading
the files are reported on stderr. Standard library only.
"""

from __future__ import print_function

import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT = os.path.join(ROOT, "data", "run_report.json")
DEFAULT_PUBLICATIONS = os.path.join(ROOT, "data", "publications.json")

SITE_PUBLICATIONS_URL = "https://zdozier.com/publications/"
MAX_LISTED = 60  # keeps the issue well under GitHub's 65,536 character limit

SOURCE_NAMES = {
    "orcid": "ORCID",
    "openalex": "OpenAlex",
    "crossref": "Crossref",
    "europepmc": "Europe PMC",
    "manual": "manual entry",
    "forced": "include_dois",
}

STATUS_LABELS = {
    "preprint": "Preprint",
    "accepted": "Accepted",
    "under-review": "Under review",
}

# Keys in content/publications.yml used in the copy-paste snippet.
HIDE_KEY = "hide"
OVERRIDES_KEY = "overrides"
CONTRIBUTION_FIELD = "contribution"

ROLE_LABELS = {
    "first": "First author",
    "senior": "Senior author",
}

# Site health: a failing source is reported once its last good copy is this old.
STALE_DAYS = 14

# Same rule as common.scrub (this script uses the standard library only):
# API keys and tokens never go into an issue body.
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(api_key|apikey|key|token|access_token|mailto)=[^&\s'\")]+")
SECRET_ENV_VARS = ("OPENALEX_API_KEY", "GITHUB_TOKEN")


def scrub(text):
    if text is None:
        return None
    out = _SECRET_PARAM_RE.sub(lambda m: m.group(1) + "=REDACTED", str(text))
    for var in SECRET_ENV_VARS:
        val = os.environ.get(var)
        if val and len(val) >= 6:
            out = out.replace(val, "REDACTED")
    return out

HEALTH_SOURCE_NAMES = {
    "orcid": "ORCID",
    "openalex": "OpenAlex",
    "crossref": "Crossref",
    "europepmc": "Europe PMC",
    "reporter": "NIH RePORTER",
    "github": "GitHub",
}

HEALTH_SOURCE_SHOWS = {
    "orcid": "the publication list",
    "openalex": "citation counts, h-index, and newly found papers",
    "crossref": "author lists and journal details",
    "europepmc": "PubMed links and papers ahead of print",
    "reporter": "NIH grants",
    "github": "the Software section",
}


def warn(msg):
    print("ci_issue.py: " + msg, file=sys.stderr)


def load_json(path):
    """Return parsed JSON, or None if the file is missing or unreadable."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        warn("could not read %s: %s" % (path, exc))
        return None


def get_new_works(report):
    if not isinstance(report, dict):
        return []
    works = report.get("new_works") or []
    if not isinstance(works, list):
        return []
    return [w for w in works if isinstance(w, dict)]


def index_publications(pubs):
    """Map id and DOI (lowercase) to the full record from publications.json."""
    index = {}
    works = pubs.get("works") if isinstance(pubs, dict) else None
    for work in works or []:
        if not isinstance(work, dict):
            continue
        for key in (work.get("id"), work.get("doi")):
            if key:
                index[str(key).lower()] = work
    return index


def norm_doi(value):
    if not value:
        return None
    doi = str(value).strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I)
    return doi.strip().lower() or None


def md_escape(text):
    """Escape characters that GitHub Markdown would treat as formatting."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([`*_\[\]#|~])", r"\\\1", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def first(*values):
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def describe(work, pub_index):
    """Combine the run_report entry with its publications.json record."""
    key = str(work.get("id") or "").lower()
    pub = pub_index.get(key)
    if pub is None and work.get("doi"):
        pub = pub_index.get(str(work["doi"]).lower())
    pub = pub or {}

    doi = norm_doi(first(work.get("doi"), pub.get("doi")))
    if doi is None and key and not key.startswith("manual:") and key.startswith("10."):
        doi = norm_doi(key)

    year = first(work.get("year"), pub.get("year"))
    if year is None:
        date = first(work.get("date"), pub.get("date"))
        if date:
            year = str(date)[:4]

    sources = first(work.get("sources"), pub.get("sources")) or []
    if not isinstance(sources, list):
        sources = [sources]

    return {
        "key": doi or work.get("id") or pub.get("id"),
        "title": first(work.get("title"), pub.get("title")) or "(untitled)",
        "venue": first(work.get("venue"), pub.get("venue")),
        "year": year,
        "doi": doi,
        "url": ("https://doi.org/" + doi) if doi else first(work.get("url"), pub.get("url")),
        "status": first(work.get("status"), pub.get("status")),
        "status_note": first(work.get("status_note"), pub.get("status_note")),
        "role": first(work.get("role"), pub.get("role")),
        "contribution": first(work.get("contribution"), pub.get("contribution")),
        "sources": [str(s) for s in sources],
    }


def sort_key(item):
    try:
        year = int(str(item["year"])[:4])
    except (TypeError, ValueError):
        year = 0
    return (-year, str(item["title"]).lower())


def run_date(report):
    stamp = first(report.get("finished"), report.get("started")) if isinstance(report, dict) else None
    if stamp:
        return str(stamp)[:10]
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def related_warnings(report, items):
    """Warnings from the run that mention one of the new works."""
    warnings = report.get("warnings") if isinstance(report, dict) else None
    if not isinstance(warnings, list):
        return []
    needles = set()
    for item in items:
        if item["key"]:
            needles.add(str(item["key"]).lower())
        if item["doi"]:
            needles.add(item["doi"])
    out = []
    for text in warnings:
        if not isinstance(text, str):
            text = json.dumps(text, sort_keys=True)
        low = text.lower()
        if any(n in low for n in needles):
            out.append(text)
    return out


def render_entry(number, item):
    lines = ["%d. **%s**" % (number, md_escape(item["title"]))]

    meta = []
    if item["venue"]:
        meta.append("*%s*" % md_escape(item["venue"]))
    if item["year"]:
        meta.append(md_escape(item["year"]))
    detail = ", ".join(meta)
    tags = []
    if item["status"] in STATUS_LABELS:
        label = STATUS_LABELS[item["status"]]
        if item["status_note"]:
            label += " (%s)" % md_escape(item["status_note"])
        tags.append(label)
    if item["role"] in ROLE_LABELS:
        tags.append(ROLE_LABELS[item["role"]])
    if tags:
        detail = (detail + ". " if detail else "") + ". ".join(tags)
    if detail:
        lines.append("   " + detail + ".")

    if item["doi"]:
        lines.append("   DOI: [%s](%s)" % (md_escape(item["doi"]), item["url"]))
    elif item["url"]:
        lines.append("   Link: <%s>" % item["url"])
    else:
        lines.append("   No DOI yet. ID: `%s`" % item["key"])
    if is_manual(item):
        lines.append("   From the `manual:` list in `content/publications.yml`. "
                     "Edit or remove that entry to change it.")

    if item["sources"]:
        names = [SOURCE_NAMES.get(s, s) for s in item["sources"]]
        lines.append("   Found by: " + ", ".join(names))
        if item["sources"] == ["europepmc"]:
            lines.append(
                "   Only Europe PMC found this one, so it may be ahead of print "
                "or a name match. Worth a quick check that it is yours."
            )
    return "\n".join(lines)


def short_title(title, limit=60):
    title = re.sub(r"\s+", " ", str(title)).strip()
    if len(title) > limit:
        title = title[: limit - 3].rstrip() + "..."
    return title


def is_manual(item):
    return str(item["key"] or "").startswith("manual:")


def needs_note(item):
    """Contribution notes are for middle-author papers that lack one."""
    return item["role"] not in ("first", "senior") and not item.get("contribution")


def render_snippet(items):
    """YAML lines to paste into content/publications.yml, keyed by DOI."""
    keyed = [i for i in items if i["key"] and not is_manual(i)]
    if not keyed:
        return ""
    width = max(len(json.dumps(str(i["key"]))) for i in keyed)
    lines = ["```yaml", "%s:" % HIDE_KEY]
    for i in keyed:
        lines.append("  - %s  # %s" % (json.dumps(str(i["key"])).ljust(width),
                                       short_title(i["title"])))
    noted = [i for i in keyed if needs_note(i)]
    if noted:
        lines.append("")
        lines.append("%s:" % OVERRIDES_KEY)
        for i in noted:
            lines.append("  %s:" % json.dumps(str(i["key"])))
            lines.append('    %s: "Describe your role here."' % CONTRIBUTION_FIELD)
    lines.append("```")
    return "\n".join(lines)


def render_actions(listed):
    keyed = [i for i in listed if i["key"] and not is_manual(i)]
    if not keyed:
        return [
            "### What you can do",
            "",
            "**Nothing.** The site and CV PDF already include these works.",
            "",
        ]
    has_notes = any(needs_note(i) for i in keyed)
    parts = [
        "### What you can do",
        "",
        "**Nothing.** The site and CV PDF already include these works.",
        "",
        "If you want to change how one appears, edit `content/publications.yml` "
        "(on github.com: open the file, click the pencil icon, then commit). The "
        "site rebuilds a few minutes after the commit.",
        "",
        "- To hide a work, copy its line into the existing `%s:` list." % HIDE_KEY,
    ]
    if has_notes:
        parts.append(
            "- To add a note about your role on a co-authored paper (shown under "
            "the entry and as an indented note in the CV), copy its two lines into "
            "the existing `%s:` section and replace the placeholder text." % OVERRIDES_KEY
        )
    parts.extend([
        "",
        "Keep only the lines you want, and add them under the keys already in the "
        "file rather than pasting a second copy of a key (YAML keeps only the last "
        "copy of a repeated key, which would undo earlier entries).",
        "",
        render_snippet(listed),
        "",
    ])
    return parts


def build_body(report, pubs=None):
    """Return the Markdown issue body, or "" when there is nothing new."""
    works = get_new_works(report)
    if not works:
        return ""
    pub_index = index_publications(pubs)
    items = sorted((describe(w, pub_index) for w in works), key=sort_key)
    n = len(items)
    noun = "publication" if n == 1 else "publications"
    it = "It is" if n == 1 else "They are"

    parts = [
        "The site update on %s found %d new %s. %s already live on %s and in the CV."
        % (run_date(report), n, noun, it, SITE_PUBLICATIONS_URL),
        "",
    ]
    listed = items[:MAX_LISTED]
    for i, item in enumerate(listed, 1):
        parts.append(render_entry(i, item))
        parts.append("")
    if n > len(listed):
        parts.append(
            "...and %d more. The full list is in `data/run_report.json` (`new_works`)."
            % (n - len(listed))
        )
        parts.append("")

    notes = related_warnings(report, items)
    if notes:
        parts.append("<details><summary>Notes from this run</summary>")
        parts.append("")
        for text in notes:
            parts.append("- " + md_escape(text))
        parts.append("")
        parts.append("</details>")
        parts.append("")

    parts.extend(render_actions(listed))
    parts.extend([
        "Close this issue once you have looked. If more new works turn up while it "
        "is open, they are added here as comments.",
        "",
        "<sub>Generated by `scripts/ci_issue.py` from `data/run_report.json`.</sub>",
    ])
    return "\n".join(parts).rstrip() + "\n"


def parse_time(value):
    """'2026-10-12T14:04:40Z' -> aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        day = datetime.datetime.strptime(text[:10], "%Y-%m-%d")
        return day.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def stale_sources(report, now=None):
    """Sources that failed in this run and whose last good copy is STALE_DAYS
    or more old (or missing). Returns (name, status dict, last good datetime)."""
    sources = report.get("sources") if isinstance(report, dict) else None
    if not isinstance(sources, dict):
        return []
    ref = parse_time(report.get("finished")) or now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for name in sorted(sources):
        status = sources[name]
        if not isinstance(status, dict):
            continue
        if status.get("ok") and not status.get("used_cache"):
            continue
        last = parse_time(status.get("fetched_at"))
        if last is not None and (ref - last).days < STALE_DAYS:
            continue
        out.append((name, status, last))
    return out


def guard_tripped(report):
    guard = report.get("sanity_guard") if isinstance(report, dict) else None
    return isinstance(guard, dict) and bool(guard.get("tripped"))


def health_state(report, now=None):
    """'stale' (an issue is due), 'ok' (every source refreshed without errors
    and the list was updated, so an open issue can be closed), or 'degraded'
    (something failed, or a source reported a partial error, but not for long
    enough to report yet)."""
    if not isinstance(report, dict):
        return "degraded"
    if stale_sources(report, now) or guard_tripped(report):
        return "stale"
    sources = report.get("sources")
    if not isinstance(sources, dict) or not sources:
        return "degraded"
    for status in sources.values():
        if not isinstance(status, dict) or not status.get("ok") or status.get("used_cache") \
                or status.get("error"):
            return "degraded"
    return "ok"


def build_health_body(report, now=None):
    """Return the Markdown body of the site-health issue, or "" if all is well."""
    if not isinstance(report, dict):
        return ""
    stale = stale_sources(report, now)
    tripped = guard_tripped(report)
    if not stale and not tripped:
        return ""
    ref = parse_time(report.get("finished")) or now or datetime.datetime.now(datetime.timezone.utc)
    parts = [
        "The site update on %s could not refresh everything. zdozier.com is still online and "
        "shows the last good copy, so the parts listed below are no longer current."
        % run_date(report),
        "",
    ]
    if stale:
        parts.append("### Sources that keep failing")
        parts.append("")
        for name, status, last in stale:
            label = HEALTH_SOURCE_NAMES.get(name, name)
            shows = HEALTH_SOURCE_SHOWS.get(name)
            if last is None:
                when = "no successful fetch on record"
            else:
                when = "last good copy from %s (%d days old)" % (last.strftime("%Y-%m-%d"),
                                                                (ref - last).days)
            line = "- **%s**: %s." % (md_escape(label), when)
            if shows:
                line += " Affects %s." % shows
            parts.append(line)
            if status.get("error"):
                parts.append("  Error: %s" % md_escape(scrub(status["error"])[:300]))
        parts.append("")
    if tripped:
        parts.append("### Publication list kept from the previous run")
        parts.append("")
        note = None
        for text in report.get("warnings") or []:
            if isinstance(text, str) and text.startswith("sanity guard"):
                note = text
                break
        parts.append(
            "This run would have removed more than 15% of the listed works at once, so the "
            "previous list was kept. That usually means a source returned a partial list. "
            "New works are not added while the old list is kept.")
        if note:
            parts.append("")
            parts.append("> " + md_escape(scrub(note)))
        parts.append("")
        parts.append(
            "If the next run comes back with exactly the same shorter list, it is accepted "
            "automatically and this issue closes. If the drop is real and you want it now "
            "(for example, you hid several works or merged duplicates), add "
            "`accept_count_drop: %s` at the top level of `content/publications.yml`; it is "
            "honored for 14 days." % run_date(report))
        parts.append("")
    parts.extend([
        "### What you can do",
        "",
        "**Usually nothing.** Most outages end on their own. This issue closes itself after "
        "the next run in which every source refreshes normally (and the publication list is "
        "updated), and you get one more email when that happens.",
        "",
        "If it stays open for several weeks, open the latest run under **Actions** > "
        "**Update site** and read the log of the step \"Fetch, merge, build, and print the CV\". "
        "If OpenAlex is the source that fails, an API key usually fixes it (see "
        "`docs/SETUP.md`, \"Optional: an OpenAlex API key\").",
        "",
        "<sub>Generated by `scripts/ci_issue.py --health` from `data/run_report.json`.</sub>",
    ])
    return "\n".join(parts).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--publications", default=DEFAULT_PUBLICATIONS)
    parser.add_argument("--count", action="store_true",
                        help="print the number of new works and exit")
    parser.add_argument("--health", action="store_true",
                        help="print the site-health issue body (empty when the data is current)")
    parser.add_argument("--health-state", action="store_true",
                        help="print stale, degraded, or ok")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0
    try:
        report = load_json(args.report)
        if args.count:
            print(len(get_new_works(report)))
            return 0
        if args.health_state:
            print(health_state(report))
            return 0
        if args.health:
            body = build_health_body(report)
            if body:
                sys.stdout.write(body)
            return 0
        body = build_body(report, load_json(args.publications))
        if body:
            sys.stdout.write(body)
    except Exception as exc:  # never fail the workflow over the issue text
        warn("unexpected error: %r" % (exc,))
        if args.count:
            print(0)
        elif args.health_state:
            print("degraded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
