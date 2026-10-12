# zdozier.com build spec

Personal academic website for Zachary B. Dozier, PhD, MPH (Stanford Medicine).
Goal: a site that stays current with **zero routine maintenance**. Publications,
citation metrics, and federal funding refresh weekly from public APIs; the CV
(HTML and PDF) is regenerated from the same data on every build.

The site must look original. It should NOT resemble the "academicpages" /
Minimal Mistakes Jekyll theme (round avatar in a left sidebar, black masthead,
icon list of profile links). Structure and visual language below are our own.

## 1. Hard rules (apply to every file)

- **Writing style.** No em dashes (U+2014) anywhere in site copy, CV text, docs,
  or code comments meant for humans. Use periods, commas, colons, or parentheses.
  En dashes (U+2013) are fine for numeric/date ranges ("2024–2029"). Avoid
  AI-sounding phrasing: no "it's not just X, it's Y", no stacked triplets of
  adjectives, no "delve", "leverage", "cutting-edge", "passionate about", no
  hype. Plain, direct, academic-professional.
- **Name.** Current name is **Zachary B. Dozier** (short: Zach Dozier). He
  published earlier work as **Zachary Butzin-Dozier**. Citations of published
  work keep the name as published ("Butzin-Dozier, Z."). The site says once,
  near the top: "Formerly published as Zachary Butzin-Dozier."
- **"Me" detection in author lists** must never match his mother, Mary Dozier
  (co-author on the 2010 Archives of Pediatrics paper as "Dozier, M."). Match by
  ORCID when the source provides it; otherwise family name in
  {Butzin-Dozier, Butzin Dozier, Dozier} AND given name/initial starting with "Z".
- **Privacy.** Do not publish his phone number anywhere (site or PDF). Work email
  zdozier@stanford.edu and the work address (3145 Porter Drive, Palo Alto, CA
  94304) are fine. Do not send his email address to any API (no `mailto=`
  params) unless `content/site.yml: api.contact_email` is set (default null).
- **No third-party requests from the rendered site.** No CDNs, web fonts,
  analytics, icon fonts, or embeds. System font stacks only. Everything served
  from the repo. (This is a feature; the colophon mentions it.)
- **Python compatibility.** Code must run on the local machine's Python 3.8.8
  (anaconda: Jinja2 2.11.3, PyYAML 5.4.1, requests 2.25.1) AND on Python 3.12
  in CI with current versions. Dependencies: jinja2, pyyaml, requests only (plus
  stdlib). No 3.9+ syntax at runtime (`dict | dict`, `str.removeprefix`,
  `list[str]` outside `from __future__ import annotations`, `match`).
  Run things locally with `python3` (which is 3.8.8).
- **No file downloads** of third-party assets (fonts, icon packs, JS libs).
  Write what is needed (small inline SVGs, vanilla JS).
- Never create or modify anything outside `~/GitHub/zdozier.com/`. Never run
  `git push`, never create remote repos, never touch DNS or any account.

## 2. Repository layout and commands

```
content/            hand-curated YAML (rarely edited)
  site.yml          identity, bio, links, research themes, api settings
  cv.yml            education, appointments, honors, teaching, trainees,
                    talks, media, service, skills, summary statement
  publications.yml  overrides: contribution notes, manual entries, hides,
                    status notes, links, topic rules, featured flags
  funding.yml       non-federal grants + enrichment of auto-fetched grants
data/               machine-generated, committed (history + offline fallback)
  raw/<source>.json last good raw response per source
  publications.json merged, deduped, classified works (contract in section 3)
  grants.json       merged funding (contract in section 4)
  metrics.json      author-level metrics (section 3.3)
  software.json     public GitHub repos (section 5)
  run_report.json   per-run status (section 6)
scripts/
  fetch.py          network: query all sources, write data/raw/*.json
  merge.py          offline: raw + content/*.yml -> data/*.json
  build.py          offline: data + content + templates -> _site/
  pdf.py            _site/cv/index.html -> _site/cv.pdf via headless Chrome
  sources/          one module per source (orcid, openalex, crossref,
                    europepmc, reporter, github)
  common.py         shared helpers (http with retry, name matching, DOI norm)
templates/          Jinja2 templates
static/             css/, js/, img/ (copied verbatim into _site/)
tests/              unittest tests (python3 -m unittest discover tests)
_site/              build output (gitignored)
```

Canonical commands (Makefile wraps these):

```
python3 scripts/fetch.py      # network
python3 scripts/merge.py      # offline
python3 scripts/build.py      # offline
python3 scripts/pdf.py        # needs Chrome; CHROME_PATH env overrides
python3 -m unittest discover tests
python3 -m http.server 8000 -d _site    # preview
```

`make all` = fetch, merge, build, pdf. `make offline` = merge, build, pdf.

## 3. Publications

### 3.1 Sources (identity from `content/site.yml: identity`)

| source | query | role |
|---|---|---|
| ORCID public API v3.0 | `pub.orcid.org/v3.0/{orcid}/works` (+ bulk `/works/{putcodes}` for details) | primary, author-curated list. Stanford's profile system already pushes into ORCID |
| OpenAlex | works filtered by `author.orcid:{orcid}` (paginate with cursor); author by `authors/orcid:{orcid}` | discovery + enrichment: authorships with ORCIDs, venue, citations, OA links, counts_by_year, h-index |
| Crossref | `works/{doi}` for each DOI; also `works?filter=orcid:{orcid}` | canonical metadata (given/family author names, volume, issue, pages, dates, `relation` preprint links) |
| Europe PMC | REST search `AUTH:"Butzin-Dozier Z" OR AUTHORID:"{orcid}" OR AUTH:"Dozier ZB"` (resultType=core) | catches PubMed ahead-of-print and preprints before ORCID sync; PMID/PMCID |
| manual | `content/publications.yml: manual` | in press without DOI, chapters, reports |
| forced | `content/publications.yml: include_dois` | DOIs that must be included even if discovery misses them |

Discovery = union of DOIs from ORCID, OpenAlex, Crossref-ORCID filter, Europe
PMC (name hits must pass the "me" rule in section 1), plus forced DOIs. Every
work records `sources` (which sources found it). Works found ONLY by name search
(not ORCID-linked anywhere) are included but listed in `run_report.warnings`.

### 3.2 Merge rules

- DOI normalization: lowercase, strip `https://doi.org/`, `doi:`, whitespace.
- Preprint detection by DOI prefix or source type: `10.1101/` (medRxiv/bioRxiv),
  `10.64898/` (medRxiv's 2026+ prefix), `10.2196/preprints.` (JMIR Preprints),
  `10.21203/` (Research Square), `10.2139/ssrn` (SSRN), `10.48550/` (arXiv),
  `10.31219/` (OSF), `10.20944/` (Preprints.org); OpenAlex `type: preprint`.
  ORCID type labels are unreliable (some medRxiv items are typed journal-article).
- Version linking (preprint -> published), in priority order:
  1. explicit `content/publications.yml: same_as` groups;
  2. Crossref `relation.is-preprint-of` / `has-preprint`;
  3. JMIR rule: `10.2196/preprints.N` <-> `10.2196/N`;
  4. fuzzy title match (normalized: lowercase, strip punctuation/HTML, collapse
     spaces) ratio >= 0.88 via difflib, publication years within 3, and >= 2
     shared author family names.
  The published version is the primary record; linked preprints go into
  `versions`. Multiple non-published versions of the same paper (medRxiv +
  SSRN + conference abstract) collapse to one record (prefer medRxiv).
- Field precedence for the primary record: manual override > Crossref >
  OpenAlex > Europe PMC > ORCID. Titles: strip HTML tags, fix ALL CAPS titles
  to sentence case only if the whole title is uppercase.
- Authors: prefer Crossref given/family. Fall back to OpenAlex
  `raw_author_name`/display_name (family = last token, keeping hyphenated and
  particles like "van der"), then Europe PMC `authorString`. Consortium/group
  authors ("N3C Consortium", "the National COVID Cohort Collaborative (N3C)
  Consortium", etc.) are kept with `is_group: true`. When Crossref's list is
  more than twice as long as Europe PMC's and Europe PMC names a group,
  Crossref has deposited the consortium's member list: use Europe PMC's
  byline. When Crossref drops a trailing group that Europe PMC keeps (same
  people otherwise), add the group back. Exact repeats (same family and full
  given name) are dropped. Per-DOI overrides: `authors_source:
  crossref|europepmc|openalex|orcid` pins one source's list, `group_author:`
  appends a group no source reports, `authors:` replaces the list.
- Role: `first` if he is author 1; `senior` if he is the last non-group author
  and author_count >= 3; else `coauthor`. Override via `role` in overrides.
- Type: `article | review | conference | preprint | chapter | report | other`.
  `review` from overrides or OpenAlex type `review` (e.g. Epidemiologic Reviews
  ring trial paper). NeurIPS -> `conference`.
- Status: `published | accepted | under-review | preprint`. Published if a
  non-preprint DOI exists with a publication date. Overrides may set
  `status: accepted` + `status_note: "Diabetes, Obesity and Metabolism"` on a
  preprint DOI; when the published version later appears the published record
  wins automatically and the note is dropped.
- Topics: from `topic_rules` (keyword regex over title + venue, case-insensitive)
  plus per-work `topics` overrides. Topic ids are fixed slugs defined in
  `content/publications.yml: topics`.
- Hidden: `hide: [doi, ...]` removes works (and from metrics shown on site).
- Sanity guard: if the merged count of visible works drops by > 15% relative to
  the previous `data/publications.json`, keep the previous file, write the
  failure into `run_report.json`, and exit 0 (site still builds from last good).

### 3.3 `data/publications.json` contract

```json
{
  "generated": "2026-10-09T20:00:00Z",
  "works": [
    {
      "id": "10.1186/s12916-024-03655-x",          // DOI, or "manual:<slug>"
      "doi": "10.1186/s12916-024-03655-x",          // null for manual w/o DOI
      "title": "SSRI use during acute COVID-19 and risk of Long COVID among patients with depression",
      "authors": [
        {"given": "Zachary", "family": "Butzin-Dozier", "name": "Zachary Butzin-Dozier",
         "is_me": true, "is_group": false, "orcid": "0000-0001-6419-0008"}
      ],
      "author_count": 13,
      "my_position": 1,                              // 1-based, null if not found
      "role": "first",                               // first | senior | coauthor
      "year": 2024,
      "date": "2024-11-05",                          // best available, may be YYYY or YYYY-MM
      "venue": "BMC Medicine",
      "volume": "22", "issue": "1", "pages": "445",
      "type": "article",
      "status": "published",
      "status_note": null,
      "url": "https://doi.org/10.1186/s12916-024-03655-x",
      "oa_url": "https://...pdf",                    // OpenAlex best_oa_location, or null
      "pmid": "39380080", "pmcid": "PMC...",         // or null
      "cited_by": 9,                                 // OpenAlex, or null
      "versions": [{"doi": "10.1101/2024.02.05.24302352", "server": "medRxiv",
                    "url": "https://doi.org/10.1101/2024.02.05.24302352", "year": 2024}],
      "topics": ["covid"],
      "contribution": null,                          // middle-author role statement
      "featured": true,
      "code_url": null,
      "sources": ["orcid", "openalex", "crossref", "europepmc"],
      "first_seen": "2026-10-09"                     // carried forward across runs
    }
  ]
}
```

Sorted by date descending. `first_seen` persists across runs (read from the
previous file) so "new since last run" is computable.

`data/metrics.json`: `{generated, works_count, cited_by_count, h_index,
i10_index, counts_by_year: [{year, works_count, cited_by_count}],
by_year_role: [{year, first_or_senior, coauthor}], source: "openalex"}`.
Visible-works counts are computed from publications.json; citation totals and
h-index come from OpenAlex author summary_stats.

## 4. Funding: `data/grants.json`

Sources: NIH RePORTER v2 (`api.reporter.nih.gov/v2/projects/search`, criteria
by `pi_profile_ids` once known, else `pi_names`, plus `core_projects` from
config), ORCID `/fundings`, and `content/funding.yml` (manual + enrichment keyed
by core project number). Group RePORTER fiscal-year records by core project
(`1K01AI182501-01` -> `K01AI182501`). Do not publish dollar amounts unless
`funding.yml: show_amounts: true` (default false).

```json
{"generated": "...", "grants": [
  {"id": "K01AI182501", "title": "latest title from RePORTER", "funder": "NIH",
   "agency": "NIAID", "mechanism": "K01", "role": "PI",
   "start": "2024-09", "end": "2029-08", "status": "active",
   "institutions": ["University of California, Berkeley", "Stanford University"],
   "url": "https://reporter.nih.gov/project-details/<latest appl_id>",
   "description": null, "source": "nih_reporter", "pis": ["Zachary Butzin-Dozier"],
   "amount_total": 406461, "fiscal_years": [2024, 2025, 2026]}
]}
```
`status` computed at merge time: `active` if end >= today, else `completed`.
Manual grants use the same shape with `source: "manual"`.

## 5. Software: `data/software.json`

Public repos of `identity.github_user` via GitHub REST (`GITHUB_TOKEN` header if
env var set). Keep name, description, html_url, language, stargazers_count,
pushed_at, topics. `content/site.yml: software.include` (ordered list) and
`software.exclude` control what is shown; default shows non-fork repos not
excluded. `software.descriptions: {RepoName: "text"}` replaces a repo's
GitHub description on the site. The site does not label forks.

## 6. Run report: `data/run_report.json`

```json
{"started": "...", "finished": "...", "ok": true,
 "sources": {"orcid": {"ok": true, "count": 44, "error": null, "used_cache": false}, ...},
 "new_works": [{"id": "...", "title": "...", "venue": "...", "sources": [...]}],
 "removed_works": [...], "new_grants": [...], "warnings": ["..."]}
```
fetch.py: each source wrapped independently. On failure, keep the previous
`data/raw/<source>.json`, record `used_cache: true` and the error. A source
failing never fails the run. Network: requests with timeout 30s, 3 retries
with backoff, polite User-Agent "zdozier.com-site-builder/1.0
(+https://zdozier.com)". RePORTER: <= 1 request/second.

## 7. Site

### 7.1 Pages
- `/` Home: name, one-line research statement, affiliations, "Formerly published
  as" line, portrait (rounded rectangle, not a circle), short bio, link row
  (Stanford profile, ORCID, OpenAlex, Google Scholar if configured, GitHub,
  Email, CV PDF). Stat strip (visible publications, citations, h-index, active
  grants) with a small citations-per-year bar sparkline. Research themes (from
  site.yml) each with 2 to 3 representative papers pulled by topic (featured
  first, then most recent). Recent publications (5 newest, automatic). Current
  funding (automatic). Software (automatic). Contact.
- `/publications/`: summary line ("N works since <earliest public year>") +
  chart (publications per year, stacked first/senior vs co-author; same
  public-works rule as `metrics.by_year_role`, nothing dated after the
  current year; the latest year and the 7 before it, with a caption line
  "Plus N earlier works (years)." for anything older). Filter chips (All, First or senior author, each
  topic, Preprints), text search, and type filter, all client-side vanilla JS
  with progressive enhancement (full list works without JS). Grouped by year.
  Each entry: title (links to DOI), authors (his name bold; long lists
  truncated APA-style with a "show all" toggle), venue (italic) + year, role
  tag for first/senior, status tag for preprint/accepted/under review, badges:
  DOI, PDF (oa_url), preprint version(s), PubMed, code, citations count.
  Collapsible "My contribution" when `contribution` is set. Downloads:
  `/publications.bib` (BibTeX, all visible works), `/publications.json`.
- `/funding/`: active and completed grants, automatic + manual.
- `/team/`: current and former trainees from cv.yml (names, role, years if known).
- `/cv/`: full CV as HTML (print-optimized) + "Download PDF" (`/cv.pdf`). Header
  states "Updated <build date>. Generated automatically from ORCID, OpenAlex,
  Crossref, Europe PMC, and NIH RePORTER."
- `/colophon/` ("How this site works"): pipeline diagram (inline SVG: sources
  -> fetch -> data snapshots -> build -> site + CV PDF -> GitHub Pages, weekly
  schedule), last run time and per-source status table from run_report.json,
  link to source repo. Short and factual.
- `404.html`, `sitemap.xml`, `robots.txt`, `CNAME` (zdozier.com),
  `/feed.xml` (Atom: newest 20 publications), favicon (SVG monogram).
- `<head>`: title, meta description, canonical (site.url), OpenGraph, JSON-LD
  `Person` with `sameAs` (ORCID, Stanford profile, OpenAlex, GitHub, Scholar if
  set), `alternateName: "Zachary Butzin-Dozier"`, affiliation, jobTitle.

### 7.2 Visual design (original; data-forward academic)
- Top bar: wordmark "Zachary B. Dozier" (left), nav right: Research,
  Publications, Funding, Team, CV. A small status pill "synced <N> days ago"
  (computed at build time) linking to /colophon/. Collapses to a simple
  disclosure menu on mobile (no JS required: `<details>`).
- Typography: headings in a system serif stack (`"Iowan Old Style", "Palatino
  Linotype", Palatino, "TeX Gyre Pagella", "P052", "URW Palladio L", Charter,
  "Bitstream Charter", Georgia, serif`), body in system sans (`-apple-system,
  BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Liberation
  Sans", "DejaVu Sans", "Noto Sans", sans-serif`), all data/metadata (years,
  DOIs, counts, badges, dates) in system mono (`ui-monospace, "SF Mono",
  SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", "Liberation Mono",
  monospace`) with `font-variant-numeric: tabular-nums`. The Linux names are
  for the CI runner that prints cv.pdf (the workflow installs fonts-texgyre
  and fonts-liberation, and a failed install does not fail the run).
- Color: light theme paper background (~#fbfaf8), ink text (~#17181c), muted
  (~#5d6370), hairline rules (~#e5e2dc), accent Stanford cardinal (#8c1515) used
  sparingly (links on hover, active nav, role tags). Charts use a neutral ink +
  one accent; follow the `dataviz` skill (invoke it before writing chart code).
  Full dark theme via `prefers-color-scheme: dark` (accent lightens to keep
  contrast). WCAG AA contrast everywhere.
- Layout: single main column max ~760px for reading, home hero as two columns
  on desktop (text + portrait), stacking on mobile. Publications use a left
  year gutter (mono) on wide screens. Generous whitespace, hairline dividers, no
  cards with heavy shadows, no gradients, no emoji.
- Charts: static inline SVG generated in Python at build time (no JS charting),
  with `<title>`/`aria-label` text alternatives and a visually hidden data table.
- Accessibility: semantic landmarks, skip link, visible focus rings,
  `prefers-reduced-motion`, alt text, labels on filter inputs.
- Performance: no blocking JS, one CSS file, one small JS file (deferred),
  images with width/height, `loading="lazy"` below the fold.

## 8. CV (HTML + PDF)
Mirrors the Word CV's section order and APA-like style:
Header (name with degrees, website, email, work address; "Updated <date>";
"Formerly published as Zachary Butzin-Dozier") · Summary Statement · Academic
Appointments · Education · Funding · Honors and Awards · Publications ·
Teaching · Trainees · Presentations · Media Coverage · Peer Review Activities ·
Skills.

Publications subsections, each numbered from 1, reverse chronological:
Peer-Reviewed Journal Articles (Original Research) [type article] ·
Peer-Reviewed Review Articles [type review] · Peer-Reviewed Conference Papers
[conference] · Accepted and In Press [status accepted, if any not already
published] · Preprints and Manuscripts Under Review [preprint/under-review] ·
Book Chapters [chapter] · Non-Peer-Reviewed Publications [report].
Citation format (APA 7-like): `Family, G. I., Family, G. I., & Family, G. I.
(Year). Title. *Venue*, *Volume*(Issue), Pages. https://doi.org/...`. More than
20 authors: first 19, ellipsis, last. His name in bold. Middle-author entries
with a contribution statement get an asterisk after his name and the statement
as an indented note beneath (as in the Word CV).

PDF: `scripts/pdf.py` prints `_site/cv/index.html` with headless Chrome
(`--headless=new --no-pdf-header-footer --print-to-pdf`), US Letter, margins
0.75in, page numbers via CSS `@page` margin boxes where supported. Chrome
discovery: `CHROME_PATH` env, macOS `/Applications/Google Chrome.app/...`,
then `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`
on PATH. Exit non-zero if Chrome is missing and `CI=true`; warn otherwise.

## 9. Automation (GitHub Actions)
Workflow `.github/workflows/update.yml`: triggers weekly cron (Mondays 14:00 UTC),
`workflow_dispatch`, and `push` to main (ignore pushes that only touch
`data/**` from the bot to avoid loops; note GITHUB_TOKEN pushes do not trigger
workflows anyway). Steps: checkout, setup-python 3.12, pip install
-r requirements.txt, `make all` (Chrome preinstalled on ubuntu-latest), run
tests, commit changed `data/**` back as github-actions[bot] ("data: weekly
refresh"), deploy `_site/` with actions/upload-pages-artifact + deploy-pages.
If `run_report.new_works` is non-empty, open one GitHub issue listing them (so he
gets an email and can optionally hide one or add a contribution note). If a
source has been failing (cached copy in use) for 14 days or more, or the
sanity guard tripped, open one `site-health` issue ("Site data is going
stale"; later runs comment on it) and close it after a run in which every
source refreshes normally (`scripts/ci_issue.py --health`, `--health-state`).
Hand-edited YAML is read with a loader that rejects repeated keys, and a YAML
error stops merge/build with a one-line message before `data/` is rewritten. Keep the
schedule alive past GitHub's 60-day inactivity auto-disable by calling the
"enable workflow" REST endpoint each run (`actions: write`). Minimal
permissions per job.
