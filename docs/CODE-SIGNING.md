# Code signing

Windows SmartScreen warns about unsigned installers ("Windows protected your PC... Unknown
publisher"). Signing doesn't make that warning disappear on day one: SmartScreen also wants to see
a download history. What it changes:

- The warning names a **verified publisher** instead of "Unknown publisher".
- Trust is earned by the **publisher**, not by each file. Unsigned, every new release starts from
  zero and warns again. Signed, reputation builds up over the first few weeks and hundreds of
  installs, and later releases inherit it.
- Smart App Control (on some Windows 11 PCs) blocks unsigned apps it doesn't already know, with
  no "Run anyway"; a valid signature lets them run.

SnipForge signs with [Azure Artifact Signing](https://learn.microsoft.com/azure/artifact-signing/)
(formerly Trusted Signing). It's what Microsoft recommends for apps outside the Store, costs
**$9.99 a month** (Basic: 5,000 signatures; each release uses about 3), and needs no hardware
token. Release and preview builds sign `SnipForge.exe`, the installer and its uninstaller as soon as
the five repository variables below are set. Until then they build unsigned, with a warning in the
build log.

Microsoft's own guidance on what to expect:
[SmartScreen reputation for Windows app developers](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation).

## Setting it up (one time, about 30 minutes plus the identity check)

You need an Azure account with a subscription (pay-as-you-go), and to be in the US or Canada to
validate as an individual. The certificate shows your **verified legal name** as the publisher. To
show a company name instead, validate a registered organization in step 4.

### 1. Turn on Artifact Signing in your subscription

[Azure portal](https://portal.azure.com) > **Subscriptions** > your subscription > **Settings** >
**Resource providers** > search `Microsoft.CodeSigning` > **Register**.

### 2. Create the signing account

Search the portal for **Artifact Signing Accounts** > **Create**:

- **Resource group**: create one, e.g. `snipforge-signing`
- **Account name**: e.g. `snipforge` (this is `ARTIFACT_SIGNING_ACCOUNT`)
- **Region**: e.g. East US
- **Pricing tier**: Basic

When it's created, open it. Its **Overview** shows the **Account URI**, e.g.
`https://eus.codesigning.azure.net/`. That's `ARTIFACT_SIGNING_ENDPOINT`.

### 3. Let yourself request the identity check

In the signing account: **Access control (IAM)** > **Add** > **Add role assignment** >
**Artifact Signing Identity Verifier** > **Members**: yourself > **Review + assign**.

### 4. Verify your identity

In the signing account: **Objects** > **Identity validations** > **New identity** > **Public** >
**Individual** (or **Organization** for a registered business). Fill in your legal name, address,
email and phone, and complete the ID check Microsoft emails you. It can take from a few hours to a
couple of weeks; the status changes to **Completed**.

### 5. Create the certificate profile

In the signing account: **Objects** > **Certificate profiles** > **Create** > **Public Trust**:

- **Name**: e.g. `snipforge-public` (this is `ARTIFACT_SIGNING_PROFILE`)
- **Verified CN and O**: the identity from step 4

### 6. Let GitHub sign in to Azure, with no password

Search the portal for **App registrations** > **New registration** > name it
`snipforge-github-signing` > **Register**. Its **Overview** shows the **Application (client) ID**
(`AZURE_CLIENT_ID`) and **Directory (tenant) ID** (`AZURE_TENANT_ID`).

Then in that app: **Certificates & secrets** > **Federated credentials** > **Add credential**:

- **Scenario**: GitHub Actions deploying Azure resources
- **Organization**: `nighthawk909`, **Repository**: `snipforge`
- **Entity type**: Branch, **Branch**: `main`
- **Name**: `snipforge-main`

This tells Azure to trust only workflow runs from `main`: releases, previews and manual builds.
Pull requests can't sign. Don't create a client secret; nothing needs one.

### 7. Let that app sign

Back in the signing account: **Access control (IAM)** > **Add role assignment** >
**Artifact Signing Certificate Profile Signer** > **Members** > **Select members** > search
`snipforge-github-signing` > **Review + assign**.

### 8. Tell the repository

GitHub > the repository > **Settings** > **Secrets and variables** > **Actions** > **Variables** tab
> **New repository variable**, five times. They're variables, not secrets: none of them is a
password, and Azure trusts the GitHub token, not these values.

| Name | Example |
|---|---|
| `ARTIFACT_SIGNING_ENDPOINT` | `https://eus.codesigning.azure.net` |
| `ARTIFACT_SIGNING_ACCOUNT` | `snipforge` |
| `ARTIFACT_SIGNING_PROFILE` | `snipforge-public` |
| `AZURE_CLIENT_ID` | the app's Application (client) ID |
| `AZURE_TENANT_ID` | the Directory (tenant) ID |

Or with the GitHub CLI: `gh variable set ARTIFACT_SIGNING_ENDPOINT --body https://eus.codesigning.azure.net`
(and so on).

### 9. Try it without releasing anything

GitHub > **Actions** > **Build** > **Run workflow** > branch `main` > tick **Sign it** > **Run**.
The **Check signatures** step lists each file and who signed it, and the run summary shows
"Signed by: CN=...". The signed installer is in the run's `snipforge-windows` artifact. Right-click
it > **Properties** > **Digital Signatures** shows the same.

From then on every release and preview is signed. If signing fails, the build fails: a release
never quietly goes out unsigned once signing is set up.

## How it works

- [`build.yml`](../.github/workflows/build.yml) checks the five variables (none: unsigned with a
  warning; some: fails and names the missing ones), signs in to Azure with GitHub's OpenID Connect
  token through `azure/login`, and builds with `scripts/build.py --sign`.
- [`scripts/codesign.py`](../scripts/codesign.py) downloads Microsoft's signtool and the Artifact
  Signing client from nuget.org, pinned to exact versions and SHA-256 checksums, and writes the
  `metadata.json` naming the account and profile.
- [`scripts/build.py --sign`](../scripts/build.py) signs `SnipForge.exe` before it's zipped and
  packed into the installer, then Inno Setup signs the uninstaller and `Setup.exe` with the same
  command, then everything is verified (`signtool verify /pa`) before the checksums are written.
  Every signature is timestamped (Artifact Signing certificates last three days; the timestamp keeps
  signatures valid after that).

### Signing on your own PC

Install the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli-windows), give your
own account the **Artifact Signing Certificate Profile Signer** role (step 7), then:

```powershell
az login
python scripts/codesign.py setup --dest $env:LOCALAPPDATA\SnipForgeSigning --endpoint https://eus.codesigning.azure.net --account snipforge --profile snipforge-public
```

Set the three variables it prints (`SIGNTOOL`, `ARTIFACT_SIGNING_DLIB`,
`ARTIFACT_SIGNING_METADATA`), then `python scripts/build.py --installer --sign`. The signing client
needs the [.NET 8 runtime](https://dotnet.microsoft.com/download/dotnet/8.0).

## Inside a company

Signing helps everyone who downloads SnipForge from GitHub. Inside a company there are faster ways
to avoid the prompt altogether:

- **Software Center / Intune**: apps IT deploys this way never show the SmartScreen download prompt.
- **An intranet share**: files run from a location in the Local intranet zone aren't checked by
  SmartScreen. The update feed in [DISTRIBUTION.md](DISTRIBUTION.md) can point there too.
- **The company's own certificate**: a company copy of this repository can set its own five
  variables (its Azure tenant) or leave them unset and have IT sign the installer.
- IT can also submit a release to the
  [Microsoft Security Intelligence portal](https://www.microsoft.com/wdsi/filesubmission) to speed up
  trust for managed PCs.
