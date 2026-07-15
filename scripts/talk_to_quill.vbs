' Talk to Quill: open the chat window.
'
' Clicking the "Talk to Quill" shortcut runs this. It reuses the server if one is
' already up (e.g. from the logon auto-start), otherwise it starts one quietly
' (no console window), waits for it, and then opens the chat UI in your browser.
' The UI must be opened from the server like this so its requests resolve.

Dim sh, fso, root, url
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
url  = "http://127.0.0.1:8000/"

Function ServerUp()
  Dim http
  ServerUp = False
  On Error Resume Next
  Set http = CreateObject("MSXML2.XMLHTTP")
  http.Open "GET", url & "health", False
  http.Send
  If Err.Number = 0 Then ServerUp = (http.Status = 200)
  On Error GoTo 0
End Function

If Not ServerUp() Then
  Dim pyw
  pyw = root & "\.venv\Scripts\pythonw.exe"
  If Not fso.FileExists(pyw) Then
    MsgBox "Can't find " & pyw & vbCrLf & _
           "Create the virtual environment first: py -3.12 -m venv .venv", _
           48, "Talk to Quill"
    WScript.Quit 1
  End If
  sh.CurrentDirectory = root
  sh.Run """" & pyw & """ """ & root & "\serve.py""", 0, False
  ' Wait up to ~25s for the server to bind.
  Dim i
  For i = 1 To 25
    WScript.Sleep 1000
    If ServerUp() Then Exit For
  Next
End If

sh.Run url, 1, False
