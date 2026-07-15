# Stop Quill (localmind) from starting at logon: removes the Startup shortcut
# that install_startup.ps1 created. Any running server is left alone.
#
#     powershell -ExecutionPolicy Bypass -File scripts\uninstall_startup.ps1

$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Quill.lnk"

if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Host "Removed startup shortcut: $lnk" -ForegroundColor Green
} else {
    Write-Host "No Quill startup shortcut found — nothing to remove."
}
