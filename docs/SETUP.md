# One-time setup: moving zdozier.com from WordPress.com to GitHub Pages

This is done once. After it, the site updates itself (see
[MAINTENANCE.md](MAINTENANCE.md)).

Assumptions: GitHub user `zbutzin`, public repository `zdozier.com`, and the
domain `zdozier.com` registered at WordPress.com (registrar Automattic,
nameservers ns1.wordpress.com, ns2.wordpress.com, and ns3.wordpress.com,
registered 2026-08-19, expires 2027-08-19). Everything below is free except
the yearly domain renewal you already pay.

Plan on 30 to 60 minutes of hands-on time, plus some waiting for DNS
(usually under a few hours). Menu names on github.com and WordPress.com
change from time to time; where this guide says "check the current menu
wording", the exact label may differ slightly from what is written here.

Before you start, note that the repository will be public, including
`content/` and `data/`. Nothing private is in them (the phone number is
deliberately left out everywhere). Keep it that way when you edit.

---

## Part 1. Put the code on GitHub

**1. Create an empty repository.**
Go to <https://github.com/new>. Owner: `zbutzin`. Repository name:
`zdozier.com`. Visibility: Public. Leave "Add a README", ".gitignore", and
"license" all off, so the repository starts empty. Click **Create repository**.

**2. Push the code.** In Terminal:

```sh
cd ~/GitHub/zdozier.com
git init -b main            # harmless if the folder is already a git repository
git add -A
git status                  # look over the list; _site/ should not be in it
git commit -m "Initial version of zdozier.com"
git remote add origin https://github.com/zbutzin/zdozier.com.git
git push -u origin main
git config pull.rebase true   # later pulls replay your edits on top of the bot's data commits
```

If `git push` asks for a password, GitHub wants a token rather than your
account password. Either run `gh auth login` once if you have the GitHub CLI,
use GitHub Desktop, or, if you already use SSH keys with GitHub, switch the
remote with
`git remote set-url origin git@github.com:zbutzin/zdozier.com.git` and push
again.

**3. Expect the first run to fail at the deploy step.** The push starts the "Update site" workflow
(repository > **Actions** tab). Its `deploy` job will probably fail this first
time with a message that Pages is not enabled. That is expected. The `build`
job should still succeed and commit a refreshed `data/` folder as
`github-actions[bot]`.

From now on, run `git pull` before editing locally, because the bot commits
to `main` every week. Commit only the files you changed (for example
`git add content`), not `data/`, and preview with `make preview`, which leaves
`data/` untouched (see [MAINTENANCE.md](MAINTENANCE.md)).

## Part 2. Turn on GitHub Pages

**4. Set the Pages source.** Repository > **Settings** > **Pages**. Under
"Build and deployment", set **Source** to **GitHub Actions**. Nothing else on
this page yet.

**5. Check workflow permissions (usually no change needed).** Repository >
**Settings** > **Actions** > **General**.
- "Actions permissions" should allow actions created by GitHub (the default,
  "Allow all actions and reusable workflows", is fine).
- "Workflow permissions": the workflow asks for exactly the permissions it
  needs, so the default normally works. If a later run fails at the
  "Commit refreshed data" step with a 403 or "permission denied" error,
  choose **Read and write permissions** here and click **Save**.

**6. Run the workflow by hand.** **Actions** > **Update site** (left
sidebar) > **Run workflow** > branch `main` > **Run workflow**. It takes about
5 minutes. When it finishes, `build`, `deploy`, and `keepalive` should all have
green checks. `notify` is skipped unless new papers were found.

**7. Look at the site.** The `deploy` job shows its URL, which will be
<https://zbutzin.github.io/zdozier.com/> until the custom domain is set.
The site is built to live at the root of zdozier.com, so at this temporary
address some styles or links may not work. Check that the pages have the
right content; the look will be right once the domain is connected.

## Part 3. Point zdozier.com at GitHub Pages

Do steps 8 to 11 in order, with 9 and 10 in one sitting. The short gap
described under "What to expect" starts at step 10 and ends at step 11.

**8. Verify the domain with GitHub.** This stops anyone else's GitHub
repository from claiming zdozier.com.
1. On github.com, click your profile picture > **Settings** > **Pages** (in
   the "Code, planning, and automation" section of the left sidebar) >
   **Add a domain**. Enter `zdozier.com` and click **Add domain**.
2. GitHub shows a TXT record to create. Its name is
   `_github-pages-challenge-zbutzin` (that is, the full name
   `_github-pages-challenge-zbutzin.zdozier.com`) and its value is a code.
   Leave the page open.
3. In another tab, sign in to WordPress.com and open the DNS records for
   zdozier.com: Hosting Dashboard > **Domains** (<https://wordpress.com/domains/manage>)
   > **zdozier.com** > **Settings** > **DNS records** (check the current menu
   wording). Add a record: type
   **TXT**, name `_github-pages-challenge-zbutzin` (WordPress.com adds
   `.zdozier.com` for you), value: the code from GitHub. Save.
4. Back on GitHub, click **Verify**. If it says the record was not found,
   wait 10 to 30 minutes and click Verify again. Leave this TXT record in
   place permanently.

**9. Set the custom domain on the repository.** Repository > **Settings** >
**Pages** > **Custom domain**: enter `zdozier.com` (without `www`) and click
**Save**. The DNS check will fail at first; that is fine, because the
records are changed in the next step. GitHub keeps checking.

(The repository also contains a `CNAME` file. For sites deployed by GitHub
Actions, GitHub ignores that file; this setting is what counts.)

**10. Change the DNS records at WordPress.com.** In the same WordPress.com DNS
records page as step 8:
1. Do not try to delete WordPress.com's own **A** records for the root
   domain. They are marked "Handled by WordPress.com" and cannot be edited;
   adding your own A records in the next step replaces them automatically.
   If WordPress.com first asks you to change the site's primary address away
   from zdozier.com, do so (pick the `butzin-dozierlab.wordpress.com`
   address), then see step 13 about making the old site private.
2. Add four **A** records, name `@` (or blank), one per address:
   `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
3. Add four **AAAA** records, name `@` (or blank), one per address:
   `2606:50c0:8000::153`, `2606:50c0:8001::153`, `2606:50c0:8002::153`,
   `2606:50c0:8003::153`
4. For `www`: delete any existing `www` record, then add a **CNAME** record
   with name `www` and value `zbutzin.github.io`.
5. Leave every other record alone, especially any **MX** records (email) and
   the TXT record from step 8. Do not add a wildcard (`*`) record pointing to
   GitHub.

WordPress.com may warn that the domain will no longer point to your
WordPress.com site. That is the intended result.

**11. Turn on HTTPS.** Back in repository > **Settings** > **Pages**, wait
for the DNS check to show the domain is configured correctly. GitHub then
requests a certificate from Let's Encrypt. When the **Enforce HTTPS** box
becomes available, tick it. If it stays greyed out for more than a day,
remove the custom domain, save, add `zdozier.com` again, and save; that
restarts the certificate request.

### What to expect during the switch

- DNS changes reach most of the internet within minutes to a few hours,
  depending on how long resolvers cache the old records. Some visitors, and
  possibly your own laptop, can see the old WordPress site for a while after
  the change. In rare cases it takes longer everywhere (WordPress.com's
  stated maximum is 72 hours).
- Between the moment DNS points to GitHub and the moment GitHub's certificate
  is issued (often under an hour, occasionally up to a day), visiting
  `https://zdozier.com` can show a certificate warning or a GitHub "404" page.
  The old WordPress site told browsers to always use https, so any browser
  that visited it in the last year will show a certificate error you cannot
  click past until GitHub's certificate is issued. Check progress with
  `curl -I http://zdozier.com` or a browser that never visited the old site,
  not your usual browser.
- So there is a short window, typically under an hour, where the site may be
  unreachable or look broken for some visitors. Doing the switch on a weekend
  evening keeps the impact small.
- You can watch progress from Terminal:

  ```sh
  dig +short zdozier.com            # should list the four 185.199.x.153 addresses
  dig +short AAAA zdozier.com       # the four 2606:50c0:... addresses
  dig +short www.zdozier.com        # zbutzin.github.io. followed by GitHub addresses
  dig +short TXT _github-pages-challenge-zbutzin.zdozier.com
  ```

- To undo: delete the A, AAAA, and `www` CNAME records you added in step 10
  and use "Restore default DNS records" on the domain's page at WordPress.com.
  The old site returns once DNS catches up.

## Part 4. WordPress.com afterwards

**12. Keep domain auto-renew on.** WordPress.com > your domains > zdozier.com:
make sure auto-renew is **on** and the card on file is current. The domain
expires 2027-08-19 and renews yearly. This is the one thing that must not
lapse: if the domain expires, the site goes offline and someone else could
register the name.

**13. Downgrade or cancel the WordPress.com site plan, not the domain.** After
the new site has been live for a week or so:
- Optional: keep a copy of the old site first (in WordPress.com, the site's
  Tools > Export, or similar; check the current menu wording).
- Then make the old site private (the site's Settings > General > Privacy,
  set to Private; check the current menu wording) or delete the site once the
  export is saved. Either way the domain registration and its DNS records are
  unaffected. Otherwise the old pages, under the old name, stay public at
  `butzin-dozierlab.wordpress.com`.
- Cancel or downgrade the **site plan** (the WordPress.com hosting
  subscription). Do **not** cancel the **domain registration**.
- If WordPress.com asks during cancellation whether to keep the domain,
  choose to keep it. If the domain came free with the plan, the cancellation
  screen may say the domain's price will be deducted from any refund, or that
  the domain now renews at its own price. Read that screen before
  confirming.
- Afterwards, open the DNS records page once more to confirm the GitHub
  records from steps 8 and 10 are still there and still editable.

**14. Optional: move the domain to another registrar.** There is no technical
need to. If you want to, the 60-day transfer lock after registration ends on
2026-10-18. A transfer usually adds one year to the expiration date. Moving
also means leaving the WordPress.com nameservers, so recreate the same records
at the new DNS host (the A, AAAA, and `www` CNAME records, the GitHub TXT
record, and any MX records) before or during the transfer.

## Part 5. Link the site from your other profiles

**15. Stanford Profiles (CAP).** Go to <https://profiles.stanford.edu>, sign
in with your SUNet ID, and edit your profile (check the current menu wording).
In the **Links** section:
- Add "Personal website": `https://zdozier.com`
- If you have an uploaded CV document, consider replacing it with a link
  "Curriculum Vitae (auto-updated)": `https://zdozier.com/cv.pdf`, so the
  profile never shows a stale CV.

Stanford's profile system already pushes your publications into ORCID, and
ORCID is the main source for this site, so the two publication lists stay
consistent without extra work. While you are there, copy your public profile
URL from the browser and compare it with the Stanford link in
`content/site.yml` (currently `https://med.stanford.edu/profiles/zachary-dozier`).
Fix the file if they differ. While editing, check that the Bio uses "Zach
Dozier" (it still began "Zach Butzin-Dozier is an Assistant Professor..." in
October 2026); the home page bio in `content/site.yml` has the current text.

**16. ORCID.** Sign in at <https://orcid.org> and open your record.
- **Websites & social links**: add `https://zdozier.com` (title "Personal
  website") and your Stanford profile URL.
- **Other names**: add "Zachary Butzin-Dozier" if it is not there.
- Check that your works are visible to **Everyone**. The site reads only the
  public ORCID record, so works set to "Trusted parties" or "Only me" are
  skipped.
- Optional: fill in **Education** and **Funding**. ORCID funding entries feed
  the site's funding page too. NIH grants already come from NIH RePORTER, and
  the non-NIH grants are in `content/funding.yml`, so after adding a grant to
  ORCID, check the funding page once for a duplicate.

**17. Optional: Google Scholar.** If you have a Google Scholar profile, copy its
URL (it looks like `https://scholar.google.com/citations?user=...`) into
`content/site.yml`, on the Google Scholar line under `links:`, replacing
`url: null` with `url: "<the URL>"`. The site only links to Scholar; it does
not read data from it. The LinkedIn line works the same way.

**18. GitHub.** Profile picture > **Settings** > **Public profile** >
**Website**: `https://zdozier.com` > **Update profile**. Then, on the
zdozier.com repository page, click the gear next to **About** and set
**Website** to `https://zdozier.com`.

## Part 6. Final check

- <https://zdozier.com> loads with a padlock, and `http://zdozier.com` and
  `https://www.zdozier.com` both end up there.
- <https://zdozier.com/cv.pdf> opens the CV.
- <https://zdozier.com/colophon/> shows the last update time and a status
  line for each data source.
- The **Actions** tab shows a green "Update site" run. The next scheduled run
  is the coming Monday at 14:00 UTC (7 am Pacific in summer, 6 am in winter).

## Optional: an OpenAlex API key

OpenAlex (citation counts, h-index, open-access links) allows a limited
number of requests per day without a key, and GitHub's runners share IP
addresses with many other users. If the colophon or a run log shows the
OpenAlex source failing or using its cached copy week after week, get a free
API key from your OpenAlex account (check the current menu wording on
openalex.org), then add it to the repository: **Settings** > **Secrets and
variables** > **Actions** > **New repository secret**, name
`OPENALEX_API_KEY`, value: the key. The workflow passes it to the fetch step
automatically. Nothing else changes, and the site works without it.
