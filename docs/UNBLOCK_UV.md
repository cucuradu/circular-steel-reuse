# Unblocking `uv` (and other unsigned binaries) on Windows

On the development machine, [`uv`](https://docs.astral.sh/uv/) and uv-managed Python interpreters
fail to run: Windows refuses to execute them because they are **unsigned**. This note records what is
actually doing the blocking and how to unblock it. It is Windows-specific and only relevant if you hit
the same wall; the project itself installs fine with plain `pip` (see [README](../README.md)).

> ⚠️ Everything in the "fix" sections changes a **system code-integrity policy** and needs an
> **elevated (Administrator) PowerShell**. Read the cautions first. If the machine is managed by an
> employer/IT, the policy may be locked — talk to IT instead of removing it.

## What is doing the blocking (diagnosis)

Three different Windows features can block unsigned executables. Check which one is active:

```powershell
# 1) Smart App Control (0 = off, 1 = enforced, 2 = evaluation)
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' VerifiedAndReputablePolicyState).VerifiedAndReputablePolicyState

# 2) AppLocker (look for any RuleCollection with EnforcementMode = Enabled)
([xml](Get-AppLockerPolicy -Effective -Xml)).AppLockerPolicy.RuleCollection |
  ForEach-Object { "$($_.Type): $($_.EnforcementMode), rules=$($_.ChildNodes.Count)" }

# 3) WDAC / App Control for Business — list active policies (needs elevation)
CiTool --list-policies --json | ConvertFrom-Json |
  Select-Object -Expand Policies |
  Select-Object FriendlyName, IsEnforced, IsSystemPolicy, PolicyID
```

**On this machine (2026-06-09):**
- Smart App Control = **0 (off)** → not the cause.
- AppLocker = **0 rules** → not the cause.
- **WDAC is active**: eight enforced policies live in
  `C:\Windows\System32\CodeIntegrity\CiPolicies\Active\*.cip`. WDAC is what blocks unsigned `uv.exe`
  and the unsigned uv-managed `python.exe`.

Most of those eight are **Microsoft's own signed base policies** (Windows mode, the driver block list,
the SecureBoot/Code-Integrity defaults) — **do not touch those**. The blocker is whichever **custom,
enforced, non-system** policy is in the list. Identify it with the `CiTool` command above (run
elevated): note the `FriendlyName` and `PolicyID` of any policy where `IsSystemPolicy = False` and
`IsEnforced = True`.

## Fastest option — skip uv entirely (no policy change)

WDAC blocks *unsigned* binaries, but the **Microsoft Store Python is signed** and runs fine. This is
what the project already uses. You don't need uv:

```powershell
# create a venv from the signed Store Python, then use pip + `python -m`
py -3.12 -m venv .venv-signed
.\.venv-signed\Scripts\Activate.ps1
pip install -e ".[analysis,fea,ml,opt,report,llm,ui,bim,dev]"
python -m pytest          # the pip-generated *.exe shims are unsigned too, so invoke via -m
```

If you only ever do this, you can stop here — uv is a convenience, not a requirement.

## Option A — put the custom policy into Audit mode (recommended, reversible)

Audit mode keeps the policy but **logs** instead of **blocks**, so unsigned binaries run again while
you keep the (Microsoft) protections. With the offending policy's `.cip` file:

1. Find it: the custom policy's `.cip` is one of the files in `...\CiPolicies\Active\`. Match its name
   to the `PolicyID` from `CiTool --list-policies`.
2. Flip it to audit and redeploy (elevated):
   ```powershell
   $cip = 'C:\path\to\your-exported-policy.cip'         # an editable copy, see note below
   Set-RuleOption -FilePath $xml -Option 3              # 3 = "Enabled:Audit Mode" (operates on the XML)
   ConvertFrom-CIPolicy -XmlFilePath $xml -BinaryFilePath $cip
   CiTool --update-policy $cip
   ```
   (`Set-RuleOption` edits the policy **XML**; if you only have the `.cip`, recreate the XML with the
   ConfigCI module or re-export it from your policy source.)
3. **Reboot.** Verify with `CiTool --list-policies` that the policy is no longer enforced.

## Option B — remove the custom policy (only if it is local and yours)

If the enforced custom policy was added locally (not pushed by IT/MDM) and you don't need it:

```powershell
CiTool --remove-policy "<PolicyID-GUID>"   # use the GUID, including braces, from --list-policies
```
Then **reboot**. Managed/base/system policies (`IsSystemPolicy = True`) cannot be removed this way and
**must not** be.

## Option C — allow-list uv specifically (advanced, keeps the policy enforced)

Keep WDAC enforcing but add an explicit allow for the tools you trust, by **file hash** or by
**signer**:

- Easiest durable form: get a code-signing certificate (or self-signed for a dev box), sign `uv.exe`
  and the uv-managed `python.exe` with `signtool`, and add a **signer rule** for that cert to the
  custom policy (`New-CIPolicyRule -DriverFilePath ... ` / `Add-SignerRule`), then rebuild + update the
  `.cip` as in Option A.
- Quicker but brittle: add **hash rules** for the exact binaries (`New-CIPolicyRule -Level Hash`).
  These break on every uv/Python update, so prefer the signer rule.

## Cautions

- **Never** delete or weaken the Microsoft base policies (Windows mode, driver block list, SecureBoot
  defaults). Removing the wrong policy can prevent boot.
- WDAC changes take effect **after a reboot** (or a re-evaluation); don't assume it worked until you
  reboot and re-check with `CiTool --list-policies`.
- If **BitLocker** is on, have your recovery key handy before changing code-integrity settings.
- If the policy is **enterprise-managed** (Intune/SCCM/Group Policy), local removal will be reverted
  or refused — that one is genuinely IT's call.
