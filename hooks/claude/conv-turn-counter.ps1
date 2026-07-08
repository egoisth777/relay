#!/usr/bin/env pwsh
# Conversate turn counter - Claude Code UserPromptSubmit hook.
#
# Reads the hook's stdin JSON (which carries `session_id`), keeps a per-session counter
# file in the OS temp directory keyed by a hash of that session id, and increments it once
# per user prompt. Once the count reaches 10 (and every 10 prompts after) it prints a
# CONVERSATE AUTO-SAVE reminder on stdout; Claude Code injects UserPromptSubmit stdout
# agent's context, so the Conversate skill sees it and runs a silent save.
#
# Dependency-free. Keyed by session_id (not PID) so parallel/reused shells stay distinct.
# Safe to wire user-level: missing installed Conversation database is a fast, silent no-op.
# Never blocks the prompt: any error exits 0 with no output.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw.Trim()) { exit 0 }
    if (-not $raw.TrimStart().StartsWith('{')) { exit 0 }
    $data = $raw | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $data -or $data -isnot [System.Management.Automation.PSCustomObject]) { exit 0 }
    if ($data.hook_event_name -ne 'UserPromptSubmit') { exit 0 }
    $session = [string]$data.session_id
    if ([string]::IsNullOrWhiteSpace($session)) { exit 0 }
    $session = $session.Trim()
    $scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $pluginRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot 'convs') -PathType Container)) { exit 0 }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($session)
        $safe = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
    } finally {
        $sha.Dispose()
    }
    $safe = $safe.Substring(0, 16)
    $counterFile = Join-Path ([System.IO.Path]::GetTempPath()) "conversate-claude-turns-$safe.count"

    $count = 0
    if (Test-Path -LiteralPath $counterFile) {
        [int]$parsedCount = 0
        $rawCount = (Get-Content -Raw -LiteralPath $counterFile).Trim()
        if ([int]::TryParse($rawCount, [ref]$parsedCount) -and $parsedCount -ge 0) {
            $count = $parsedCount
        }
    }
    $count++
    Set-Content -LiteralPath $counterFile -Value $count -Encoding ascii

    if ($count -ge 10 -and ($count % 10) -eq 0) {
        Write-Output "CONVERSATE AUTO-SAVE: threshold reached - run /conversate:save via the Conversate plugin, then continue."
    }
} catch {
    # never break the user's prompt on a counter error
}
exit 0
