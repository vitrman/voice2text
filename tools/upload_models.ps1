# Uploads GigaAM model files to a GitHub release, so the app can download them
# when the Sber CDN is unreachable (fallback source in model_fetch.py).
#
#   -DryRun            show what would be uploaded, no network calls
#   -Token <pat>       token with repo scope (default: stored token or $env:GITHUB_TOKEN)
#   -Source <folder>   folder with the .ckpt files (default: app cache)
#   -Models a,b        which models to upload (default: v3_e2e_rnnt,v3_e2e_ctc)
#
# Example:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\upload_models.ps1 -DryRun

param(
    [string]$Token = $env:GITHUB_TOKEN,
    [string]$Repo = "vitrman/voice2text",
    [string]$Tag = "models-v1",
    [string]$Source = "",
    [string[]]$Models = @("v3_e2e_rnnt"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $Source) {
    $Source = Join-Path $env:LOCALAPPDATA "voice2text\.cache\gigaam"
}

$hashes = @{
    "v3_e2e_rnnt" = "2730de7545ac43ad256485a462b0a27a"
    "v3_e2e_ctc"  = "367074d6498f426d960b25f49531cf68"
}

$tokenFile = Join-Path $env:LOCALAPPDATA "voice2text\github.token"

function Get-StoredToken {
    if (-not (Test-Path $tokenFile)) { return "" }
    try {
        Add-Type -AssemblyName System.Security
        $bytes = [IO.File]::ReadAllBytes($tokenFile)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, "CurrentUser")
        return [Text.Encoding]::UTF8.GetString($plain).Trim()
    } catch {
        return ""
    }
}

if (-not $Token) { $Token = Get-StoredToken }

$files = @()
foreach ($model in $Models) {
    foreach ($suffix in @(".ckpt", "_tokenizer.model")) {
        $file = Join-Path $Source ($model + $suffix)
        if (-not (Test-Path $file)) {
            Write-Host "нет файла: $file" -ForegroundColor Red
            exit 1
        }
        $item = Get-Item $file
        $print = [math]::Round($item.Length / 1MB, 0)
        if ($suffix -eq ".ckpt") {
            $md5 = (Get-FileHash $file -Algorithm MD5).Hash.ToLower()
            if ($hashes.ContainsKey($model) -and $md5 -ne $hashes[$model]) {
                Write-Host "md5 не совпал у $($item.Name): $md5" -ForegroundColor Red
                exit 1
            }
        }
        $files += [pscustomobject]@{ Path = $file; Name = $item.Name; MB = $print }
    }
}

Write-Host "релиз: $Repo, тег $Tag"
foreach ($file in $files) { Write-Host ("  {0}  {1} МБ" -f $file.Name, $file.MB) }

if ($DryRun) {
    Write-Host "DryRun: ничего не отправлено."
    exit 0
}

if (-not $Token) {
    Write-Host "Нужен токен: параметр -Token или переменная GITHUB_TOKEN (scope repo)." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "Нужен curl.exe (входит в Windows 10/11)." -ForegroundColor Red
    exit 1
}

$auth = "Authorization: Bearer $Token"
$api = "https://api.github.com/repos/$Repo"

# 1) release by tag, create when missing
$release = $null
$existing = & curl.exe -sS -H $auth "$api/releases/tags/$Tag"
try { $release = $existing | ConvertFrom-Json } catch { $release = $null }
if (-not $release -or -not $release.id) {
    Write-Host "создаю релиз $Tag"
    $what = @{
        tag_name = $Tag
        name = "Модели GigaAM v3 (резервный источник)"
        body = "Файлы моделей для запасного источника загрузки. Приложение берёт их, если CDN Сбера недоступен."
        draft = $false
        prerelease = $false
    }
    # JSON кладём в файл: PowerShell съедает кавычки, если передавать строку
    # аргументом нативному curl.exe — GitHub отвечает "Problems parsing JSON"
    $jsonFile = Join-Path $env:TEMP "voice2text-release.json"
    [IO.File]::WriteAllText($jsonFile, ($what | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    $created = & curl.exe -sS -X POST -H $auth -H "Content-Type: application/json" --data-binary "@$jsonFile" "$api/releases"
    Remove-Item $jsonFile -ErrorAction SilentlyContinue
    $release = $created | ConvertFrom-Json
    if (-not $release.id) {
        Write-Host "не удалось создать релиз: $created" -ForegroundColor Red
        exit 1
    }
}

# 2) upload assets (skip names that already exist)
$assets = @()
$list = & curl.exe -sS -H $auth "$api/releases/$($release.id)/assets"
try { $assets = ($list | ConvertFrom-Json) | ForEach-Object { $_.name } } catch { $assets = @() }

foreach ($file in $files) {
    if ($assets -contains $file.Name) {
        Write-Host "уже есть: $($file.Name)"
        continue
    }
    Write-Host "загружаю $($file.Name) ($($file.MB) МБ)..."
    $answer = & curl.exe -sS -X POST -H $auth -H "Content-Type: application/octet-stream" `
        --data-binary "@$($file.Path)" `
        "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$($file.Name)"
    $asset = $null
    try { $asset = $answer | ConvertFrom-Json } catch { $asset = $null }
    if ($LASTEXITCODE -ne 0 -or -not $asset -or -not $asset.id) {
        Write-Host "ошибка загрузки $($file.Name): $answer" -ForegroundColor Red
        exit 1
    }
}

Write-Host "готово: https://github.com/$Repo/releases/tag/$Tag"
exit 0
