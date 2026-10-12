# Maintenance

**You never have to do anything.** Every Monday at 14:00 UTC the "Update site"
workflow fetches your publications, citations, NIH grants, and GitHub repos,
rebuilds the site and `cv.pdf`, and publishes them. It also runs whenever you
change the site's code or content files (a commit that touches only `data/`,
`docs/`, `README.md`, or `SPEC.md` does not start a run). If a run fails, the
live site stays as it was and GitHub emails you.

The one real chore is outside this repository: the domain renews once a year
at WordPress.com (next on 2027-08-19). Keep auto-renew on and the card current.

## Optional edits

Every edit below is a small change to a YAML file in `content/`. You can make
it on github.com (open the file, click the pencil icon, **Commit changes**) and
the site rebuilds within about 5 minutes. If you edit on your laptop instead,
run `git pull` first, since the bot commits to `main` weekly, and commit only
the files you edited (for example `git add content`), not `data/`.

| To | Do this |
|---|---|
| Hide a paper | `content/publications.yml`: add its DOI (lowercase, in quotes) to the `hide:` list. |
| Add a contribution note | `content/publications.yml`, under `overrides:`, add the DOI as a key with `contribution: "..."`. If the DOI already has an entry there, add the line inside that entry instead. It shows under the entry and as an indented note in the CV. |
| Mark a preprint accepted | Same place, on the preprint's DOI: `status: accepted` and `status_note: "Journal Name"`. If the DOI already has an entry, add the two lines inside it. When the published version appears, it replaces the preprint and the note is dropped automatically. |
| Fix an author list | Same place, inside the DOI's entry: `group_author: "N3C Consortium"` adds a consortium that the sources leave out of the byline; `authors_source: crossref` (or `europepmc`) uses that source's author list as is. |
| Add a non-NIH grant | `content/funding.yml`: copy an entry under `manual:` and edit it. NIH grants appear on their own from NIH RePORTER; use `enrich:` to add a description to one. |
| Add a talk | `content/cv.yml`: copy the first entry under `presentations:` > `invited:` (or `presentations:` > `teaching:` for guest lectures; not the top-level `teaching:` list, which is for courses) and paste it above that entry, so the newest talk is listed first. |
| Add a trainee | `content/cv.yml`: add `{name: "...", role: "..."}` under `trainees:` > `current:`. Move it to `former:` when they leave. |
| Change the bio | `content/site.yml`: `person:` > `bio:` (home page) and `tagline:`. The CV summary is `summary_statement:` in `content/cv.yml`. |
| Describe a code repository | `content/site.yml`: under `software:` > `descriptions:`, add `RepoName: "One plain sentence."`. It replaces the description from GitHub on the home page. To correct the language label (GitHub counts rendered HTML, so an R demo can show as "HTML"), add `RepoName: R` under `software:` > `languages:`. |
| Add a paper the APIs cannot find | `content/publications.yml`: a DOI goes under `include_dois:`; a work with no DOI goes under `manual:` (copy an existing entry). |
| Force a rebuild | github.com/zbutzin/zdozier.com > **Actions** > **Update site** > **Run workflow**. |
| Preview locally | `make preview`, then open <http://localhost:8000> (Ctrl-C to stop). It builds from a scratch copy of `data/`, so `git status` stays clean. `make offline` and `make all` rewrite `data/*.json`; after using them, run `git checkout -- data` before `git pull` or committing. |

Each DOI appears once under `overrides:`, and `hide:`, `overrides:`, and
`manual:` each appear once in the file. To change a DOI that is already
listed, add lines inside its entry. YAML keeps only the last copy of a
repeated key, which would quietly undo the earlier settings, so the build
stops with an error instead.

Quote any text that contains a colon, and indent with spaces, not tabs. If a
YAML file has a mistake, that run fails, the live site does not change, and
the error is in the run's log under **Actions**. The message names the file
and line, for example `content/publications.yml line 112: mapping values are
not allowed here. Text that contains a colon must be in quotes.` A local
`make` command stops the same way, before anything in `data/` is rewritten.

## What the automatic messages mean

**"New publications detected (N)" issue.** The weekly run found works that
were not on the site before (usually because ORCID, OpenAlex, or Europe PMC
picked up a new paper). They are already live. The issue lists each one with
its DOI and which sources found it, plus copy-paste lines in case you want to
hide one or add a contribution note. Close it when you have looked. While it
is open, later finds are added to it as comments rather than opening new
issues.

**An API is down.** Each source is fetched separately. If one fails, the run
uses that source's last good snapshot from `data/raw/` and carries on, so the
site still builds. The same happens when a source is very slow (each has about
four minutes), rate-limits the run, or answers with far fewer records than
last time (an empty answer, or less than half of five or more records). A
smaller answer that comes back the same in the next run is accepted, so a
real change (say, you delete some GitHub repositories) shows up a week later. The status pill in the top bar ("synced N days ago") and
the table on the "How this site works" page (`/colophon/`) show how the last
run went for each source. A short outage needs nothing from you. If a source
has been on its cached copy for two weeks, the run opens one issue, "Site data
is going stale" (label `site-health`), that names the source and its error.
Later runs comment on it while the problem lasts, and the issue closes itself
after a run in which every source refreshes normally.

**The publication count drops sharply.** If a run would remove more than 15%
of the visible works at once (for example, an API returning a partial list),
it keeps the previous list instead, records why in `data/run_report.json`,
and opens (or comments on) the same "Site data is going stale" issue. While
the old list is kept, new papers are not added. If the next run comes back
with exactly the same shorter list, it is accepted automatically and the issue
closes. If the drop is real and you want it at once (you hid several papers,
say), add `accept_count_drop: 2026-10-12` (today's date) as a top-level line
in `content/publications.yml`. It is honored for 14 days, so it can stay in
the file afterwards and does nothing.

A paper that drops out of the sources for a week and then returns keeps its
original "first seen" date and is not reported as new a second time
(`data/seen.json` remembers every work the site has listed).

**"This scheduled workflow is disabled."** GitHub pauses scheduled workflows
in repositories with no activity for 60 days. Each run re-enables itself and
the weekly data commits count as activity, so this should not happen. If you
ever see that banner in the Actions tab, click **Enable workflow**.

**A failed-run email.** Open the run from the email to see which step failed.
The live site keeps the last good version until a run succeeds again. If the
log mentions a deprecated or unsupported action version, raise the `@vN`
numbers in `.github/workflows/update.yml` to the current major version shown
on each action's GitHub page (for example `actions/checkout`).

CI installs exact versions of the Python packages: `requirements.txt` pins
Jinja2, PyYAML, and requests, and `constraints-ci.txt` pins what they pull in
(MarkupSafe, urllib3, idna, charset-normalizer), so a new release of one of
them cannot break the run. Nothing needs updating on a schedule. If you ever
upgrade a package on purpose, change its line in the matching file.
