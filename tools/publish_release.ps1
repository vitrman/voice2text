# Publishes a GitHub release with the installer from dist-installer.
# Works through GitHub CLI (gh) if signed in, otherwise through the REST API
# with a personal access token (repo scope).
#
#   -DryRun        print what would be published, no network calls
#   -Token <pat>   use this token instead of asking / $env:GITHUB_TOKEN
#   -Repo owner/name  override the repository (default vitrman/voice2text)

param(
    [string]$Token = $env:GITHUB_TOKEN,
    [string]$Repo = "vitrman/voice2text",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$versionText = Get-Content (Join-Path $root "version.py") -Raw
$match = [regex]::Match($versionText, 'VERSION\s*=\s*"([^"]+)"')
if (-not $match.Success) { throw "version.py: VERSION not found" }
$version = $match.Groups[1].Value
$tag = "v$version"

$archive = Get-ChildItem (Join-Path $root "dist-installer") -Filter "Voice2Text-$version-installer.zip" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $archive) {
    Write-Host "Installer not found: dist-installer\Voice2Text-$version-installer.zip" -ForegroundColor Red
    Write-Host "Build it first: release.cmd"
    exit 1
}
Write-Host "Version: $version"
Write-Host "Archive: $($archive.FullName) ($([math]::Round($archive.Length / 1MB)) MB)"
Write-Host "Tag:     $tag"
Write-Host "Repo:    $Repo"

# Release notes: the section of CHANGELOG.md for this version.
$notes = ""
$startLine = 0
$lines = Get-Content (Join-Path $root "CHANGELOG.md")
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match ("^##\s+" + [regex]::Escape($version) + "\s*$")) {
        $startLine = $i + 1
        break
    }
}
if ($startLine -gt 0) {
    $collected = @()
    for ($i = $startLine; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^##\s") { break }
        $collected += $lines[$i]
    }
    $notes = ($collected -join "`n").Trim()
}
if (-not $notes) { $notes = "Voice2Text $version" }
$notesFile = Join-Path $env:TEMP "voice2text-notes-$version.md"
Set-Content -Path $notesFile -Value $notes -Encoding UTF8
Write-Host "Notes:   $notesFile"

if ($DryRun) {
    Write-Host "Dry run: nothing was sent to GitHub."
    exit 0
}

function Test-Gh {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { return $false }
    & gh auth status 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not $Token -and (Test-Gh)) {
    Write-Host "Publishing through GitHub CLI..."
    & gh release view $tag --repo $Repo 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & gh release upload $tag $archive.FullName --repo $Repo --clobber
    } else {
        & gh release create $tag $archive.FullName --repo $Repo --title "Voice2Text $version" --notes-file $notesFile
    }
    if ($LASTEXITCODE -ne 0) { throw "gh release failed with code $LASTEXITCODE" }
    Write-Host "Release page: https://github.com/$Repo/releases/tag/$tag"
    exit 0
}

if (-not $Token) {
    $secure = Read-Host "GitHub token with repo scope (input hidden)" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}
if (-not $Token) { throw "No GitHub token: nothing to publish with" }

$headers = @{
    Authorization          = "token $Token"
    Accept                 = "application/vnd.github+json"
    "User-Agent"           = "Voice2Text-publisher"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Host "Looking for release $tag..."
$release = $null
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$tag" -Headers $headers -Method Get
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -ne 404) { throw }
}

if (-not $release) {
    Write-Host "Creating release $tag..."
    $body = @{
        tag_name   = $tag
        name       = "Voice2Text $version"
        body       = $notes
        draft      = $false
        prerelease = $false
    } | ConvertTo-Json
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -Headers $headers -Method Post -Body $body -ContentType "application/json"
} else {
    Write-Host "Release exists, uploading asset..."
}

$uploadUrl = "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$($archive.Name)"
Write-Host "Uploading $($archive.Name)..."
Invoke-RestMethod -Uri $uploadUrl -Headers $headers -Method Post -InFile $archive.FullName -ContentType "application/zip" | Out-Null
Write-Host "Release page: https://github.com/$Repo/releases/tag/$tag"
