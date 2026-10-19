Option Explicit
Dim shell, fs, folder, dataFolder, enabledFile, startupLink, python, f, link
Dim watcher, wantedArgs, existingLink, disable, check
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
dataFolder = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\CodexThreadMonitor"
enabledFile = fs.BuildPath(dataFolder, "autostart.enabled")
startupLink = fs.BuildPath(shell.SpecialFolders("Startup"), "CodexThreadMonitorAutoStart.lnk")
watcher = fs.BuildPath(folder, "watcher.py")
wantedArgs = Chr(34) & watcher & Chr(34)
disable = WScript.Arguments.Named.Exists("disable")
check = WScript.Arguments.Named.Exists("check")

If check Then
    WScript.Echo "Startup shortcut: " & CStr(fs.FileExists(startupLink))
    WScript.Echo "Enabled flag: " & CStr(fs.FileExists(enabledFile))
    If fs.FileExists(startupLink) And fs.FileExists(enabledFile) Then
        Set link = shell.CreateShortcut(startupLink)
        If link.Arguments = wantedArgs Then WScript.Quit 0
    End If
    WScript.Quit 1
End If

If fs.FileExists(startupLink) Then
    Set existingLink = shell.CreateShortcut(startupLink)
    If existingLink.Arguments <> wantedArgs Then
        WScript.Echo "A different shortcut already uses this name. No files changed."
        WScript.Quit 1
    End If
End If

If disable Then
    If fs.FileExists(startupLink) Then fs.DeleteFile startupLink
    If fs.FileExists(enabledFile) Then fs.DeleteFile enabledFile
    WScript.Echo "Codex monitor auto-start disabled. The current window is kept open."
    WScript.Quit 0
End If

Set f = fs.OpenTextFile(fs.BuildPath(folder, "python-path.txt"), 1, False, -1)
python = Trim(Replace(Replace(f.ReadAll, vbCr, ""), vbLf, ""))
f.Close
If Not fs.FileExists(python) Then
    WScript.Echo "Python runtime not found. Check python-path.txt."
    WScript.Quit 1
End If
If Not fs.FolderExists(dataFolder) Then fs.CreateFolder dataFolder
Set f = fs.CreateTextFile(enabledFile, True)
f.WriteLine "Enabled by the user for Codex desktop launch detection."
f.Close
Set link = shell.CreateShortcut(startupLink)
link.TargetPath = python
link.Arguments = wantedArgs
link.WorkingDirectory = folder
link.WindowStyle = 7
link.Description = "Open the conversation monitor when Codex desktop starts"
link.Save
shell.Run Chr(34) & python & Chr(34) & " " & wantedArgs, 0, False
WScript.Echo "Codex monitor auto-start enabled for this Windows user."
