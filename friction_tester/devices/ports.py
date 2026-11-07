"""Which device of this app has which COM port open.

Windows lets only one handle open a COM port; a second open fails with
"Access is denied" and does not say who has it. Devices register here so a
clash inside this app is reported by name before the port is touched.
"""

import threading

_lock = threading.Lock()
_owners = {}          # "COM11" -> device name


def _key(port):
    return (port or "").strip().upper()


def owner(port, exclude=None):
    """Name of the device of this app holding `port`, other than `exclude`."""
    with _lock:
        name = _owners.get(_key(port))
    return None if name == exclude else name


def claim(port, name):
    with _lock:
        _owners[_key(port)] = name


def release(port, name):
    with _lock:
        if _owners.get(_key(port)) == name:
            del _owners[_key(port)]


def check_free(port, name):
    """Raise if another device of this app already has `port` open."""
    other = owner(port, exclude=name)
    if other:
        raise PortClashError(f"{port} is already used by {other} in this app — "
                             f"pick a different COM port for {name}.")


class PortClashError(Exception):
    pass
