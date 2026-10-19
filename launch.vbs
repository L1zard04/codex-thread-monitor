Option Explicit
Dim shell, fs, folder, runtimeFile, python, app, command, f
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
runtimeFile = fs.BuildPath(folder, "python-path.txt")
python = shell.ExpandEnvironmentStrings("%CODEX_MONITOR_PYTHON%")
If python = "%CODEX_MONITOR_PYTHON%" Then
    python = ""
    If fs.FileExists(runtimeFile) Then
        Set f = fs.OpenTextFile(runtimeFile, 1, False, -1)
        python = Trim(Replace(Replace(f.ReadAll, vbCr, ""), vbLf, ""))
        f.Close
    End If
End If
If Not fs.FileExists(python) Then
    If WScript.Arguments.Named.Exists("check") Then
        WScript.Echo "Python runtime not found: " & python
        WScript.Quit 1
    End If
    MsgBox "Python runtime not found. See README.md or set CODEX_MONITOR_PYTHON to pythonw.exe.", 16, "Codex Thread Monitor"
    WScript.Quit 1
End If
app = fs.BuildPath(folder, "app.py")
command = Chr(34) & python & Chr(34) & " " & Chr(34) & app & Chr(34)
shell.CurrentDirectory = folder
If WScript.Arguments.Named.Exists("check") Then
    WScript.Quit shell.Run(command & " --check", 0, True)
End If
shell.Run command, 0, False
