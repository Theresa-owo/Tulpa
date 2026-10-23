#Requires -Version 7.0
<#
.SYNOPSIS
Checks upstream changes, or explicitly merges them while retaining fork history.
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$Apply,
    [switch]$Push,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Invoke-Git {
    param([string[]]$Arguments, [switch]$AllowMissing)
    $output = @(& git -C $RepoPath @Arguments 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not ($AllowMissing -and $code -eq 1)) {
        # Git diagnostics can contain credential-bearing remote URLs.
        throw "Git operation '$($Arguments[0])' failed (exit $code). Inspect the local repository; no reset, rebase or force push was attempted."
    }
    if ($code -eq 0) { $output | ForEach-Object { "$_" } }
}

function Get-RepositoryIdentity {
    param([string]$Url)
    if ($Url -match '^git@github\.com:([^/\s:]+)/([^/\s?#]+?)(?:\.git)?/?$') {
        return "github.com/$($Matches[1])/$($Matches[2])".ToLowerInvariant()
    }
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri)) {
        throw 'Remote must use a GitHub HTTPS or SSH URL.'
    }
    $validScheme = $uri.Scheme -eq 'https' -or $uri.Scheme -eq 'ssh'
    $validUser = ($uri.Scheme -eq 'https' -and -not $uri.UserInfo) -or ($uri.Scheme -eq 'ssh' -and $uri.UserInfo -eq 'git')
    if (-not $validScheme -or -not $validUser -or $uri.Host -ne 'github.com' -or -not $uri.IsDefaultPort -or $uri.Query -or $uri.Fragment) {
        throw 'Remote must use a standard GitHub HTTPS or SSH URL without embedded credentials.'
    }
    if ($uri.AbsolutePath -notmatch '^/([^/\s%:]+)/([^/\s%:]+?)(?:\.git)?/?$') {
        throw 'Remote must identify exactly one GitHub owner and repository.'
    }
    return "github.com/$($Matches[1])/$($Matches[2])".ToLowerInvariant()
}

function Assert-Remotes {
    foreach ($entry in @(@{ Name = 'origin'; Expected = $manifest.fork_repository }, @{ Name = 'upstream'; Expected = $manifest.upstream_repository })) {
        $urls = @(Invoke-Git -Arguments @('config', '--get-all', "remote.$($entry.Name).url"))
        $pushUrls = @(Invoke-Git -Arguments @('config', '--get-all', "remote.$($entry.Name).pushurl") -AllowMissing)
        if ($urls.Count -ne 1 -or $pushUrls.Count -gt 1) { throw "Remote '$($entry.Name)' must have exactly one fetch destination and at most one push destination." }
        foreach ($url in @($urls) + @($pushUrls)) {
            if ((Get-RepositoryIdentity $url) -ne $entry.Expected.ToLowerInvariant()) {
                throw "Remote '$($entry.Name)' does not match fork-manifest.json."
            }
        }
    }
}

function Assert-CleanMain {
    $branch = @(Invoke-Git -Arguments @('symbolic-ref', '--quiet', '--short', 'HEAD'))
    if ($branch.Count -ne 1 -or $branch[0] -ne $manifest.branch) { throw "Check out the fork's '$($manifest.branch)' branch first." }
    $null = Invoke-Git -Arguments @('rev-parse', '--verify', 'HEAD')
    if (@(Invoke-Git -Arguments @('status', '--porcelain', '--untracked-files=normal')).Count) {
        throw 'Working tree is dirty. Commit or preserve those changes before synchronizing.'
    }
    foreach ($marker in @('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply')) {
        $markerPath = Invoke-Git -Arguments @('rev-parse', '--git-path', $marker)
        if (-not [IO.Path]::IsPathRooted($markerPath)) { $markerPath = Join-Path $RepoPath $markerPath }
        if (Test-Path -LiteralPath $markerPath) { throw 'Finish or abort the existing Git operation before synchronizing.' }
    }
}

function Test-Ancestor {
    param([string]$Ancestor, [string]$Descendant)
    $null = Invoke-Git -Arguments @('merge-base', '--is-ancestor', $Ancestor, $Descendant) -AllowMissing
    return $LASTEXITCODE -eq 0
}

try {
    $RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
    $root = Invoke-Git -Arguments @('rev-parse', '--show-toplevel')
    if ([IO.Path]::GetFullPath($root).TrimEnd('/', '\') -ne [IO.Path]::GetFullPath($RepoPath).TrimEnd('/', '\')) {
        throw 'RepoPath must be the Git working-tree root.'
    }
    $manifest = Get-Content -LiteralPath (Join-Path $RepoPath 'fork-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.branch -ne 'main' -or -not $manifest.fork_repository -or -not $manifest.upstream_repository -or $manifest.fork_repository -eq $manifest.upstream_repository) {
        throw 'Unsupported or incomplete fork-manifest.json.'
    }
    if (-not $manifest.required_checks -or @($manifest.required_checks).Count -eq 0) { throw 'At least one required fork check must be configured.' }
    Assert-Remotes
    Assert-CleanMain
    foreach ($remote in @('upstream', 'origin')) {
        $null = Invoke-Git -Arguments @('fetch', '--no-tags', $remote, "refs/heads/main:refs/remotes/$remote/main")
    }
    $counts = (Invoke-Git -Arguments @('rev-list', '--left-right', '--count', 'HEAD...upstream/main')).Trim() -split '\s+'
    Write-Output "Fork versus upstream/main: ahead $($counts[0]), behind $($counts[1])."
    if (-not $Apply -and -not $Push) {
        Write-Output 'Check complete. Use -Apply to merge and run fork checks; add -Push to publish after successful validation.'
        exit 0
    }
    if (-not (Test-Ancestor 'origin/main' 'HEAD')) { throw 'origin/main has commits absent from this checkout. Integrate them before synchronizing.' }
    if ($Apply) {
        $null = Invoke-Git -Arguments @('merge', '--no-edit', 'upstream/main')
        Write-Output 'Upstream merge completed; fork history retained.'
    }
    if (-not (Test-Ancestor 'upstream/main' 'HEAD')) { throw 'Upstream changes are not integrated. Run with -Apply before pushing.' }
    $validatedHead = Invoke-Git -Arguments @('rev-parse', 'HEAD')
    foreach ($check in $manifest.required_checks) {
        if ($check -notmatch '^scripts/[A-Za-z0-9_/-]+\.py$' -or $check -match '(^|/)\.\.(/|$)') { throw 'Invalid required check path in fork-manifest.json.' }
        $checkPath = Join-Path $RepoPath $check
        if (-not (Test-Path -LiteralPath $checkPath -PathType Leaf)) { throw "Required fork check is missing: $check" }
        Write-Output "Running $check"
        Push-Location -LiteralPath $RepoPath
        try {
            & $Python $checkPath
            if ($LASTEXITCODE -ne 0) { throw "Required fork check failed: $check" }
        } finally { Pop-Location }
    }
    Assert-CleanMain
    Assert-Remotes
    if ((Invoke-Git -Arguments @('rev-parse', 'HEAD')) -ne $validatedHead) { throw 'HEAD changed during validation; refusing to push.' }
    if ($Push) {
        $null = Invoke-Git -Arguments @('push', 'origin', 'refs/heads/main:refs/heads/main')
        Write-Output 'Validated main pushed to origin without force.'
    } else {
        Write-Output 'Fork checks passed. Changes remain local; -Push can validate and publish them later.'
    }
} catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
