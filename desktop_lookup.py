# === DESKTOP LOOKUP: SELECTED-TEXT READING, SINGLE-INSTANCE HAND-OFF & WINDOWS HOTKEY ===
# `app.py --lookup` (bound to a desktop shortcut on Linux) reads the selected/copied text and hands it to
# the running Ocean over a local socket. On Windows the running app registers Ctrl+Alt+O itself.
#
# Linux note: on Wayland an unfocused app can't read the clipboard, but GNOME/KDE bridge selections to
# XWayland, where reading is unrestricted. So selections are read through plain Xlib (ctypes, no extra
# dependency) whenever an X display is available.
import ctypes
import ctypes.util
import getpass
import json
import logging
import os
import re
import sys
import time
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("ocean.lookup")

MAX_LOOKUP_CHARS = 300
HOTKEY_LABEL = "Ctrl+Alt+O"


def clean_lookup_text(text: str) -> str:
    """Whitespace-collapsed, length-capped text; a single word also loses surrounding punctuation."""
    text = " ".join(text.split())[:MAX_LOOKUP_CHARS].strip()
    text = text.strip("\"'“”‘’()[]{}<>,;:")
    if " " not in text:
        text = text.strip(".!?…")
    return text


# --- X11 selection reading (works on X11 sessions and through XWayland) ---
_SELECTION_NOTIFY = 31


def _load_xlib():
    name = ctypes.util.find_library("X11")
    if not name:
        return None
    x = ctypes.cdll.LoadLibrary(name)
    vp, ul, i, ui = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
    x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
    x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
    x.XCreateSimpleWindow.restype = ul
    x.XCreateSimpleWindow.argtypes = [vp, ul, i, i, ui, ui, ui, ul, ul]
    x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, i]
    x.XConvertSelection.argtypes = [vp, ul, ul, ul, ul, ul]
    x.XPending.argtypes = [vp]
    x.XNextEvent.argtypes = [vp, vp]
    x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, i, ul, ctypes.POINTER(ul),
                                     ctypes.POINTER(i), ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(vp)]
    x.XFree.argtypes = [vp]
    x.XCloseDisplay.argtypes = [vp]
    return x


def read_x11_selection(selection: str, timeout: float) -> Optional[str]:
    """Text of an X selection ("PRIMARY" or "CLIPBOARD"); "" if empty, None if unavailable or no answer."""
    try:
        x = _load_xlib()
    except OSError:
        return None
    if x is None:
        return None
    display = x.XOpenDisplay(None)
    if not display:
        return None
    try:
        win = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 0, 0, 1, 1, 0, 0, 0)
        sel = x.XInternAtom(display, selection.encode(), 0)
        utf8 = x.XInternAtom(display, b"UTF8_STRING", 0)
        prop = x.XInternAtom(display, b"OCEAN_SELECTION", 0)
        x.XConvertSelection(display, sel, utf8, prop, win, 0)
        event = (ctypes.c_long * 24)()   # XEvent is a 192-byte union
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not x.XPending(display):
                time.sleep(0.01)
                continue
            x.XNextEvent(display, event)
            # XSelectionEvent as longs: type, serial, send_event, display, requestor, selection, target, property.
            # `type` is a C int, so mask off the slot's padding.
            if event[0] & 0xFFFFFFFF != _SELECTION_NOTIFY:
                continue
            if event[7] == 0:            # property == None: no owner or conversion refused
                return ""
            typ, fmt = ctypes.c_ulong(), ctypes.c_int()
            count, remaining, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
            x.XGetWindowProperty(display, win, prop, 0, 1 << 20, 1, 0, ctypes.byref(typ), ctypes.byref(fmt),
                                 ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data))
            if not data:
                return ""
            try:
                return ctypes.string_at(data, count.value).decode("utf-8", "replace")
            finally:
                x.XFree(data)
        return None
    finally:
        x.XCloseDisplay(display)


def read_selected_text() -> str:
    """Highlighted text (PRIMARY) if the desktop provides it, else the clipboard. Empty where no X
    display exists (e.g. Windows); the running app then reads its own clipboard."""
    if not os.environ.get("DISPLAY") or sys.platform in ("win32", "darwin"):
        return ""
    # PRIMARY gets a short timeout: GNOME with middle-click paste off owns it but never answers.
    for selection, timeout in (("PRIMARY", 0.3), ("CLIPBOARD", 1.5)):
        text = read_x11_selection(selection, timeout)
        if text and text.strip():
            return text
    return ""


# --- Single instance: a second launch hands its request to the running app and exits ---
def _server_name() -> str:
    return "ocean-" + re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser())


def send_to_running_instance(message: Dict[str, str], timeout_ms: int = 3000) -> bool:
    """True if a running Ocean accepted the message. Connecting fails at once when no instance runs;
    the timeout only matters if the running window is busy (on Windows it must free a pipe instance)."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(message).encode("utf-8") + b"\n")
    ok = sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(timeout_ms)
    return ok


class InstanceServer(QObject):
    """Receives hand-offs from later launches (`--lookup` or a plain second start)."""
    message_received = pyqtSignal(dict)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)

    def listen(self) -> bool:
        name = _server_name()
        if not self.server.listen(name):
            # A crashed instance can leave a stale socket behind; only reached when no live
            # instance answered the hand-off, so removing it is safe.
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                log.warning("Single-instance server unavailable: %s", self.server.errorString())
                return False
        return True

    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(self._read)
            sock.disconnected.connect(sock.deleteLater)

    def _read(self) -> None:
        sock = self.sender()
        if not isinstance(sock, QLocalSocket) or not sock.canReadLine():
            return
        try:
            message = json.loads(bytes(sock.readLine()).decode("utf-8"))
        except ValueError:
            log.warning("Ignoring malformed hand-off message")
            return
        if isinstance(message, dict):
            self.message_received.emit({k: str(v) for k, v in message.items()})


# --- Windows global hotkey (RegisterHotKey; WM_HOTKEY arrives on the GUI thread's queue) ---
class WindowsHotkey(QAbstractNativeEventFilter):
    _ID = 0x0CEA
    _WM_HOTKEY = 0x0312
    _MOD_ALT, _MOD_CONTROL, _MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    _VK_O = 0x4F

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback

    def register(self) -> bool:
        ok = bool(ctypes.windll.user32.RegisterHotKey(
            None, self._ID, self._MOD_CONTROL | self._MOD_ALT | self._MOD_NOREPEAT, self._VK_O))
        if not ok:
            log.warning("Could not register %s (already used by another app?)", HOTKEY_LABEL)
        return ok

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self._WM_HOTKEY and msg.wParam == self._ID:
                self.callback()
                return True, 0
        return False, 0
