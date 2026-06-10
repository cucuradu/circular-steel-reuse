<#
.SYNOPSIS
    Copy all routine task templates from maid\routine\ into maid\queue\ with the next free NNNN numbers.

.DESCRIPTION
    Routine tasks (R01, R02, ...) are templates that can be re-queued at any time.
    Run this script before a work session or on a schedule; then run Run-Maid.ps1 to drain them.

    Safe to run repeatedly -- it only adds tasks, never overwrites or deletes anything.

.EXAMPLE
    .\Queue-Routine.ps1           # queue all routine templates
    .\Queue-Routine.ps1 -DryRun  # show what would be queued without writing anything
#>
[CmdletBinding()]
param(
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"

$MaidDir    = $PSScriptRoot
$RoutineDir = Join-Path $MaidDir "routine"
$QueueDir   = Join-Path $MaidDir "queue"
$DoneDir    = Join-Path $MaidDir "done"

# Find the next free NNNN by scanning queue + done
function Get-NextTaskNumber {
    $existing = @(Get-ChildItem -Path $QueueDir -Filter *.md -File) +
                @(Get-ChildItem -Path $DoneDir  -Filter *.md -File)
    $highest = 0
    foreach ($f in $existing) {
        if ($f.Name -match '^(\d{4})-') {
            $n = [int]$matches[1]
            if ($n -gt $highest) { $highest = $n }
        }
    }
    return $highest + 1
}

$templates = @(Get-ChildItem -Path $RoutineDir -Filter "R*.md" -File | Sort-Object Name)

if ($templates.Count -eq 0) {
    Write-Host "No routine templates found in $RoutineDir" -ForegroundColor Yellow
    exit 0
}

$next = Get-NextTaskNumber

foreach ($t in $templates) {
    $slug = $t.BaseName -replace '^R\d+-', ''
    $dest = Join-Path $QueueDir ("{0:D4}-{1}.md" -f $next, $slug)
    if ($DryRun) {
        Write-Host ("  [DRY RUN] would queue: {0} -> {1}" -f $t.Name, (Split-Path $dest -Leaf))
    } else {
        Copy-Item -Path $t.FullName -Destination $dest
        Write-Host ("  Queued: {0} -> {1}" -f $t.Name, (Split-Path $dest -Leaf)) -ForegroundColor Green
    }
    $next++
}

if (-not $DryRun) {
    Write-Host ""
    Write-Host "Done. Run .\Run-Maid.ps1 -Once to process the queue." -ForegroundColor Cyan
}
