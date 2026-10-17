# Getting SnipForge

There are three ways to get SnipForge onto PCs, from simplest to most managed:

| | Who it's for | Updates come from |
|---|---|---|
| [1. Download link](#1-download-link) | Anyone; no GitHub account or knowledge needed | GitHub, with the app asking first |
| [2. Company mirror](#2-company-mirror-sharepoint-or-a-network-share) | Teams that want installers on SharePoint or a file share, or to pilot previews | The mirror folder, set by IT policy |
| [3. Software Center](#3-software-center) | IT-managed, all-users installs | IT packages each release |

Running SnipForge from your organization's own copy of this repository (for example on GitHub
Enterprise)? A private copy's download link and page need a sign-in and the app can't check it for
updates, so the company mirror becomes the way updates arrive. See [PORTING.md](PORTING.md).

## 1. Download link

This link always downloads the newest stable installer:

**<https://github.com/nighthawk909/snipforge/releases/latest/download/SnipForge-setup.exe>**

Every stable release attaches `SnipForge-setup.exe` (the same file as
`SnipForge-<version>-setup.exe`, listed in that release's `SHA256SUMS.txt`), and GitHub's
`releases/latest` always points at the newest non-preview release, so the link never needs updating.
A new release stays a draft until the Release workflow has attached its files, so the link keeps
serving the previous version until then instead of showing "Not Found".

The download page at <https://nighthawk909.github.io/snipforge/> wraps the link in a big
**Download for Windows** button, with install steps, the SmartScreen prompt explained, how updates
work, a feature list and system requirements. It keeps itself current: the
[`Download page`](../.github/workflows/pages.yml) workflow runs after every Release and Preview
release run, and [`scripts/build_site.py`](../scripts/build_site.py) stamps the page with the
current version, release date, download size and "What's new" from the release notes, plus a
**Try what's next** download when a newer preview build exists. People viewing it load nothing
from anywhere else; there are no scripts, fonts, trackers or analytics.

Its source is [`docs/site/`](site/index.html): one HTML file plus the screenshot. It works on its
own too (without the version details), so you can upload both files to a SharePoint library or
Teams channel and open the page from there.

Text you can paste into a post for coworkers:

> **SnipForge**, a free screenshot and markup tool for Windows (a SnagIt alternative).
> Download: https://github.com/nighthawk909/snipforge/releases/latest/download/SnipForge-setup.exe
> No admin rights needed. If Windows says "Windows protected your PC", click **More info**, then
> **Run anyway** (the installer isn't code-signed yet). Then press Win+Shift+S to snip.

SnipForge then checks GitHub once a day and asks before installing a newer version.

## 2. Company mirror: SharePoint or a network share

A mirror is a folder holding SnipForge installers and a `releases.json` file that lists them. Point
SnipForge at it (with the policy values below) and it updates from there instead of GitHub. Use one
when GitHub downloads are blocked, when you want to choose which version people get, or to give a
pilot group preview builds first.

### Fill the mirror

[`scripts/publish_feed.py`](../scripts/publish_feed.py) needs only Python 3.11+ (no other packages):

```powershell
# The newest stable release from GitHub
python scripts/publish_feed.py --github latest --target "\\fileserver\apps\SnipForge"

# A specific release or a preview build
python scripts/publish_feed.py --github v0.6.0-preview.1 --target "\\fileserver\apps\SnipForge"

# Your own build (python scripts/build.py --installer), with release notes from a file
python scripts/publish_feed.py --dist dist --target "\\fileserver\apps\SnipForge" --notes-file notes.md
```

For each release it:

1. downloads (or reads from `dist`) the installer and `SHA256SUMS.txt`, and checks the installer's
   SHA-256 against it;
2. copies the installer into the target folder and checks the copy again;
3. only then adds the release to `releases.json`, newest first, writing it through a temp file and a
   rename so the app never reads half a file.

Running it again for the same release changes nothing. It refuses to overwrite a different
installer with the same name (add `--replace` when you really rebuilt a release) and leaves alone a
`releases.json` it doesn't understand. Older installers stay in the folder so people can roll back.
`--channel preview` publishes a stable version to the preview channel first; run it again with
`--channel stable` to promote it. `--published` and `--notes` override the date and notes.

The folder ends up like this:

```
\\fileserver\apps\SnipForge\
  releases.json
  SnipForge-0.6.0-setup.exe
  SnipForge-0.6.0-preview.1-setup.exe
  SnipForge-0.5.0-setup.exe
```

and `releases.json` looks like this (newest first; `channel` is `stable` or `preview`):

```json
{
  "schema": 1,
  "releases": [
    {
      "version": "0.6.0",
      "channel": "stable",
      "published": "2026-10-20T15:00:00Z",
      "notes": "### Features\n* ...",
      "assets": [
        {"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": "<64 hex digits>"}
      ]
    }
  ]
}
```

The app downloads `name` from the same folder as `releases.json` and refuses it unless its SHA-256
matches.

### SharePoint

SharePoint web addresses (`https://<company>.sharepoint.com/...`) need a Microsoft 365 sign-in,
which the updater doesn't do. To use a SharePoint library:

- **Publish** by syncing the library to your PC (**Sync** or **Add shortcut to My files** in
  SharePoint) and running `publish_feed.py` against the synced folder. Temp files end in `.tmp`,
  which OneDrive doesn't upload, so others never see a half-copied installer.
- **Read** it from a location the app can open without signing in: a copy on a network share, a
  folder each PC syncs (for example `%USERPROFILE%\Contoso\SnipForge - Documents`), or an internal
  https web server.

To have every PC sync the library without anyone clicking **Sync**, IT can use the OneDrive policy
**Configure team site libraries to sync automatically** (Group Policy or Intune; the first sync can
take a few hours), then set `UpdateFeed` to the synced folder with `%USERPROFILE%` in the path so
it works for every user.

People can also install from the mirror directly: the installer there is the same file as on GitHub.

### Point SnipForge at the mirror (IT)

Set these string (`REG_SZ`) policy values with Group Policy Preferences, Intune or a script. They
need a SnipForge release with update feeds (newer than 0.5.0; see the release notes):

```
HKLM\Software\Policies\SnipForge
  UpdateSource = "feed"
  UpdateFeed   = "\\fileserver\apps\SnipForge"
```

```powershell
reg add HKLM\Software\Policies\SnipForge /v UpdateSource /t REG_SZ /d feed /f
reg add HKLM\Software\Policies\SnipForge /v UpdateFeed /t REG_SZ /d "\\fileserver\apps\SnipForge" /f
```

`UpdateFeed` is the folder that holds `releases.json` (or the path of the file itself): a UNC path,
a local or synced folder (environment variables such as `%USERPROFILE%` are expanded), or an
`https://` address. Plain `http://` is refused. Machine (`HKLM`) values win over per-user ones.

### Preview builds

Maintainers publish a preview from **Actions → Preview release → Run workflow**: pick the branch,
tag or commit and a version such as `0.6.0-preview.1` (the next stable version plus
`-preview.N`). The workflow builds and self-tests it and publishes a GitHub *prerelease* tagged
`v0.6.0-preview.1`. Prereleases never become "latest", so the download link, stable updates and
release-please's release PRs are unaffected. In SnipForge releases with update channels, people on
the Preview channel get them from GitHub; mirror one with
`publish_feed.py --github v0.6.0-preview.1`.

## 3. Software Center

For IT-managed, machine-wide deployment (needs admin), use the versioned installer from the
[GitHub release](https://github.com/nighthawk909/snipforge/releases):

| | |
|---|---|
| Install | `SnipForge-<version>-setup.exe /VERYSILENT /ALLUSERS /TASKS="autostart"` (installs to `%ProgramFiles%\SnipForge` and starts SnipForge for everyone at sign-in) |
| Uninstall | `"%ProgramFiles%\SnipForge\unins000.exe" /VERYSILENT` |
| Detection | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{23E25F09-747F-4B3D-B9D0-E81D15E709B3}_is1`, value `DisplayVersion` |
| Verify | Compare with `SHA256SUMS.txt`, or `gh attestation verify SnipForge-<version>-setup.exe --repo nighthawk909/snipforge` |

All-users installs don't update themselves: IT deploys each new release. To stop the app from
mentioning updates at all, set `DisableUpdateCheck = 1` (DWORD) under
`HKLM\Software\Policies\SnipForge`. People who installed SnipForge for themselves first keep that
copy until they uninstall it (Settings → Apps, or
`"%LOCALAPPDATA%\Programs\SnipForge\unins000.exe" /VERYSILENT` run as that user); remove it so
there's only one SnipForge on the PC.

See [IT-REVIEW.md](IT-REVIEW.md) for the security review: network access, data on the PC and the
supply chain.
