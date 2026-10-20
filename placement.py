"""Restore normal window bounds within the current monitor work areas."""
import ctypes
from ctypes import wintypes


def work_areas():
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize',wintypes.DWORD),('monitor',wintypes.RECT),
                    ('work',wintypes.RECT),('flags',wintypes.DWORD)]
    areas = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                      wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32 = ctypes.WinDLL('user32',use_last_error=True)
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, callback_type,wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL

    @callback_type
    def collect(monitor, _dc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(monitor,ctypes.byref(info)):
            r = info.work
            areas.append((bool(info.flags & 1),(r.left,r.top,r.right,r.bottom)))
        return True

    if not user32.EnumDisplayMonitors(None,None,collect,0) or not areas:
        raise ctypes.WinError(ctypes.get_last_error())
    return [area for _primary,area in sorted(areas,key=lambda item:not item[0])]


def frame_size():
    metrics = ctypes.WinDLL('user32',use_last_error=True).GetSystemMetrics
    return (2*(metrics(32)+metrics(92)), 2*(metrics(33)+metrics(92))+metrics(4))


def restore_bounds(saved, areas, frame_size=(0,0)):
    valid = (isinstance(saved,dict) and
             all(type(saved.get(k)) is int for k in ('width','height','x','y')) and
             saved['width'] > 0 and saved['height'] > 0)
    if not valid:
        left,top,right,bottom = areas[0]
        saved = {'width':360,'height':420,'x':left+40,'y':top+40}
    x,y = saved['x'],saved['y']
    def overlap(area):
        left,top,right,bottom = area
        return max(0,min(x+saved['width'],right)-max(x,left))*max(0,min(y+saved['height'],bottom)-max(y,top))
    left,top,right,bottom = max(areas,key=overlap)
    frame_width,frame_height = frame_size
    width = min(max(300,saved['width']),max(1,right-left-frame_width))
    height = min(max(360,saved['height']),max(1,bottom-top-frame_height))
    return {'width':width,'height':height,
            'x':max(left,min(x,right-width-frame_width)),
            'y':max(top,min(y,bottom-height-frame_height))}
