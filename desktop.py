"""Windows notification area integration. Worker never calls Tk."""
import ctypes
from ctypes import wintypes as w
import os
import queue
import threading


WINDOW_TITLE='CodexThreadMonitorTray'
TRAY_MESSAGE=0x8001
COMMAND_MESSAGE=0x8002
SHOW_MESSAGE=0x8003
WNDPROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)


class GUID(ctypes.Structure):
    _fields_=[('a',w.DWORD),('b',w.WORD),('c',w.WORD),('d',w.BYTE*8)]


class NotifyData(ctypes.Structure):
    _fields_=[('cbSize',w.DWORD),('hWnd',w.HWND),('uID',w.UINT),('uFlags',w.UINT),
              ('uCallbackMessage',w.UINT),('hIcon',w.HICON),('szTip',w.WCHAR*128),
              ('dwState',w.DWORD),('dwStateMask',w.DWORD),('szInfo',w.WCHAR*256),
              ('uVersion',w.UINT),('szInfoTitle',w.WCHAR*64),('dwInfoFlags',w.DWORD),
              ('guidItem',GUID),('hBalloonIcon',w.HICON)]


class WindowClass(ctypes.Structure):
    _fields_=[('style',w.UINT),('lpfnWndProc',WNDPROC),('cbClsExtra',ctypes.c_int),
              ('cbWndExtra',ctypes.c_int),('hInstance',w.HINSTANCE),('hIcon',w.HICON),
              ('hCursor',w.HANDLE),('hbrBackground',w.HANDLE),('lpszMenuName',w.LPCWSTR),
              ('lpszClassName',w.LPCWSTR)]


def native_api():
    user=ctypes.WinDLL('user32',use_last_error=True)
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    definitions=[
        (user,'RegisterClassW',[ctypes.POINTER(WindowClass)],w.WORD),
        (user,'UnregisterClassW',[w.LPCWSTR,w.HINSTANCE],w.BOOL),
        (user,'CreateWindowExW',[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,
                                ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p],w.HWND),
        (user,'DefWindowProcW',[w.HWND,w.UINT,w.WPARAM,w.LPARAM],ctypes.c_ssize_t),
        (user,'DestroyWindow',[w.HWND],w.BOOL),
        (user,'PostMessageW',[w.HWND,w.UINT,w.WPARAM,w.LPARAM],w.BOOL),
        (user,'PostQuitMessage',[ctypes.c_int],None),
        (user,'RegisterWindowMessageW',[w.LPCWSTR],w.UINT),
        (user,'FindWindowW',[w.LPCWSTR,w.LPCWSTR],w.HWND),
        (user,'LoadIconW',[w.HINSTANCE,ctypes.c_void_p],w.HICON),
        (user,'GetMessageW',[ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT],ctypes.c_int),
        (user,'TranslateMessage',[ctypes.POINTER(w.MSG)],w.BOOL),
        (user,'DispatchMessageW',[ctypes.POINTER(w.MSG)],ctypes.c_ssize_t),
        (user,'CreatePopupMenu',[],w.HMENU),
        (user,'AppendMenuW',[w.HMENU,w.UINT,ctypes.c_size_t,w.LPCWSTR],w.BOOL),
        (user,'TrackPopupMenu',[w.HMENU,w.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,ctypes.c_void_p],w.UINT),
        (user,'DestroyMenu',[w.HMENU],w.BOOL),
        (user,'SetForegroundWindow',[w.HWND],w.BOOL),
        (user,'GetCursorPos',[ctypes.POINTER(w.POINT)],w.BOOL),
        (shell,'Shell_NotifyIconW',[w.DWORD,ctypes.POINTER(NotifyData)],w.BOOL),
        (kernel,'GetModuleHandleW',[w.LPCWSTR],w.HMODULE)]
    for library,name,args,result in definitions:
        function=getattr(library,name)
        function.argtypes,function.restype=args,result
    return user,shell,kernel


class WindowsDesktop:
    def __init__(self,output):
        self.output=output
        self.commands=queue.Queue()
        self.available=False
        self.error=''
        self.hwnd=None
        self.closing=False
        self.ready=threading.Event()
        self.user,self.shell,self.kernel=native_api()
        self.thread=threading.Thread(target=self.run,name='windows-notification-area',daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            self.error='托盘初始化超时'
            self.close()

    @staticmethod
    def wake_existing():
        user,_shell,_kernel=native_api()
        hwnd=user.FindWindowW(None,WINDOW_TITLE)
        return bool(hwnd and user.PostMessageW(hwnd,SHOW_MESSAGE,0,0))

    def data(self,flags):
        data=NotifyData()
        data.cbSize=ctypes.sizeof(data)
        data.hWnd=self.hwnd
        data.uID=1
        data.uFlags=flags
        data.uCallbackMessage=TRAY_MESSAGE
        data.hIcon=self.icon
        data.szTip='Codex 四格监视器 · 点击显示，右键管理'
        return data

    def add_icon(self):
        data=self.data(1|2|4|0x80)
        if not self.shell.Shell_NotifyIconW(0,ctypes.byref(data)):
            raise OSError('Windows 未能创建托盘图标')
        data.uVersion=4
        if not self.shell.Shell_NotifyIconW(4,ctypes.byref(data)):
            self.shell.Shell_NotifyIconW(2,ctypes.byref(data))
            raise OSError('Windows 未能设置托盘回调版本')
        self.available=True

    def run(self):
        instance=self.kernel.GetModuleHandleW(None)
        name=f'CodexMonitorTray-{os.getpid()}-{id(self)}'
        registered=False
        try:
            self.callback=WNDPROC(self.window_proc)
            self.icon=self.user.LoadIconW(None,ctypes.c_void_p(32512))
            self.restart_message=self.user.RegisterWindowMessageW('TaskbarCreated')
            window_class=WindowClass()
            window_class.lpfnWndProc=self.callback
            window_class.hInstance=instance
            window_class.lpszClassName=name
            if not self.user.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered=True
            self.hwnd=self.user.CreateWindowExW(0,name,WINDOW_TITLE,0,0,0,0,0,None,None,instance,None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.add_icon()
            self.ready.set()
            if self.closing:
                self.user.PostMessageW(self.hwnd,0x10,0,0)
            message=w.MSG()
            while True:
                status=self.user.GetMessageW(ctypes.byref(message),None,0,0)
                if status==0:
                    break
                if status==-1:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.user.TranslateMessage(ctypes.byref(message))
                self.user.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error=str(error)
            if not self.closing:
                self.output.put(('desktop_error',self.error))
        finally:
            self.available=False
            self.ready.set()
            if self.hwnd:
                data=self.data(0)
                self.shell.Shell_NotifyIconW(2,ctypes.byref(data))
                self.user.DestroyWindow(self.hwnd)
                self.hwnd=None
            if registered:
                self.user.UnregisterClassW(name,instance)

    def window_proc(self,hwnd,message,wparam,lparam):
        try:
            if message==TRAY_MESSAGE:
                event=lparam & 0xffff
                if event in (0x400,0x401,0x405):
                    self.output.put(('desktop','show'))
                elif event in (0x7b,0x205):
                    self.popup()
                return 0
            if message==SHOW_MESSAGE:
                self.output.put(('desktop','show'))
                return 0
            if message==COMMAND_MESSAGE:
                while True:
                    try:
                        title,body=self.commands.get_nowait()
                    except queue.Empty:
                        break
                    data=self.data(0x10)
                    data.szInfoTitle=title[:63]
                    data.szInfo=body[:255]
                    data.dwInfoFlags=1|0x10|0x80  # info, no sound, respect quiet time
                    if not self.shell.Shell_NotifyIconW(1,ctypes.byref(data)):
                        raise OSError('Windows 未能接收完成通知')
                return 0
            if message==self.restart_message:
                self.available=False
                self.add_icon()
                self.output.put(('desktop_ready',None))
                return 0
            if message==0x10:
                if not self.closing:
                    self.output.put(('desktop_error','托盘窗口意外关闭'))
                data=self.data(0)
                self.shell.Shell_NotifyIconW(2,ctypes.byref(data))
                self.user.DestroyWindow(hwnd)
                self.hwnd=None
                return 0
            if message==2:
                self.user.PostQuitMessage(0)
                return 0
        except Exception as error:
            self.error=str(error)
            self.output.put(('desktop_error',self.error))
            return 0
        return self.user.DefWindowProcW(hwnd,message,wparam,lparam)

    def popup(self):
        menu=self.user.CreatePopupMenu()
        if not menu:
            raise ctypes.WinError(ctypes.get_last_error())
        actions={1:'show',2:'hide',3:'notifications',4:'exit'}
        try:
            for command,label in [(1,'显示监视器'),(2,'收起到托盘'),(3,'切换完成提醒'),(4,'退出')]:
                self.user.AppendMenuW(menu,0,command,label)
            point=w.POINT()
            self.user.GetCursorPos(ctypes.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            command=self.user.TrackPopupMenu(menu,0x100|0x80|2,point.x,point.y,0,self.hwnd,None)
            if command in actions:
                self.output.put(('desktop',actions[command]))
            self.user.PostMessageW(self.hwnd,0,0,0)
        finally:
            self.user.DestroyMenu(menu)

    def notify(self,title,body):
        if not self.available or self.closing:
            raise OSError('托盘尚未就绪')
        self.commands.put((title,body))
        if not self.user.PostMessageW(self.hwnd,COMMAND_MESSAGE,0,0):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.closing=True
        self.available=False
        if self.hwnd:
            self.user.PostMessageW(self.hwnd,0x10,0,0)
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=1)
