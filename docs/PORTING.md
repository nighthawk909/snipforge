# Moving SnipForge into your organization's GitHub

This repository is meant to be copied whole into a company's own GitHub, usually a GitHub
Enterprise (GHE) repository. One script points the copy at its new home; this guide covers the
rest: the values to use, the settings to check, and how updates reach people at work.

## What changes at work (read this first)

A private or internal repository can only be seen after signing in. SnipForge's updater never
signs in (no tokens on PCs, by design), and neither does `scripts/publish_feed.py --github`. So in
your copy:

- The permanent download link (`<repository>/releases/latest/download/SnipForge-setup.exe`) and the
  GitHub Pages download page ask for a GitHub sign-in. Coworkers without an account there can't use
  them.
- The app can't read the copy's releases. **Check for updates** says *"SnipForge can't see the
  releases at …: the repository is private, needs a sign-in (which SnipForge doesn't do) or has
  moved. If your organization keeps SnipForge updates in a shared folder, ask your IT team to point
  SnipForge at it (the UpdateFeed policy)."* Automatic checks fail quietly; SnipForge tries again
  every six hours and each time it starts, sending one anonymous request that the server refuses.

So at work the **company mirror is the main update path**: a SharePoint library (synced to PCs by
OneDrive) or a file share, filled by `scripts/publish_feed.py` and chosen with the
`UpdateSource = "feed"` / `UpdateFeed` policy ([DISTRIBUTION.md, section 2](DISTRIBUTION.md#2-company-mirror-sharepoint-or-a-network-share)).
Set it up before handing out installers ([step 5](#5-updates-at-work-the-company-mirror)).

## 1. Know your values

Look at the address of your organization's GitHub in a browser:

| | GHE Cloud on github.com | GHE Cloud with data residency | GHE Server (self-hosted) |
|---|---|---|---|
| Looks like | `https://github.com/<org>` | `https://<name>.ghe.com/<org>` | `https://<host>/<org>` |
| `--server-url` | leave it out | `https://<name>.ghe.com` | `https://<host>` |
| API (worked out from it) | `https://api.github.com` | `https://api.<name>.ghe.com` | `https://<host>/api/v3` |
| `--pages-url` (only if you use Pages) | shown in **Settings → Pages** | shown in **Settings → Pages** | `https://<host>/pages/<org>/<repo>/` or `https://pages.<host>/<org>/<repo>/` |

`--repo` is always `<org>/<repo>`: the organization (or user) and the repository name.

Release downloads are trusted from the server's own host (plus GitHub's file storage on
github.com). If your copy can be read without signing in and its downloads redirect elsewhere, add
that host to `EXTRA_DOWNLOAD_HOSTS` in `snipforge/project.py`. A private copy never gets that far.

## 2. Copy the repository and point it at its new home

Create an empty repository in your organization (no README or license). Don't push to it yet:
the first push to `main` starts the release workflows, so the settings in step 3 come first.

```bash
git clone https://github.com/nighthawk909/snipforge.git snipforge
cd snipforge
git remote rename origin upstream
git remote add origin https://<server>/<org>/<repo>.git
python scripts/port_repo.py --repo <org>/<repo> --server-url <server> --pages-url <pages address>
git diff
python -m ruff check .
python -m pytest -q
git commit -am "chore: point SnipForge at its new repository"
```

Leave out `--server-url` on github.com and `--pages-url` if you don't use Pages; `--dry-run` lists
the files first. The script rewrites `snipforge/project.py` (`SERVER_URL` and `REPO`), which is
the one place the app, the build, the scripts and the tests read the repository from, and every
link to the old repository in the files listed in [Files that name the repository](#files-that-name-the-repository).
Workflows need no edits: they use `$GITHUB_REPOSITORY` and `$GITHUB_SERVER_URL` and point `gh` at
your server. A `chore:` commit doesn't trigger a release. Keeping the public repository as
`upstream` lets you take its later changes ([step 6](#6-taking-later-changes-from-the-public-repository)).

## 3. Repository settings (before the first push)

- **Actions** must be allowed to run the actions the workflows use, all pinned to commit SHAs:
  `git grep -h "uses:" .github/workflows | sort -u` lists them. On GHE Server they come from
  github.com through GitHub Connect, or from copies synced by your admin.
- **Runners**: the workflows use `windows-latest` and `ubuntu-latest`. On GHE Cloud these are
  GitHub-hosted (private repositories use your Actions minutes). On GHE Server, use self-hosted
  runners with those labels or change `runs-on`. The Windows build installs Inno Setup 6 with
  Chocolatey if it's missing.
- **Workflow permissions** (Settings → Actions → General): keep the default read-only token, and
  turn on **Allow GitHub Actions to create and approve pull requests**. release-please opens the
  release PR and approves its CI runs. Your organization may have to allow this first.
- **Protect `main`** with a ruleset like the public one: squash merges only, and required checks
  **Lint and test** and **Build installer / Build and self-test**.
- **CodeQL** needs GitHub Advanced Security (Code Security) on private and internal repositories.
  Without it, disable the CodeQL workflow in the Actions tab rather than deleting the file, so
  merges from upstream stay clean.
- **Build provenance** is skipped on private and internal repositories; `SHA256SUMS.txt` still
  lets IT verify every file.
- **Pages** is optional. If you want the download page anyway (only people who can sign in will see
  it), set **Settings → Pages → Source** to **GitHub Actions**.
- **Code signing** is optional and off until you set its repository variables
  ([CODE-SIGNING.md](CODE-SIGNING.md)), for your company's Azure tenant and certificate. Leave them
  unset to build unsigned, for example when IT signs or deploys the installer itself. Software Center
  installs don't show the SmartScreen prompt either way. Either way the build workflow names
  `azure/login`, and GitHub fetches every action a job names when it starts, so it must be allowed
  (on GHE Server, through GitHub Connect or a synced copy) even if you never sign.

## 4. Push, and give release-please its starting point

`<version>` is the one in `.release-please-manifest.json`:

```bash
gh auth login --hostname <server host>   # once
git push origin --tags
gh release create v<version> --repo <server host>/<org>/<repo> --verify-tag --title "SnipForge <version>" --notes "Copied from the public repository."
git push -u origin main
```

The tags and that release come first so release-please can find the previous release; otherwise its
first release PR lists the whole history. Then run **Actions → Release → Run workflow** with the
tag `v<version>`: it builds and self-tests SnipForge and attaches the installer, `SHA256SUMS.txt`
and the rest to that release, so you can fill the mirror right away. From then on releases work as
in the public repository: merge pull requests, then merge release-please's release PR. Previews
come from **Actions → Preview release**.

## 5. Updates at work: the company mirror

1. **Create the mirror**: a SharePoint document library or a file-share folder that everyone can
   read and maintainers can write. [DISTRIBUTION.md](DISTRIBUTION.md#sharepoint) explains why
   SharePoint has to be read through a synced folder (or a share), not its web address.
2. **Fill it after each release.** `publish_feed.py --github` can't sign in, so download the
   release with `gh` (signed in to your server) and publish the folder with `--dist`:

   ```powershell
   [Console]::OutputEncoding = [Text.UTF8Encoding]::new()   # read gh's output as UTF-8
   $tag  = "v0.7.0"
   $repo = "<server host>/<org>/<repo>"
   $dl   = "$env:TEMP\snipforge-$tag"
   gh release download $tag --repo $repo --dir $dl --clobber --pattern "SnipForge-*-setup.exe" --pattern SHA256SUMS.txt
   gh release view $tag --repo $repo --json body --jq .body | Out-File -Encoding utf8 "$dl\notes.md"
   python scripts/publish_feed.py --dist $dl --target "<synced library or \\server\share>" --notes-file "$dl\notes.md"
   ```

   It checks every installer against `SHA256SUMS.txt` before and after copying it, and only then
   lists it in `releases.json`. Preview builds (`-preview.N`) go to the Preview channel.
3. **Point SnipForge at it** with Group Policy, Intune or a script, before or together with the
   installer:

   ```
   HKLM\Software\Policies\SnipForge
     UpdateSource = "feed"
     UpdateFeed   = "\\fileserver\apps\SnipForge"   (or %USERPROFILE%\<synced library folder>)
   ```

4. **Hand out the installer** from the mirror: share the library or folder, or the
   `SnipForge-<version>-setup.exe` in it. IT can deploy it machine-wide instead
   ([DISTRIBUTION.md, section 3](DISTRIBUTION.md#3-software-center)). If you upload the download page
   (`docs/site/index.html`) to SharePoint, change its **Download for Windows** link (`id="download"`)
   to the installer in the library; its other links go to your repository and need a sign-in.

## 6. Taking later changes from the public repository

```bash
git fetch upstream
git merge upstream/main
python scripts/port_repo.py --repo <org>/<repo> --server-url <server> --pages-url <pages address> --from https://github.com/nighthawk909/snipforge
```

`--from` rewrites links that arrived with the merge (after the first port, `snipforge/project.py`
already names your copy); leave out `--server-url` and `--pages-url` as in step 2. If a merge
conflicts in `snipforge/project.py`, keep your `SERVER_URL` and `REPO`. If it conflicts on a line
that only differs by its link, take the public repository's line and let `port_repo.py` rewrite it.
Then run ruff and pytest as in step 2.

## Files that name the repository

`port_repo.py` rewrites these (a test keeps this list complete):

| File | What names the repository |
|---|---|
| `snipforge/project.py` | `SERVER_URL` and `REPO`: the app's update checks, release links and About box, the installer's links, and the scripts' and tests' defaults |
| `packaging/installer.iss` | Default `RepoUrl` for builds by hand (`scripts/build.py` passes the real one) |
| `pyproject.toml` | Project links |
| `README.md` | Download link, releases, issues |
| `SECURITY.md` | Where to report a vulnerability |
| `docs/DISTRIBUTION.md` | Download link, download page, how to verify a release |
| `docs/IT-REVIEW.md` | Update check and download addresses, how to verify a release |
| `docs/ROADMAP.md` | Links to the `feedback` and `idea` issues (point them at your own repository's issues) |
| `docs/site/index.html` | Download page links (`scripts/build_site.py` also re-points them when it builds the page) |
| `.github/workflows/pages.yml` | The download page's address, in a comment (rewritten only with `--pages-url`) |

Left as they are: `CHANGELOG.md` (the public releases' history; new entries link to your copy),
`LICENSE` (the MIT copyright notice has to stay), the author in `pyproject.toml`, and this guide.

Check these by hand too; they describe the public setup in words:

- `SECURITY.md`: point vulnerability reports at your internal process.
- `docs/IT-REVIEW.md`, **Network access**: at work, updates come from the mirror, not github.com,
  and GitHub's limit of 60 anonymous requests an hour doesn't apply.
- `docs/DISTRIBUTION.md`, **1. Download link**: needs a sign-in in a private copy.

Company branding belongs in the copy (for example as a theme), not in the public repository.
