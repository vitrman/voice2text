@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -like '*CN=Voice2Text*' } | Select-Object -First 1;" ^
  "if (-not $c) { Write-Host 'No certificate. Run build.cmd first.'; exit 1 };" ^
  "Export-Certificate -Cert $c -FilePath \"$env:TEMP\voice2text.cer\" | Out-Null;" ^
  "certutil -user -addstore Root \"$env:TEMP\voice2text.cer\";" ^
  "certutil -user -addstore TrustedPublisher \"$env:TEMP\voice2text.cer\";" ^
  "Remove-Item \"$env:TEMP\voice2text.cer\" -Force"
echo.
echo Done. Windows asked once about the certificate - that is expected.
pause
