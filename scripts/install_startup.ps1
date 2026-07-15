# Register Quill (localmind) to start automatically at logon.
#
# This drops a single shortcut into your personal Startup folder that runs
# scripts\quill_autostart.vbs — which starts the server hidden and opens the UI.
# It is entirely reversible: run uninstall_startup.ps1, or delete the shortcut.
#
# Run once from a normal (non-admin) PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1

$ErrorActionPreference = "Stop"

$vbs = Join-Path $PSScriptRoot "quill_autostart.vbs"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $vbs)) { throw "Missing $vbs" }

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Quill.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$sc.Arguments = '"' + $vbs + '"'
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7                     # minimised/hidden
$sc.Description = "Start Quill (localmind) at logon"
$sc.Save()

Write-Host "Installed startup shortcut:" -ForegroundColor Green
Write-Host "  $lnk"
Write-Host ""
Write-Host "Quill will start automatically at your next logon."
Write-Host "To start it right now without rebooting:"
Write-Host "  wscript `"$vbs`""
Write-Host ""
Write-Host "Note: Quill needs Ollama running. Ollama's installer sets it to launch"
Write-Host "at login by default; if you disabled that, re-enable it or start Ollama"
Write-Host "before using the chat."
