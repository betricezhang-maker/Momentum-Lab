# Running the unchanged unsigned Momentum Lab EXE

Checked against Microsoft documentation on 2026-09-17. This guide does not
change the executable, package, application code or Windows security settings.

## Answer for this PC

**There is no per-app manual Allow option for the Smart App Control block
recorded on this PC.** The packaging test recorded Windows error 4551 and
Code Integrity events 3033, 3077 and 3118. That is different from the ordinary
SmartScreen warning that may offer **Run anyway**.

Microsoft explicitly says Smart App Control has no individual-app bypass.
An unsigned application can run if Windows already trusts its reputation, but
that does not provide an operator-controlled exception for this blocked build.
[Microsoft Smart App Control FAQ](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)

## Identify the message before proceeding

| Message or condition | What it means for manual launch |
| --- | --- |
| “Windows protected your PC,” naming Microsoft Defender SmartScreen and an unrecognized app | The steps below apply if **Run anyway** is offered. |
| “Smart App Control has blocked…” or error 4551 | No individual-app Allow or Run anyway option; stop here. |
| Block imposed by an administrator, organization or application-control policy | Ask the responsible administrator to review it; the SmartScreen steps are not a policy override. |
| A named malware detection | Stop and investigate the detection; these instructions are only for an unrecognized-app reputation warning. |

To inspect Smart App Control without changing anything:

1. Open **Start**, type **Windows Security**, and open it.
2. Open **App & browser control**.
3. Open **Smart App Control settings** and read its current status.
4. Leave the setting unchanged. Status alone does not replace reading the actual
   block message.

## Manual launch when the only warning is SmartScreen

These steps do **not** resolve the Smart App Control block already observed on
this PC. They describe a separate Windows warning you may encounter elsewhere.

1. Use the existing `MomentumLab_V4_Stable_Unsigned.zip` from the release folder.
   Right-click it, choose **Extract All…**, and extract into a new writable
   folder. Do not extract over your working installation.
2. Open the extracted `MomentumLab_V4_Stable` folder. Confirm that
   `MomentumLab.exe`, `_internal`, `data`, `results`, `live`, `logs`, `backups`
   and `config` are together. Do not run the EXE from inside the ZIP.
3. Double-click **MomentumLab.exe** as your normal Windows user.
4. If the dialog specifically says **Windows protected your PC** and identifies
   **Microsoft Defender SmartScreen** and an unrecognized application, click
   **More info**.
5. Confirm the application is **MomentumLab.exe**. An unknown publisher is
   expected for this unsigned build. If you trust this locally built package
   and **Run anyway** is available, click **Run anyway**.
6. If that button is absent, or a Smart App Control / policy block follows, stop.
   Do not substitute administrator mode, antivirus exclusions or renamed files.
7. If startup succeeds, keep the Momentum Lab launcher window open. The browser
   opens after the backend is healthy. Use **Open Momentum Lab** if needed.
   Close the application with **Quit** in the launcher after active work finishes.

Microsoft documents that unsigned files may receive a SmartScreen reputation
warning with a user continuation option, while policy can prohibit continuation.
Smart App Control can supersede SmartScreen and also evaluates locally built
executables. [Microsoft SmartScreen reputation documentation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

## What remains blocked

For the recorded Smart App Control rejection, keeping both this unsigned build
and the current security policy unchanged means there is no supported manual
allow procedure. Continue using the working source launcher, or arrange trusted
code signing and repeat the EXE validation. Signing would produce a different
binary; it has not been performed.

The clean package contains no personal data or API token. For any eventual EXE
testing, copy your data and portfolio folders after closing the source app;
preserve the originals. A successful manual launch would not by itself complete
the pending restart, persistence and relocation checks.

## Existing artifact identities

Release directory: `dist/20260917-070826-378119/`.

SHA-256 of `MomentumLab_V4_Stable/MomentumLab.exe`:

```text
C1790FBC8FF0766FA88C62638D61844E7B54E7A92E6EA5E331E7615A9A203F11
```

SHA-256 of `MomentumLab_V4_Stable_Unsigned.zip`:

```text
1EDF677A8ED1D21EAF814B5BA3F05BC110995122A1B1E99DB9159A686DCAF0E1
```

These identify the existing artifacts, not a publisher signature or security
certification. This guide is separate from the ZIP so its contents stay unchanged.
