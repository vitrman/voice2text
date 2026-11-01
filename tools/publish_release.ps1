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
    [switch]$DryRun,
    [switch]$Forget,
    [switch]$SaveToken
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

# sha256 рядом с архивом: приложение сверяет его перед установкой
$hashFile = "$($archive.FullName).sha256"
$hash = (Get-FileHash -Algorithm SHA256 -Path $archive.FullName).Hash.ToLower()
Set-Content -Path $hashFile -Value $hash -Encoding ascii -NoNewline
Write-Host "SHA256:  $hash"

# Release notes: the section of CHANGELOG.md for this version.
# CHANGELOG в UTF-8, а powershell.exe (5.1) по умолчанию читает файлы как ANSI:
# без -Encoding UTF8 русский текст превращался в мусор ещё до отправки.
$notes = ""
$startLine = 0
$lines = Get-Content (Join-Path $root "CHANGELOG.md") -Encoding UTF8
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
[IO.File]::WriteAllText($notesFile, $notes, (New-Object System.Text.UTF8Encoding($false)))
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

# Токен можно один раз сохранить: файл шифруется DPAPI для текущей учётной записи
# Windows, поэтому другой пользователь или другая машина его не прочитают.
# Лежит в %LOCALAPPDATA%\voice2text\github.token и в репозиторий не попадает.
$TokenFile = Join-Path $env:LOCALAPPDATA "voice2text\github.token"

function Get-StoredToken {
    if (-not (Test-Path $TokenFile)) { return "" }
    try {
        Add-Type -AssemblyName System.Security
        $bytes = [IO.File]::ReadAllBytes($TokenFile)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, "CurrentUser")
        return [Text.Encoding]::UTF8.GetString($plain).Trim()
    } catch {
        Write-Host "Сохранённый токен не читается (файл от другой учётной записи?)." -ForegroundColor Yellow
        return ""
    }
}

function Save-StoredToken([string]$value) {
    if (-not $value) { return }
    Add-Type -AssemblyName System.Security
    $folder = Split-Path -Parent $TokenFile
    if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
    $bytes = [Text.Encoding]::UTF8.GetBytes($value)
    $protected = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, "CurrentUser")
    [IO.File]::WriteAllBytes($TokenFile, $protected)
}

function Remove-StoredToken {
    if (Test-Path $TokenFile) { Remove-Item $TokenFile -Force }
}

if ($Forget) {
    Remove-StoredToken
    Write-Host "Сохранённый токен удалён: $TokenFile"
    exit 0
}

if (-not $Token) { $Token = Get-StoredToken }

if (-not $Token -and (Test-Gh)) {
    Write-Host "Publishing through GitHub CLI..."
    & gh release view $tag --repo $Repo 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & gh release upload $tag $archive.FullName $hashFile --repo $Repo --clobber
    } else {
        & gh release create $tag $archive.FullName $hashFile --repo $Repo --title "Voice2Text $version" --notes-file $notesFile
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
    if ($Token) {
        $answer = if ($SaveToken) { "y" } else { Read-Host "Сохранить токен на этой машине? [y/N]" }
        if ($answer -match "^(y|Y|д|Д)$") {
            Save-StoredToken $Token
            Write-Host "Токен сохранён (DPAPI, только для вашей учётной записи): $TokenFile"
        }
    }
}
if (-not $Token) { throw "No GitHub token: nothing to publish with" }

$headers = @{
    Authorization          = "token $Token"
    Accept                 = "application/vnd.github+json"
    "User-Agent"           = "Voice2Text-publisher"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Host "Checking repository $Repo..."
try {
    $repoInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo" -Headers $headers -Method Get
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 404) {
        Write-Host "Репозиторий $Repo не найден или токену не выдан доступ к нему." -ForegroundColor Red
        Write-Host "  - проверьте имя: release_github.cmd -Repo owner/name (сейчас $Repo)"
        Write-Host "  - classic-токен: нужен scope repo"
        Write-Host "  - fine-grained: выберите этот репозиторий и право Contents: Read and write"
        Write-Host "  - если сохранён неверный токен: release_github.cmd -Forget"
        exit 1
    }
    if ($code -eq 401) {
        Write-Host "GitHub не принял токен (401). Удалите сохранённый: release_github.cmd -Forget" -ForegroundColor Red
        exit 1
    }
    $detail = $_.ErrorDetails.Message
    if ($detail) { Write-Host "GitHub: $detail" -ForegroundColor Red }
    throw
}
Write-Host "Repository: $($repoInfo.full_name) ($($repoInfo.visibility))"

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
    } | ConvertTo-Json -Depth 5
    # GitHub ждёт UTF-8: если отдать строку, powershell.exe кодирует её в ANSI,
    # кириллица превращается в невалидный UTF-8 и приходит "Problems parsing JSON"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" `
            -Headers $headers -Method Post -Body $bytes `
            -ContentType "application/json; charset=utf-8"
    } catch {
        $detail = $_.ErrorDetails.Message
        if ($detail) { Write-Host "GitHub: $detail" -ForegroundColor Red }
        throw
    }
} else {
    Write-Host "Release exists, uploading asset..."
}

$uploadUrl = "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$($archive.Name)"
Write-Host "Uploading $($archive.Name)..."
Invoke-RestMethod -Uri $uploadUrl -Headers $headers -Method Post -InFile $archive.FullName -ContentType "application/zip" | Out-Null

$hashUrl = "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$([IO.Path]::GetFileName($hashFile))"
Write-Host "Uploading $([IO.Path]::GetFileName($hashFile))..."
Invoke-RestMethod -Uri $hashUrl -Headers $headers -Method Post -InFile $hashFile -ContentType "text/plain" | Out-Null
Write-Host "Release page: https://github.com/$Repo/releases/tag/$tag"
