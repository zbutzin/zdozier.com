"""Shared helpers for the zdozier.com data pipeline.

Covers paths, YAML/JSON IO with atomic writes, a polite HTTP client with
retries, DOI normalization and preprint detection, author-name parsing,
"me" detection, and title normalization for version matching.

Runs on Python 3.8 (Jinja2 2.11, PyYAML 5.4, requests 2.25) and 3.12.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import html
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from urllib.parse import unquote, urlparse

import yaml

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(ROOT, "content")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

USER_AGENT = "zdozier.com-site-builder/1.0 (+https://zdozier.com)"
TIMEOUT = 30
RETRIES = 3


def log(msg):
    """Progress messages go to stderr so stdout stays clean."""
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def utc_now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def utc_now_iso():
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_datetime(s):
    """Parse '2026-10-09T20:00:00Z' (or with offset). Returns aware datetime or None."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return _dt.datetime.strptime(t, fmt)
        except ValueError:
            continue
    try:
        d = _dt.datetime.strptime(t[:10], "%Y-%m-%d")
        return d.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def today_iso(today=None):
    if today is not None:
        return today.isoformat() if hasattr(today, "isoformat") else str(today)
    env = os.environ.get("ZD_TODAY")  # testing hook: pin "today"
    if env:
        return env
    return _dt.date.today().isoformat()


def partial_date(year, month=None, day=None):
    """Build 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' from parts (ints or strings)."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    if y < 1900 or y > 2200:
        return None
    out = "%04d" % y
    try:
        if month is not None and str(month).strip() != "":
            m = int(month)
            if 1 <= m <= 12:
                out += "-%02d" % m
                if day is not None and str(day).strip() != "":
                    d = int(day)
                    if 1 <= d <= 31:
                        out += "-%02d" % d
    except (TypeError, ValueError):
        pass
    return out


def clean_date(s):
    """Normalize a date-ish string to YYYY[-MM[-DD]] or None."""
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", s)
    if not m:
        return None
    return partial_date(m.group(1), m.group(2), m.group(3))


def date_sort_key(date):
    """Sort key for partial dates. Missing month/day sort as late in the year
    (an undated 'in press' 2026 item sorts with the newest 2026 items)."""
    d = clean_date(date)
    if not d:
        return (0, 0, 0)
    parts = d.split("-")
    y = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 13
    dd = int(parts[2]) if len(parts) > 2 else 32
    return (y, m, dd)


def date_to_ordinal(date, end=False):
    """Approximate day number for a partial date; start or end of the period."""
    d = clean_date(date)
    if not d:
        return None
    parts = [int(p) for p in d.split("-")]
    y = parts[0]
    if len(parts) == 1:
        m, dd = (12, 31) if end else (1, 1)
    elif len(parts) == 2:
        m = parts[1]
        if end:
            nxt = _dt.date(y + (m // 12), (m % 12) + 1, 1)
            dd = (nxt - _dt.timedelta(days=1)).day
        else:
            dd = 1
    else:
        m, dd = parts[1], parts[2]
    try:
        return _dt.date(y, m, dd).toordinal()
    except ValueError:
        return _dt.date(y, m, 1).toordinal()


def year_of(date):
    d = clean_date(date)
    return int(d[:4]) if d else None


# --------------------------------------------------------------------------
# File IO
# --------------------------------------------------------------------------

class UniqueKeyLoader(yaml.SafeLoader):
    """yaml.SafeLoader that refuses repeated keys in a mapping.

    Plain PyYAML keeps only the last copy of a repeated key, so a second
    `overrides:` block (or a second entry for one DOI) would silently drop
    every setting in the first one."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
            seen = {}
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    first = seen.get(key)
                except TypeError:
                    continue  # unhashable key; the base class reports it
                if first is not None:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        "found duplicate key %r (first used on line %d)" % (key, first.line + 1),
                        key_node.start_mark)
                seen[key] = key_node.start_mark
        return super(UniqueKeyLoader, self).construct_mapping(node, deep)


def yaml_error_message(path, exc):
    """One readable line for a YAML mistake: file, line, problem, and a hint."""
    try:
        rel = os.path.relpath(path, ROOT)
        if rel.startswith(".."):
            rel = path
    except ValueError:
        rel = path
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc).strip().splitlines()[0]
    where = "%s line %d" % (rel, mark.line + 1) if mark is not None else rel
    low = problem.lower()
    if "duplicate key" in low:
        hint = ("YAML keeps only the last copy of a repeated key. Put the new lines inside the "
                "existing entry instead.")
    elif "mapping values are not allowed" in low:
        hint = "Text that contains a colon must be in quotes."
    elif "\\t" in problem or "tab" in low:
        hint = "Indent with spaces, not tabs."
    else:
        hint = "Check the quotes and indentation on that line and the one above it."
    return "%s: %s. %s" % (where, problem.rstrip("."), hint)


def load_yaml(path, default=None):
    """Load a hand-edited YAML file from content/.

    A missing or empty file returns `default`. A file with a mistake (bad
    syntax or a repeated key) stops the run with a one-line message and exit
    status 1, before anything in data/ is rewritten from it."""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        log("error: " + yaml_error_message(path, exc))
        sys.exit(1)
    if data is None:
        return default
    return data


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        log("warning: could not parse %s: %s" % (path, exc))
        return default


def write_json_atomic(path, obj):
    """Write JSON via a temp file in the same directory, then os.replace."""
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def raw_path(source):
    return os.path.join(RAW_DIR, "%s.json" % source)


def as_list(x):
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def as_dict(x):
    return x if isinstance(x, dict) else {}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_ME = {
    "family_names": ["Butzin-Dozier", "Butzin Dozier", "Dozier"],
    "given_initial": "Z",
}


def load_site_config(content_dir=None):
    content_dir = content_dir or CONTENT_DIR
    site = load_yaml(os.path.join(content_dir, "site.yml"), {})
    if not isinstance(site, dict):
        site = {}
    return site


def identity_config(site):
    ident = as_dict(site.get("identity"))
    me = dict(DEFAULT_ME)
    me.update({k: v for k, v in as_dict(ident.get("me")).items() if v})
    out = {
        "orcid": normalize_orcid(ident.get("orcid")),
        "openalex_author_id": (ident.get("openalex_author_id") or None),
        "github_user": ident.get("github_user") or None,
        "me": me,
        "nih_reporter": as_dict(ident.get("nih_reporter")),
        "europepmc_query": ident.get("europepmc_query") or None,
    }
    return out


def contact_email(site):
    api = as_dict(site.get("api"))
    email = api.get("contact_email")
    if email and isinstance(email, str) and "@" in email:
        return email.strip()
    return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class HttpError(Exception):
    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


class Http(object):
    """requests wrapper: timeout 30s, 3 retries with exponential backoff,
    polite User-Agent, optional per-host minimum interval between requests."""

    def __init__(self, user_agent=USER_AGENT, timeout=TIMEOUT, retries=RETRIES,
                 backoff=1.5, min_interval=None, session=None):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = dict(min_interval or {})
        self._last = {}
        self.calls = 0

    def _throttle(self, host):
        gap = self.min_interval.get(host)
        if not gap:
            return
        last = self._last.get(host)
        if last is not None:
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)

    def request(self, method, url, params=None, json_body=None, headers=None,
                allow_404=False):
        import requests
        host = urlparse(url).netloc
        last_err = None
        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                self.calls += 1
                resp = self.session.request(method, url, params=params, json=json_body,
                                            headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = HttpError("%s %s: %s" % (method, url, exc))
                self._last[host] = time.time()
                if attempt < self.retries:
                    time.sleep(self.backoff * (2 ** attempt))
                continue
            self._last[host] = time.time()
            status = resp.status_code
            if status == 404 and allow_404:
                return None
            if status == 429 or status >= 500:
                last_err = HttpError("%s %s: HTTP %d" % (method, url, status), status)
                if attempt < self.retries:
                    wait = self.backoff * (2 ** attempt)
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        try:
                            wait = max(wait, min(float(ra), 60.0))
                        except ValueError:
                            pass
                    time.sleep(wait)
                continue
            if status >= 400:
                raise HttpError("%s %s: HTTP %d %s" % (method, url, status, resp.text[:200]),
                                status)
            try:
                return resp.json()
            except ValueError:
                raise HttpError("%s %s: response is not JSON" % (method, url), status)
        raise last_err or HttpError("%s %s: failed" % (method, url))

    def get_json(self, url, params=None, headers=None, allow_404=False):
        return self.request("GET", url, params=params, headers=headers, allow_404=allow_404)

    def post_json(self, url, body, headers=None):
        return self.request("POST", url, json_body=body, headers=headers)


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")


def normalize_doi(value):
    """Lowercase, strip URL/doi: prefixes and whitespace. None if not a DOI."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if "%2f" in s.lower():
        s = unquote(s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:", "", s, flags=re.I)
    s = s.lower()
    m = _DOI_RE.search(s)
    if not m:
        return None
    return m.group(0).rstrip(".,;")


def normalize_orcid(value):
    if not value or not isinstance(value, str):
        return None
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])", value)
    return m.group(1).upper() if m else None


def normalize_pmid(value):
    if value is None:
        return None
    m = re.search(r"(\d{1,9})\s*$", str(value).strip())
    return m.group(1) if m else None


def normalize_pmcid(value, allow_bare=False):
    """'PMC123', '.../pmc/articles/123' -> 'PMC123'. Bare digits only if allowed."""
    if value is None:
        return None
    s = str(value).strip()
    m = re.search(r"PMC(\d+)", s, re.I)
    if m:
        return "PMC" + m.group(1)
    if "/pmc/" in s.lower():
        m = re.search(r"(\d{4,9})/?$", s)
        if m:
            return "PMC" + m.group(1)
    if allow_bare and s.isdigit():
        return "PMC" + s
    return None


def doi_url(doi):
    return "https://doi.org/%s" % doi if doi else None


# Preprint servers by DOI prefix. 10.1101 is shared with Cold Spring Harbor
# journals (Genome Res., Genes Dev., ...), so only dated (2019.12.31.123456)
# or legacy six-digit bioRxiv suffixes count as preprints.
PREPRINT_PREFIXES = [
    ("10.1101/", None),
    ("10.64898/", "medRxiv"),
    ("10.2196/preprints.", "JMIR Preprints"),
    ("10.21203/", "Research Square"),
    ("10.2139/ssrn", "SSRN"),
    ("10.48550/", "arXiv"),
    ("10.31219/", "OSF Preprints"),
    ("10.20944/", "Preprints.org"),
]

# Institutional and data repositories: copies, not separate works.
REPOSITORY_PREFIXES = [
    ("10.17615/", "Carolina Digital Repository"),
    ("10.5281/", "Zenodo"),
    ("10.6084/", "figshare"),
    ("10.5061/", "Dryad"),
    ("10.17605/", "OSF"),
    ("10.7910/", "Harvard Dataverse"),
    ("10.25740/", "Stanford Digital Repository"),
]

# Registered with DataCite, not Crossref: skip Crossref lookups.
NON_CROSSREF_PREFIXES = ("10.17615/", "10.5281/", "10.6084/", "10.5061/", "10.17605/",
                         "10.7910/", "10.25740/", "10.48550/")

# Work types that are never shown as publications.
OPENALEX_EXCLUDED_TYPES = {"peer-review", "paratext", "erratum", "retraction", "dataset",
                           "grant", "standard", "supplementary-materials", "libguides"}
CROSSREF_EXCLUDED_TYPES = {"peer-review", "component", "dataset", "database", "grant",
                           "standard", "journal-issue", "journal-volume", "journal",
                           "report-component", "proceedings", "book-set", "book-series"}
ORCID_EXCLUDED_TYPES = {"data-set", "software", "lecture-speech", "physical-object",
                        "research-tool", "research-technique", "spin-off-company",
                        "standards-and-policy", "technical-standard", "trademark", "license",
                        "patent", "disclosure", "registered-copyright", "invention", "annotation",
                        "test", "artistic-performance", "conference-poster"}

_CSHL_PREPRINT_RE = re.compile(r"^10\.1101/(\d{4}\.\d{2}\.\d{2}\.\d+|\d{6})(v\d+)?$")


def preprint_server(doi, hint=None):
    """Name of the preprint server for a DOI, or None if not a preprint DOI."""
    doi = normalize_doi(doi)
    if not doi:
        return None
    for prefix, name in PREPRINT_PREFIXES:
        if doi.startswith(prefix):
            if prefix == "10.1101/":
                m = _CSHL_PREPRINT_RE.match(doi)
                if not m:
                    return None
                h = (hint or "").lower()
                if "biorxiv" in h:
                    return "bioRxiv"
                if "medrxiv" in h:
                    return "medRxiv"
                tail = m.group(1).split(".")[-1]
                # medRxiv suffixes are 8 digits starting with the 2-digit year.
                return "medRxiv" if len(tail) == 8 else "bioRxiv"
            return name
    return None


def is_preprint_doi(doi):
    return preprint_server(doi) is not None


def repository_name(doi):
    doi = normalize_doi(doi)
    if not doi:
        return None
    for prefix, name in REPOSITORY_PREFIXES:
        if doi.startswith(prefix):
            return name
    return None


def is_repository_doi(doi):
    return repository_name(doi) is not None


_VERSIONED_PREPRINT_RES = [
    # Research Square: 10.21203/rs.3.rs-455696/v1, .../v2 (the rs.N part can change too)
    (re.compile(r"^10\.21203/rs\.\d+\.rs-(\d+)/v\d+$"), "10.21203/rs-%s"),
    # Preprints.org: 10.20944/preprints202301.0001.v1, ...v2
    (re.compile(r"^10\.20944/(preprints\d{6}\.\d+)\.v\d+$"), "10.20944/%s"),
]


def preprint_base(doi):
    """Version-free key for preprint DOIs that carry a version number, so that
    v1 and v2 of one preprint are recognized as the same work. None otherwise."""
    doi = normalize_doi(doi)
    if not doi:
        return None
    for rx, fmt in _VERSIONED_PREPRINT_RES:
        m = rx.match(doi)
        if m:
            return fmt % m.group(1)
    return None


def jmir_pair(doi):
    """10.2196/preprints.N <-> 10.2196/N. Returns the counterpart DOI or None."""
    doi = normalize_doi(doi)
    if not doi:
        return None
    m = re.match(r"^10\.2196/preprints\.(\d+)$", doi)
    if m:
        return "10.2196/%s" % m.group(1)
    m = re.match(r"^10\.2196/(\d+)$", doi)
    if m:
        return "10.2196/preprints.%s" % m.group(1)
    return None


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

_DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_DASH_RE = re.compile("[%s]" % _DASH_CHARS)
_TAG_RE = re.compile(r"<[^>]+>")


def normalize_dashes(s):
    return _DASH_RE.sub("-", s) if s else s


def strip_html(s):
    if s is None:
        return None
    s = _TAG_RE.sub("", str(s))
    s = html.unescape(s)
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_accents(s):
    if not s:
        return s
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Words kept capitalized when converting an ALL CAPS title to sentence case.
_KEEP_UPPER = {"COVID", "COVID-19", "SARS-COV-2", "HIV", "AIDS", "N3C", "US", "USA", "UK",
               "NIH", "EHR", "EHRS", "AI", "ML", "WASH", "RCT", "TMLE", "HAL", "LASSO",
               "SSRI", "SSRIS", "GLP-1", "IL-6", "EED", "PEDSNET", "PCORNET", "LLM", "LLMS"}
_PROPER = {"bangladesh": "Bangladesh", "kenya": "Kenya", "ecuador": "Ecuador", "india": "India",
           "tamil": "Tamil", "nadu": "Nadu", "bangladeshi": "Bangladeshi", "kenyan": "Kenyan",
           "california": "California", "american": "American"}
_SPECIAL_CASE = {"SARS-COV-2": "SARS-CoV-2", "PEDSNET": "PEDSnet", "PCORNET": "PCORnet",
                 "SSRIS": "SSRIs", "EHRS": "EHRs", "LLMS": "LLMs"}


def fix_all_caps(title):
    """Sentence-case a title only if the whole title is uppercase."""
    if not title:
        return title
    letters = [c for c in title if c.isalpha()]
    if not letters or any(c.islower() for c in letters):
        return title
    out = []
    for i, tok in enumerate(re.split(r"(\s+)", title)):
        if not tok or tok.isspace():
            out.append(tok)
            continue
        core = re.sub(r"^[^\w]+|[^\w]+$", "", tok)
        lead = tok[:tok.find(core)] if core else ""
        trail = tok[tok.find(core) + len(core):] if core else ""
        if core.upper() in _SPECIAL_CASE:
            new = _SPECIAL_CASE[core.upper()]
        elif core.upper() in _KEEP_UPPER:
            new = core.upper()
        elif core.lower() in _PROPER:
            new = _PROPER[core.lower()]
        else:
            new = core.lower()
        out.append(lead + new + trail if core else tok.lower())
    s = "".join(out)
    # Capitalize the first letter and the first letter after a colon.
    s = re.sub(r"^(\W*)(\w)", lambda m: m.group(1) + m.group(2).upper(), s)
    s = re.sub(r"(:\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), s)
    return s


def clean_title(title):
    """Display title: strip HTML, fix ALL CAPS, drop '(Preprint)' tag and one
    trailing period (APA formatting adds its own)."""
    t = strip_html(title)
    if not t:
        return None
    t = re.sub(r"\s*\((?:preprint)\)\s*$", "", t, flags=re.I)
    # Site rule: no em dashes. A spaced or unspaced em dash in a source title
    # becomes a colon; hyphen look-alikes become ASCII hyphens.
    t = re.sub(r"\s*[\u2014\u2015]\s*", ": ", t)
    t = re.sub(r"[\u2010\u2011]", "-", t)
    t = fix_all_caps(t)
    if t.endswith(".") and not t.endswith("..") and not re.search(r"\b[A-Z]\.$", t):
        t = t[:-1].rstrip()
    return t


def match_title(title):
    """Normalized form for fuzzy matching: lowercase, no HTML/punctuation/accents."""
    t = strip_html(title) or ""
    t = re.sub(r"\s*\((?:preprint)\)\s*$", "", t, flags=re.I)
    t = strip_accents(normalize_dashes(t)).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = t.replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def title_ratio(a, b):
    na, nb = match_title(a), match_title(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


_STOPWORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "by",
              "from", "at", "as", "via", "using", "among", "during", "is", "are", "can",
              "be", "vs", "versus", "its", "their", "into", "after", "before", "between"}


def _stem(w):
    if w == "children":
        return "child"
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def title_tokens(title):
    return {_stem(w) for w in match_title(title).split() if w not in _STOPWORDS}


def title_overlap(a, b):
    """Overlap coefficient of content words: |A & B| / min(|A|, |B|)."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(min(len(ta), len(tb)))


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

PARTICLES = {"van", "der", "den", "de", "del", "della", "di", "da", "dos", "das", "du",
             "von", "zu", "la", "le", "ter", "ten", "st", "st.", "bin", "ibn", "al", "el",
             "y", "op", "het", "'t"}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii"}

_GROUP_RE = re.compile(
    r"\b(consortium|consortia|collaborative|collaboration|collaborators|investigators|"
    r"group|network|committee|initiative|alliance|program|programme|task\s+force|"
    r"working\s+party|registry|study\s+team|research\s+team|writing\s+team|team)\b"
    r"|\bN3C\b|^\s*on\s+behalf\s+of\b|^\s*the\s+\w+",
    re.I,
)


def is_group_name(name):
    """True for consortium or collaborative group authors."""
    if not name:
        return False
    s = strip_html(name)
    if not s:
        return False
    if _GROUP_RE.search(s):
        # "The" alone is not enough unless followed by a group-ish phrase or
        # the string is long (individual names rarely start with "the").
        if re.match(r"^\s*the\s+", s, re.I) and not re.search(
                r"consort|collaborat|group|network|investigat|committee|team|n3c|"
                r"initiative|program|alliance|registry", s, re.I):
            return len(s.split()) >= 4
        return True
    return False


def _clean_name_text(s):
    s = strip_html(s) or ""
    s = normalize_dashes(s)
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*,\s*", ", ", s)
    return s


def _is_initials_token(tok):
    return bool(re.match(r"^[A-Z]{1,3}$", tok))


def _split_suffix(tokens):
    suffix = None
    while tokens and tokens[-1].lower().rstrip(",") in SUFFIXES and len(tokens) > 1:
        suffix = tokens.pop().rstrip(",")
    return tokens, suffix


def _fmt_suffix(suffix):
    if not suffix:
        return None
    s = suffix.strip().rstrip(",")
    if s.lower().rstrip(".") in ("jr", "sr"):
        return s[0].upper() + s[1:].lower().rstrip(".") + "."
    return s.upper()


def parse_name(raw):
    """Parse a personal or group author string.

    Accepts "Family, Given", "Family, G. I., Jr.", "Given Family",
    "Given van der Family", and PubMed style "Family GI".
    Returns dict(given, family, suffix, is_group, name).
    """
    s = _clean_name_text(raw)
    if not s:
        return {"given": None, "family": None, "suffix": None, "is_group": False, "name": ""}
    if is_group_name(s):
        return {"given": None, "family": None, "suffix": None, "is_group": True, "name": s}
    given = family = suffix = None
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        family = parts[0]
        rest = parts[1:]
        extra = []
        for p in rest:
            if p.lower() in SUFFIXES:
                suffix = p
            else:
                extra.append(p)
        given = " ".join(extra) or None
        ftoks, fsuf = _split_suffix(family.split(" "))
        if fsuf and not suffix:
            suffix = fsuf
        family = " ".join(ftoks)
        if given:
            gtoks, gsuf = _split_suffix(given.split(" "))
            if gsuf and not suffix:
                suffix = gsuf
            given = " ".join(gtoks)
    else:
        tokens = s.split(" ")
        tokens, suffix = _split_suffix(tokens)
        if len(tokens) == 1:
            family = tokens[0]
        elif _is_initials_token(tokens[-1]) and not _is_initials_token(tokens[0]):
            # PubMed style: "Butzin-Dozier Z", "van der Laan MJ"
            family = " ".join(tokens[:-1])
            given = tokens[-1]
        else:
            i = len(tokens) - 1
            while i - 1 > 0 and tokens[i - 1].lower() in PARTICLES:
                i -= 1
            family = " ".join(tokens[i:])
            given = " ".join(tokens[:i]) or None
    suffix = _fmt_suffix(suffix)
    return {"given": given or None, "family": family or None, "suffix": suffix,
            "is_group": False, "name": display_name(given, family, suffix)}


def display_name(given, family, suffix=None):
    parts = [p for p in (given, family) if p]
    name = " ".join(parts)
    if suffix:
        name += " " + suffix
    return name


def given_initials(given):
    """'Zachary' -> 'Z.', 'John M.' -> 'J. M.', 'Lin-Chiun' -> 'L.-C.', 'MJ' -> 'M. J.'."""
    if not given:
        return ""
    # 'L.-C.' keeps its hyphen: split after a period unless a hyphen follows.
    g = re.sub(r"\.(?!-)", ". ", normalize_dashes(given))
    out = []
    for tok in g.split():
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("(") and tok.endswith(")"):
            continue  # nicknames like "(Seraphina)"
        if "-" in tok and all(tok.split("-")):
            out.append("-".join(p[0].upper() + "." for p in tok.split("-") if p))
        elif re.match(r"^[A-Z]{2,3}$", tok):
            out.extend(c + "." for c in tok)
        else:
            letter = next((c for c in tok if c.isalpha()), "")
            if letter:
                out.append(letter.upper() + ".")
    return " ".join(out)


def apa_name(author):
    """'Family, G. I.' with suffix as 'Colford, J. M., Jr.'; groups verbatim."""
    if not author:
        return ""
    if author.get("is_group"):
        return author.get("name") or ""
    family = author.get("family") or ""
    ini = given_initials(author.get("given"))
    out = family
    if ini:
        out += ", " + ini
    if author.get("suffix"):
        out += ", " + author["suffix"]
    return out


def name_key(s):
    """Comparison key for family names: ASCII, lowercase, no spaces/hyphens/dots."""
    if not s:
        return ""
    s = strip_accents(normalize_dashes(s)).lower()
    return re.sub(r"[^a-z]", "", s)


def is_me(author, me_cfg=None, my_orcid=None):
    """Match by ORCID when the author record has one; otherwise family name in
    the configured list AND given name/initial starting with the configured
    letter. Never matches Mary Dozier ("Dozier, M.")."""
    me_cfg = me_cfg or DEFAULT_ME
    if author.get("is_group"):
        return False
    a_orcid = normalize_orcid(author.get("orcid"))
    if a_orcid and my_orcid:
        return a_orcid == normalize_orcid(my_orcid)
    family = author.get("family")
    given = author.get("given")
    if not family or not given:
        return False
    fams = {name_key(f) for f in as_list(me_cfg.get("family_names"))}
    if name_key(family) not in fams:
        return False
    initial = (me_cfg.get("given_initial") or "Z").strip().upper()[:1]
    g = strip_accents(given).strip().lstrip(".( ").upper()
    return bool(g) and g.startswith(initial)


def make_author(given=None, family=None, name=None, orcid=None, suffix=None,
                is_group=None, me_cfg=None, my_orcid=None):
    """Normalized author dict used in publications.json."""
    if is_group is None:
        is_group = bool(name) and not family and is_group_name(name)
    if is_group:
        a = {"given": None, "family": None, "name": strip_html(name), "is_me": False,
             "is_group": True, "orcid": None, "suffix": None}
        a["apa"] = apa_name(a)
        return a
    given = _clean_name_text(given) or None if given else None
    family = _clean_name_text(family) or None if family else None
    if given:
        gtoks, gsuf = _split_suffix(given.replace(",", " ").split())
        if gsuf and not suffix:
            suffix = gsuf
        given = " ".join(gtoks) or None
    suffix = _fmt_suffix(suffix)
    a = {"given": given, "family": family,
         "name": display_name(given, family, suffix) or (name or ""),
         "is_me": False, "is_group": False, "orcid": normalize_orcid(orcid),
         "suffix": suffix}
    a["is_me"] = is_me(a, me_cfg, my_orcid)
    a["apa"] = apa_name(a)
    return a


def author_from_string(raw, orcid=None, me_cfg=None, my_orcid=None):
    p = parse_name(raw)
    if p["is_group"]:
        return make_author(name=p["name"], is_group=True)
    return make_author(given=p["given"], family=p["family"], suffix=p["suffix"],
                       orcid=orcid, me_cfg=me_cfg, my_orcid=my_orcid)


def family_keys(authors):
    return {name_key(a.get("family")) for a in authors or []
            if not a.get("is_group") and a.get("family")} - {""}


# --------------------------------------------------------------------------
# Organizations
# --------------------------------------------------------------------------

_SMALL_WORDS = {"of", "and", "the", "at", "for", "in", "on", "de", "la", "y"}
_ORG_FIXES = {
    "UNIVERSITY OF CALIFORNIA BERKELEY": "University of California, Berkeley",
    "UNIVERSITY OF CALIFORNIA, BERKELEY": "University of California, Berkeley",
    "UNIVERSITY OF CALIFORNIA AT BERKELEY": "University of California, Berkeley",
    "STANFORD UNIVERSITY": "Stanford University",
}


def pretty_org(name):
    """Title-case an ALL CAPS organization name from NIH RePORTER."""
    if not name:
        return name
    s = re.sub(r"\s+", " ", str(name)).strip()
    if s.upper() in _ORG_FIXES:
        return _ORG_FIXES[s.upper()]
    if any(c.islower() for c in s):
        return s
    words = []
    for i, w in enumerate(s.split(" ")):
        lw = w.lower()
        if i > 0 and lw in _SMALL_WORDS:
            words.append(lw)
        elif len(w) <= 4 and w.isalpha() and w in ("UCLA", "UCSF", "MIT", "NYU", "USC"):
            words.append(w)
        else:
            words.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    out = " ".join(words)
    m = re.match(r"^University of California (\w[\w ]*)$", out)
    if m and m.group(1) not in ("System",):
        out = "University of California, " + m.group(1)
    return out


def pretty_person(name):
    if not name:
        return name
    s = re.sub(r"\s+", " ", str(name)).strip()
    if s.isupper():
        s = " ".join("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-"))
                     for w in s.split(" "))
    return s
