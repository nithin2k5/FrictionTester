"""Allow one running FrictionTester per Windows session.

A second copy would find every COM port already open by the first (Windows
answers "Access is denied"), so it is stopped at startup with a message.
"""

import sys

_handle = None          # kept for the life of the process
ERROR_ALREADY_EXISTS = 183


def acquire(name="Local\\FrictionTester"):
    """True if this is the only copy running. Always True off Windows."""
    global _handle
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _handle = kernel32.CreateMutexW(None, False, name)
    return not (_handle and kernel32.GetLastError() == ERROR_ALREADY_EXISTS)
