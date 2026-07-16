<#
.SYNOPSIS
    Platform installer shim for the Relay plugin (Windows / PowerShell).

.DESCRIPTION
    install.py is the Python bootstrap/configuration layer. This wrapper
    locates a usable Python 3.10+ interpreter and forwards every argument.

.NOTES
    If ExecutionPolicy blocks the script, invoke it explicitly:
        pwsh -ExecutionPolicy Bypass -File .\scripts\install.ps1 --hooks all
#>

# Deliberately no param() block: automatic $args is forwarded verbatim.
$ErrorActionPreference = 'Stop'

$InstallPy = Join-Path $PSScriptRoot 'install.py'
if (-not (Test-Path -LiteralPath $InstallPy)) {
    Write-Host "install.ps1: cannot find sibling install.py at '$InstallPy'." -ForegroundColor Red
    exit 2
}

$Verify = 'import sys; print("%d.%d.%d" % sys.version_info[:3]); sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)'

function Get-UsablePython {
    param([string[]]$BaseArgs)
    if (-not (Get-Command -Name $BaseArgs[0] -ErrorAction SilentlyContinue)) { return $null }
    $probeArgs = @()
    if ($BaseArgs.Length -gt 1) { $probeArgs += $BaseArgs[1..($BaseArgs.Length - 1)] }
    $probeArgs += @('-c', $Verify)
    try {
        $out = & $BaseArgs[0] @probeArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out -join ' ').Trim() }
    } catch { }
    return $null
}

$candidates = @()
if ($env:RELAY_PYTHON) {
    $candidates += ,@($env:RELAY_PYTHON)
} elseif ($env:CONVERSATE_PYTHON) {
    # Legacy compatibility only; new configuration should use RELAY_PYTHON.
    $candidates += ,@($env:CONVERSATE_PYTHON)
}
$candidates += ,@('py', '-3')
$candidates += ,@('python')
$candidates += ,@('python3')

$resolved = $null
$pyVer = $null
foreach ($cand in $candidates) {
    $ver = Get-UsablePython -BaseArgs $cand
    if ($ver) { $resolved = $cand; $pyVer = $ver; break }
}

if (-not $resolved) {
    Write-Host 'install.ps1: no usable Python 3.10+ found.' -ForegroundColor Red
    Write-Host '  - Set $env:RELAY_PYTHON to a Python path, or install Python 3.10+.' -ForegroundColor Yellow
    exit 127
}

$exe = $resolved[0]
$pre = @()
if ($resolved.Length -gt 1) { $pre = $resolved[1..($resolved.Length - 1)] }
Write-Host "install.ps1: using Python $pyVer via ($($resolved -join ' '))"
$runArgs = @() + $pre + $InstallPy + $args
& $exe @runArgs
exit $LASTEXITCODE
