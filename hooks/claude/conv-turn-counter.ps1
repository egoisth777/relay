#!/usr/bin/env pwsh
# conversate turn counter — Claude Code UserPromptSubmit hook.
#
# Reads the hook's stdin JSON (which carries `session_id`), keeps a per-session counter
# file in $env:TEMP keyed by that session id, and increments it once per user prompt. Once
# the count reaches 10 (and every 10 prompts after) it prints a CONV AUTO-SAVE reminder on
# stdout; Claude Code injects UserPromptSubmit stdout into the agent's context, so the conv
# skill sees it and runs a silent save.
#
# Dependency-free. Keyed by session_id (not PID) so parallel/reused shells stay distinct.
# Safe to wire user-level: projects without a .conversate store are a fast, silent no-op.
# Never blocks the prompt: any error exits 0 with no output.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    $session = 'default'
    $cwd = (Get-Location).Path
    if ($raw) {
        $data = $raw | ConvertFrom-Json
        if ($data.session_id) { $session = [string]$data.session_id }
        if ($data.cwd) { $cwd = [string]$data.cwd }
    }
    # only count in projects that use conversate (have a .conversate store)
    if (-not (Test-Path -LiteralPath (Join-Path $cwd '.conversate') -PathType Container)) { exit 0 }
    # no session id -> key by project path, never one global shared counter
    if ($session -eq 'default') { $session = "cwd-$cwd" }
    # sanitize the session id so it is safe as a file name
    $safe = ($session -replace '[^A-Za-z0-9_.-]', '_')
    $counterFile = Join-Path $env:TEMP "conv-session-$safe.count"

    $count = 0
    if (Test-Path $counterFile) {
        $count = [int]((Get-Content -Raw -LiteralPath $counterFile).Trim())
    }
    $count++
    Set-Content -LiteralPath $counterFile -Value $count -Encoding ascii

    if ($count -ge 10 -and ($count % 10) -eq 0) {
        Write-Output "CONV AUTO-SAVE: threshold reached - consider saving conversation state via the conversate skill"
    }
} catch {
    # never break the user's prompt on a counter error
}
exit 0
