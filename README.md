# zdozier.com

Source for [zdozier.com](https://zdozier.com), the academic website and CV of
Zachary B. Dozier, PhD, MPH (Stanford Medicine). Formerly published as Zachary
Butzin-Dozier.

The site keeps itself current. Every Monday a GitHub Actions job pulls
publications, citation counts, NIH funding, and public code repositories from
public APIs, rebuilds every page and the CV (HTML and PDF) from that data, and
publishes the result on GitHub Pages. There is nothing to do week to week.
See [docs/MAINTENANCE.md](docs/MAINTENANCE.md) for the optional edits, and
[docs/SETUP.md](docs/SETUP.md) for the one-time move from WordPress.

## How it updates

```
  every Monday 14:00 UTC, on pushes that change code or content, or by hand (Actions > Run workflow)

  ORCID, OpenAlex, Crossref,
  Europe PMC, NIH RePORTER, GitHub
            |
            v
  scripts/fetch.py  ------------>  data/raw/*.json
  (network; a source that fails     (last good response from each source)
   keeps its previous copy)                |
                                           v
  content/*.yml  ----------------->  scripts/merge.py
  (your hand edits)                        |
                                           v
                                    data/*.json  (publications, grants,
                                           |      metrics, software, run report,
                                           |      seen list; committed back)
                                           v
                                    scripts/build.py + scripts/pdf.py
                                           |
                                           v
                                    _site/  (pages, cv.pdf, publications.bib)
                                           |
                                           v
                                    GitHub Pages  ->  https://zdozier.com

  New papers found? The job opens one GitHub issue listing them (you get an email).
  A source failing for two weeks? It opens one "Site data is going stale" issue.
```

Each run commits the refreshed `data/` folder when anything changed, so the
repository history is also a record of what each source reported on each date. If a run fails for
any reason, the live site stays as it was and GitHub emails you.

## Commands

All of these run from the repository folder with the `python3` already on
your laptop (Python 3.8 with Jinja2, PyYAML, and requests, as in Anaconda).
GitHub runs the same commands on Python 3.12.

| Command | What it does |
|---|---|
| `make all` | fetch, merge, build, pdf (needs internet) |
| `make preview` | merge, build, pdf from a scratch copy of `data/`, then serve it; `data/` is not changed |
| `make offline` | merge, build, pdf from the committed `data/` snapshot (rewrites `data/*.json`) |
| `make fetch` | `python3 scripts/fetch.py`: query the APIs, write `data/raw/` |
| `make merge` | `python3 scripts/merge.py`: `data/raw/` + `content/` to `data/*.json` |
| `make build` | `python3 scripts/build.py`: write the site to `_site/` |
| `make pdf` | `python3 scripts/pdf.py`: print `_site/cv.pdf` with Google Chrome |
| `make test` | `python3 -m unittest discover tests` |
| `make serve` | preview at http://localhost:8000 (Ctrl-C to stop) |
| `make clean` | delete `_site/` and Python caches |

To preview a change before pushing it: `make preview`. It leaves `data/` alone,
so the next `git pull` cannot clash with the bot's weekly data commit. If you
ran `make offline` or `make all`, run `git checkout -- data` before pulling or
committing.

If Chrome is somewhere unusual, point the PDF step at it with
`CHROME_PATH=/path/to/chrome make pdf`. Without Chrome, the PDF step prints a
warning and the rest of the site still builds.

## Where to edit things

| To change | Edit |
|---|---|
| Bio, tagline, profile links, research themes, which GitHub repos appear | `content/site.yml` |
| Appointments, education, honors, teaching, trainees, talks, media, peer review, skills, CV summary | `content/cv.yml` |
| Hide a paper, contribution notes, "accepted" notes, featured papers, topics, works without a DOI | `content/publications.yml` |
| Grants that are not in NIH RePORTER, grant descriptions | `content/funding.yml` |
| Page structure and wording | `templates/` |
| Colors, fonts, spacing | `static/css/` |
| Portrait and icons | `static/img/` |

Everything in `data/` is regenerated on every run, so edits there are
overwritten. Put corrections in `content/` instead. The build rules and data
formats are written down in [SPEC.md](SPEC.md).

Note that this repository is public, including `content/` and `data/`.

## Why GitHub Pages instead of WordPress

The site is plain files built by a short Python pipeline, so it can be
regenerated from data rather than edited by hand. GitHub Pages hosts it and
GitHub Actions runs the weekly update at no cost for a public repository.
There are no plugins or themes to keep patched, and the pages load nothing
from third parties (no web fonts, analytics, or embeds). The domain stays
registered at WordPress.com; only its DNS records point here.
