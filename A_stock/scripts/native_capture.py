"""Capture only this process's native window for local UI QA."""
import ctypes as c
import os


def capture_own_window(path, layer=0):
    cg = c.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    imageio = c.CDLL("/System/Library/Frameworks/ImageIO.framework/ImageIO")
    cf.CFStringCreateWithCString.argtypes = [c.c_void_p, c.c_char_p, c.c_uint32]
    cf.CFStringCreateWithCString.restype = c.c_void_p
    cf.CFRelease.argtypes = [c.c_void_p]
    cf.CFArrayGetCount.argtypes = [c.c_void_p]
    cf.CFArrayGetCount.restype = c.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [c.c_void_p, c.c_long]
    cf.CFArrayGetValueAtIndex.restype = c.c_void_p
    cf.CFDictionaryGetValue.argtypes = [c.c_void_p, c.c_void_p]
    cf.CFDictionaryGetValue.restype = c.c_void_p
    cf.CFNumberGetValue.argtypes = [c.c_void_p, c.c_int, c.c_void_p]
    cg.CGWindowListCopyWindowInfo.argtypes = [c.c_uint32, c.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = c.c_void_p
    windows = cg.CGWindowListCopyWindowInfo(1, 0)
    keys = {key: cf.CFStringCreateWithCString(None, key.encode(), 0x08000100)
            for key in ("kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowLayer")}
    window_id = None
    try:
        for i in range(cf.CFArrayGetCount(windows)):
            item = cf.CFArrayGetValueAtIndex(windows, i)
            values = {}
            for key, ref in keys.items():
                num = cf.CFDictionaryGetValue(item, ref)
                value = c.c_longlong()
                if num:
                    cf.CFNumberGetValue(num, 4, c.byref(value))
                values[key] = value.value
            if values["kCGWindowOwnerPID"] == os.getpid() and (layer is None or values["kCGWindowLayer"] == layer):
                window_id = values["kCGWindowNumber"]
                break
    finally:
        for ref in keys.values():
            cf.CFRelease(ref)
        cf.CFRelease(windows)
    if not window_id:
        return False
    class Point(c.Structure):
        _fields_ = [("x", c.c_double), ("y", c.c_double)]
    class Size(c.Structure):
        _fields_ = [("width", c.c_double), ("height", c.c_double)]
    class Rect(c.Structure):
        _fields_ = [("origin", Point), ("size", Size)]
    cg.CGWindowListCreateImage.argtypes = [Rect, c.c_uint32, c.c_uint32, c.c_uint32]
    cg.CGWindowListCreateImage.restype = c.c_void_p
    image = cg.CGWindowListCreateImage(Rect(Point(0, 0), Size(0, 0)), 8, window_id, 1)
    if not image:
        return False
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [c.c_void_p, c.c_char_p, c.c_long, c.c_bool]
    cf.CFURLCreateFromFileSystemRepresentation.restype = c.c_void_p
    raw = os.fsencode(path)
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    kind = cf.CFStringCreateWithCString(None, b"public.png", 0x08000100)
    imageio.CGImageDestinationCreateWithURL.argtypes = [c.c_void_p, c.c_void_p, c.c_long, c.c_void_p]
    imageio.CGImageDestinationCreateWithURL.restype = c.c_void_p
    target = imageio.CGImageDestinationCreateWithURL(url, kind, 1, None)
    try:
        if not target:
            return False
        imageio.CGImageDestinationAddImage.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p]
        imageio.CGImageDestinationFinalize.argtypes = [c.c_void_p]
        imageio.CGImageDestinationFinalize.restype = c.c_bool
        imageio.CGImageDestinationAddImage(target, image, None)
        return bool(imageio.CGImageDestinationFinalize(target))
    finally:
        for ref in (target, kind, url, image):
            if ref:
                cf.CFRelease(ref)
