# Local code signing for the built exe (self-signed certificate).
# Without -Trust the signature is applied but SmartScreen may still warn.
# With -Trust the certificate is added to trusted stores (Windows shows one prompt).

param(
    [string]$Exe = "dist\Voice2Text\Voice2Text.exe",
    [switch]$Trust
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) {
    Write-Host "Not found: $Exe - run build.cmd first"
    exit 1
}
$exePath = (Resolve-Path $Exe).Path

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -like "*CN=Voice2Text*" } |
    Select-Object -First 1

if (-not $cert) {
    Write-Host "Creating self-signed certificate..."
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject "CN=Voice2Text, O=Voice2Text, C=RU" `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(3) `
        -KeyUsage DigitalSignature
}

try {
    $result = Set-AuthenticodeSignature -FilePath $exePath -Certificate $cert `
        -TimestampServer "http://timestamp.digicert.com"
} catch {
    Write-Host "Timestamp server unavailable, signing without it"
    $result = Set-AuthenticodeSignature -FilePath $exePath -Certificate $cert
}
Write-Host "Signature status: $($result.Status)"

if ($Trust) {
    $tmp = Join-Path $env:TEMP "voice2text-cert.cer"
    Export-Certificate -Cert $cert -FilePath $tmp | Out-Null
    foreach ($store in @("Root", "TrustedPublisher")) {
        try {
            Import-Certificate -FilePath $tmp -CertStoreLocation "Cert:\CurrentUser\$store" | Out-Null
            Write-Host "Certificate added to $store"
        } catch {
            Write-Host "Failed to add to $store : $($_.Exception.Message)"
        }
    }
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    Write-Host "Done: SmartScreen should stay quiet on this PC."
}
