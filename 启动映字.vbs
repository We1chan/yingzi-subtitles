Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
If fs.FileExists(root & "\.venv\Scripts\pythonw.exe") Then
    shell.Run """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\main.py""", 1, False
Else
    MsgBox "Please run setup.cmd first.", 48, "Yingzi"
End If
