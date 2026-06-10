<#
.SYNOPSIS
    The Maid - a local, READ-ONLY batch worker for the Circular Steel Reuse project.

.DESCRIPTION
    Watches maid\queue\ for task files (one .md per task) that AI agents (Claude/Opus) drop in,
    runs each through the local Ollama model (qwen2.5-coder:32b) via its HTTP API, and writes a
    clean Markdown report to maid\reports\. Processed task files move to maid\done\.

    ISOLATION GUARANTEES (the Maid "just cleans" - it never interferes):
      * Writes ONLY inside maid\ (reports\, done\, logs\). Never edits src\, tests\, docs\, or configs.
      * Never calls git. Never runs the app. pytest (when a task asks) is read-only w.r.t. source.
      * Produces text only; a reviewing agent turns anything useful into real code/numbers.

.PARAMETER Once
    Drain the queue once and exit (good for Task Scheduler / overnight batches).
    Default (omitted) = watch forever, re-scanning every -IntervalSeconds. Ctrl-C to stop.

.EXAMPLE
    .\Run-Maid.ps1 -Once
.EXAMPLE
    .\Run-Maid.ps1            # watch loop
#>
[CmdletBinding()]
param(
    [switch] $Once,
    [int]    $IntervalSeconds = 30,
    [int]    $TimeoutSec      = 900,
    [int]    $DefaultNumCtx   = 16384,
    [string] $Model           = "qwen2.5-coder:32b",
    [string] $OllamaUrl       = "http://localhost:11434"
)

$ErrorActionPreference = "Stop"

# --- Paths (all derived from this script's location; CWD-independent) ---------------------
$MaidDir     = $PSScriptRoot
$ProjectRoot = Split-Path $MaidDir -Parent                       # ...\circular-steel-reuse
$PythonRoot  = Split-Path $ProjectRoot -Parent                   # ...\Python
$QueueDir    = Join-Path $MaidDir "queue"
$ReportsDir  = Join-Path $MaidDir "reports"
$DoneDir     = Join-Path $MaidDir "done"
$LogsDir     = Join-Path $MaidDir "logs"
$SystemPrompt= Join-Path $MaidDir "MAID_SYSTEM_PROMPT.md"
$LogFile     = Join-Path $LogsDir "maid.log"

# Signed Python (the only interpreter allowed by the machine's app-control policy).
$Py = Join-Path $PythonRoot ".venv-signed\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }   # fallback; pytest tasks may then be skipped

foreach ($d in @($QueueDir, $ReportsDir, $DoneDir, $LogsDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# --- Helpers ------------------------------------------------------------------------------

function Write-MaidLog([string]$Message) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Test-Ollama {
    try { Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 5 | Out-Null; return $true }
    catch { return $false }
}

# Parse a task file: front-matter (--- ... ---) + body. Returns a hashtable.
function Read-TaskFile([string]$Path) {
    $raw   = Get-Content -Path $Path -Raw -Encoding UTF8
    $lines = $raw -split "`r?`n"
    $task  = @{ title = [System.IO.Path]::GetFileNameWithoutExtension($Path)
                use_pytest = $false; num_ctx = $DefaultNumCtx; files = @(); body = "" }

    $i = 0
    if ($lines.Count -gt 0 -and $lines[0].Trim() -eq "---") {
        $i = 1
        while ($i -lt $lines.Count -and $lines[$i].Trim() -ne "---") {
            $l = $lines[$i]
            if ($l -match '^\s*-\s+(.+?)\s*$') {                 # a files: list item
                $task.files += $matches[1]
            }
            elseif ($l -match '^\s*([A-Za-z_]+)\s*:\s*(.*?)\s*$') {
                $k = $matches[1].ToLower(); $v = $matches[2]
                switch ($k) {
                    "title"      { if ($v) { $task.title = $v } }
                    "use_pytest" { $task.use_pytest = ($v -match '^(?i:true|yes|1)$') }
                    "num_ctx"    { if ($v -match '^\d+$') { $task.num_ctx = [int]$v } }
                }
            }
            $i++
        }
        $i++   # skip closing ---
    }
    $task.body = ($lines[$i..($lines.Count - 1)] -join "`n").Trim()
    return $task
}

function Invoke-MaidTask($TaskFile) {
    $start = Get-Date
    $task  = Read-TaskFile $TaskFile.FullName
    Write-MaidLog ("START  {0}  (files: {1}; pytest: {2})" -f `
        $TaskFile.Name, $task.files.Count, $task.use_pytest)

    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine((Get-Content -Path $SystemPrompt -Raw -Encoding UTF8))
    [void]$sb.AppendLine()

    $usedFiles = @()

    # Optional pytest context (read-only; all streams captured to a temp file to stay clean)
    if ($task.use_pytest) {
        if (Test-Path $Py) {
            $tmp = [System.IO.Path]::GetTempFileName()
            Push-Location $ProjectRoot
            try { & $Py -m pytest --tb=short -q *> $tmp } catch {} finally { Pop-Location }
            $out = Get-Content -Path $tmp -Raw; Remove-Item $tmp -ErrorAction SilentlyContinue
            [void]$sb.AppendLine("===== PYTEST OUTPUT (read-only) =====")
            [void]$sb.AppendLine($out)
            [void]$sb.AppendLine()
            $usedFiles += "pytest --tb=short -q"
        } else {
            [void]$sb.AppendLine("===== PYTEST OUTPUT =====`n(not available: signed Python not found)`n")
        }
    }

    # Named source files (read-only)
    foreach ($rel in $task.files) {
        $full = Join-Path $ProjectRoot $rel
        if (Test-Path $full) {
            [void]$sb.AppendLine("===== FILE: $rel =====")
            [void]$sb.AppendLine((Get-Content -Path $full -Raw -Encoding UTF8))
            [void]$sb.AppendLine()
            $usedFiles += $rel
        } else {
            [void]$sb.AppendLine("===== FILE: $rel =====`n(file not found - not provided)`n")
        }
    }

    [void]$sb.AppendLine("===== TASK =====")
    [void]$sb.AppendLine($task.body)

    $body = @{
        model   = $Model
        prompt  = $sb.ToString()
        stream  = $false
        options = @{ num_ctx = $task.num_ctx; temperature = 0.2 }
    } | ConvertTo-Json -Depth 6

    $answer = $null; $errMsg = $null
    try {
        $resp = Invoke-RestMethod -Uri "$OllamaUrl/api/generate" -Method Post `
                    -Body $body -ContentType "application/json" -TimeoutSec $TimeoutSec
        $answer = $resp.response
    } catch { $errMsg = $_.Exception.Message }

    $finish   = Get-Date
    $duration = [int]($finish - $start).TotalSeconds
    $stamp    = $finish.ToString("yyyyMMdd-HHmmss")
    $slug     = [System.IO.Path]::GetFileNameWithoutExtension($TaskFile.Name)
    $reportPath = Join-Path $ReportsDir ("{0}-{1}.md" -f $slug, $stamp)

    $hdr = @()
    $hdr += "# Maid report: $($task.title)"
    $hdr += ""
    $hdr += "- Task file: ``$($TaskFile.Name)``"
    $hdr += "- Model: ``$Model``  (num_ctx $($task.num_ctx))"
    if ($usedFiles.Count) { $ctxLabel = ($usedFiles -join ", ") } else { $ctxLabel = "(task text only)" }
    $hdr += "- Context used: $ctxLabel"
    $hdr += "- Started: $($start.ToString('yyyy-MM-dd HH:mm:ss'))  |  Duration: ${duration}s"
    $hdr += ""
    $hdr += "> Read-only draft from the local Maid. Review before acting; the Maid does not edit code or numbers."
    $hdr += ""
    $hdr += "---"
    $hdr += ""

    if ($errMsg) {
        $hdr += "**The Maid could not complete this task.**"
        $hdr += ""
        $hdr += '```'
        $hdr += $errMsg
        $hdr += '```'
        Write-MaidLog ("ERROR  {0}  ({1}s)  {2}" -f $TaskFile.Name, $duration, $errMsg)
    } else {
        $hdr += $answer
        Write-MaidLog ("OK     {0}  ({1}s)  -> reports\{2}" -f `
            $TaskFile.Name, $duration, (Split-Path $reportPath -Leaf))
    }

    Set-Content -Path $reportPath -Value ($hdr -join "`n") -Encoding utf8

    # Move the task to done\ (keep history; never overwrite)
    $destName = $TaskFile.Name
    $dest = Join-Path $DoneDir $destName
    if (Test-Path $dest) {
        $dest = Join-Path $DoneDir ("{0}-{1}{2}" -f $slug, $stamp, $TaskFile.Extension)
    }
    Move-Item -Path $TaskFile.FullName -Destination $dest
}

# --- Main loop ----------------------------------------------------------------------------

if ($Once) { $modeLabel = "drain once" } else { $modeLabel = "watch (Ctrl-C to stop)" }
Write-Host "Maid watching: $QueueDir" -ForegroundColor Cyan
Write-Host ("Mode: {0}  |  Model: {1}" -f $modeLabel, $Model) -ForegroundColor Cyan

while ($true) {
    if (-not (Test-Ollama)) {
        Write-MaidLog "WARN   Ollama API not reachable at $OllamaUrl - is Ollama running?"
        if ($Once) { exit 1 }
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $tasks = @(Get-ChildItem -Path $QueueDir -Filter *.md -File | Sort-Object LastWriteTime)
    if ($tasks.Count -eq 0) {
        if ($Once) { break }
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    foreach ($t in $tasks) { Invoke-MaidTask $t }
}

Write-Host "Maid done. Reports in: $ReportsDir" -ForegroundColor Green
