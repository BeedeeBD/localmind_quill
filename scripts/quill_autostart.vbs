' Start Quill (localmind) at logon, with no console window, then open the UI.
'
' This is what the Startup shortcut runs. It launches the server via pythonw.exe
' (so nothing flashes on screen), waits for it to come up, and opens the browser
' at the served URL — the only way the app should be opened, so its relative
' requests resolve.
'
' Register it with scripts\install_startup.ps1 ; remove it with
' scripts\uninstall_startup.ps1 (or just delete the "Quill" shortcut from your
' Startup folder).

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Project root = the parent of this script's own folder (…\localmind\scripts\).
root  = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pyw   = root & "\.venv\Scripts\pythonw.exe"
serve = root & "\serve.py"

If Not fso.FileExists(pyw) Then
  MsgBox "Quill auto-start: could not find " & pyw & vbCrLf & _
         "Create the virtual environment first: py -3.12 -m venv .venv", _
         48, "Quill"
  WScript.Quit 1
End If

sh.CurrentDirectory = root
' Window style 0 = hidden; False = don't wait for it to exit.
sh.Run """" & pyw & """ """ & serve & """", 0, False

' Give the server a few seconds to bind, then open the browser at the served URL.
WScript.Sleep 5000
sh.Run "http://127.0.0.1:8000/", 1, False
