Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
If fs.FileExists(root & "\.venv\Scripts\pythonw.exe") Then
    command = """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\main.py"""
    If WScript.Arguments.Count > 0 Then
        command = command & " """ & Replace(WScript.Arguments(0), """", "") & """"
    End If
    shell.Run command, 1, False
Else
    MsgBox "Please run setup.cmd first.", 48, "Yingzi"
End If
