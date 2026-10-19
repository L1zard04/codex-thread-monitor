"""Windows single-instance protection and read-only desktop process detection."""
import ctypes
from ctypes import wintypes
import hashlib
import ntpath
import os
from pathlib import Path


def user_data():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CodexThreadMonitor'


kernel = ctypes.WinDLL('kernel32',use_last_error=True)
kernel.CreateMutexW.argtypes = [ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL


class InstanceLock:
    def __init__(self,key):
        digest=hashlib.sha256(ntpath.normcase(str(key)).encode('utf-8')).hexdigest()[:32]
        self.name='Local\\CodexThreadMonitor-'+digest
        self.handle=None

    def acquire(self):
        if self.handle is not None:
            return True
        ctypes.set_last_error(0)
        handle=kernel.CreateMutexW(None,False,self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(handle)
            return False
        self.handle=handle
        return True

    def close(self):
        if self.handle is not None:
            kernel.CloseHandle(self.handle)
            self.handle=None


class LaunchPolicy:
    """Open on each absent->present edge; respect manual dismissal until next edge."""
    def __init__(self,launch):
        self.launch=launch
        self.was_running=False

    def tick(self,running):
        if running is None:
            return
        if running and not self.was_running:
            self.launch()
        self.was_running=running


def is_codex_desktop(executable):
    parts=ntpath.normcase(executable).split('\\')
    return (ntpath.basename(executable).lower()=='chatgpt.exe'
            and any(p.startswith('openai.codex_') for p in parts))


class PROCESSENTRY32W(ctypes.Structure):
    _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),
              ('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
              ('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
              ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
              ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]


kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
kernel.Process32FirstW.restype=wintypes.BOOL
kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
kernel.Process32NextW.restype=wintypes.BOOL
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
kernel.OpenProcess.restype=wintypes.HANDLE
kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,
                                           wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL


def codex_desktop_running():
    """Detect the Store desktop package, excluding Codex CLI and VS Code helpers."""
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==ctypes.c_void_p(-1).value:
        return None
    uncertain=False
    try:
        entry=PROCESSENTRY32W()
        entry.dwSize=ctypes.sizeof(entry)
        valid=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while valid:
            if entry.szExeFile.lower()=='chatgpt.exe':
                handle=kernel.OpenProcess(0x1000,False,entry.th32ProcessID)
                if handle:
                    try:
                        length=wintypes.DWORD(32768)
                        buffer=ctypes.create_unicode_buffer(length.value)
                        if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):
                            if is_codex_desktop(buffer.value):
                                return True
                        else:
                            uncertain=True
                    finally:
                        kernel.CloseHandle(handle)
                else:
                    uncertain=True
            valid=kernel.Process32NextW(snapshot,ctypes.byref(entry))
        return None if uncertain else False
    finally:
        kernel.CloseHandle(snapshot)
