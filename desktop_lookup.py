# === DESKTOP LOOKUP: SELECTED-TEXT READING, SINGLE-INSTANCE HAND-OFF & WINDOWS TRIGGERS ===
# `app.py --lookup` (bound to a desktop shortcut on Linux) reads the selected/copied text and hands it to
# the running Ocean over a local socket. On Windows the running app watches for "Ctrl+C twice" and
# registers the Alt+Q and Ctrl+Alt+O chords itself.
#
# Linux note: on Wayland an unfocused app can't read the clipboard, but GNOME/KDE bridge selections to
# XWayland, where reading is unrestricted. So selections are read through plain Xlib (ctypes, no extra
# dependency) whenever an X display is available.
import ctypes
import ctypes.util
import functools
import getpass
import json
import logging
import os
import re
import sys
import time
from typing import Callable, Dict, Iterable, Optional, Tuple

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("ocean.lookup")

MAX_LOOKUP_CHARS = 300
COPY_TWICE_LABEL = "Ctrl+C twice"
CHORD_LABEL = "Alt+Q"
FALLBACK_LABEL = "Ctrl+Alt+O"
HOTKEY_LABEL = f"{COPY_TWICE_LABEL} (or {CHORD_LABEL})"   # what the UI and README call the shortcut
COPY_TWICE_WINDOW = 0.6    # seconds from the first copy to the second
COPY_DUPLICATE_GAP = 0.08  # updates closer than this are one copy written in pieces; nobody taps C that fast


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


# --- "Ctrl+C twice": two copies of the same text in quick succession ---
class DoubleCopyDetector:
    """Decides from clipboard changes whether the user copied the same text twice in a row.
    Qt-free: the caller says when the clipboard changed, whether Ctrl was held and whether Ocean itself
    wrote it, and passes a reader that fetches the text (only called for changes that can count)."""

    def __init__(self, window: float = COPY_TWICE_WINDOW, duplicate_gap: float = COPY_DUPLICATE_GAP,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window
        self.duplicate_gap = duplicate_gap
        self.clock = clock
        self._text = ""
        self._time = 0.0

    def reset(self) -> None:
        self._text = ""

    def on_clipboard_change(self, read_text: Callable[[], str], ctrl_down: bool = True,
                            own: bool = False) -> Optional[str]:
        """The copied text when this change completes a double copy, else None."""
        if own or not ctrl_down:
            self.reset()   # a write that is not the user's keyboard copy breaks the sequence
            return None
        text = " ".join((read_text() or "").split())
        if not text:
            self.reset()   # images and files carry no text
            return None
        now = self.clock()
        if self._text:
            gap = now - self._time
            if gap < self.duplicate_gap:
                self._text = text   # the same copy written again (several formats): keep the first time
                return None
            if gap <= self.window and text == self._text:
                self.reset()   # a third press starts a new pair
                return text
        self._text, self._time = text, now
        return None


def is_sensitive_clipboard(formats: Iterable[str]) -> bool:
    """Password managers mark secrets with this format; Ocean must not even read those."""
    return any("ExcludeClipboardContentFromMonitorProcessing" in f for f in formats)


# --- Windows plumbing (ctypes; every signature spelled out so handles stay pointer-sized on 64-bit) ---
_VK_CONTROL = 0x11
_SW_RESTORE = 9


@functools.lru_cache(maxsize=1)
def _user32():
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    hwnd, dword, uint, boolean, c_int = wintypes.HWND, wintypes.DWORD, wintypes.UINT, wintypes.BOOL, ctypes.c_int
    u.GetAsyncKeyState.restype, u.GetAsyncKeyState.argtypes = ctypes.c_short, [c_int]
    u.GetClipboardOwner.restype, u.GetClipboardOwner.argtypes = hwnd, []
    u.GetForegroundWindow.restype, u.GetForegroundWindow.argtypes = hwnd, []
    u.GetWindowThreadProcessId.restype = dword
    u.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(dword)]
    u.AttachThreadInput.restype, u.AttachThreadInput.argtypes = boolean, [dword, dword, boolean]
    u.IsIconic.restype, u.IsIconic.argtypes = boolean, [hwnd]
    u.ShowWindow.restype, u.ShowWindow.argtypes = boolean, [hwnd, c_int]
    u.BringWindowToTop.restype, u.BringWindowToTop.argtypes = boolean, [hwnd]
    u.SetForegroundWindow.restype, u.SetForegroundWindow.argtypes = boolean, [hwnd]
    u.RegisterHotKey.restype, u.RegisterHotKey.argtypes = boolean, [hwnd, c_int, uint, uint]
    return u


@functools.lru_cache(maxsize=1)
def _kernel32():
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThreadId.restype, kernel32.GetCurrentThreadId.argtypes = wintypes.DWORD, []
    return kernel32


def _thread_and_process(hwnd) -> Tuple[int, int]:
    """(thread id, process id) that owns a window."""
    from ctypes import wintypes
    pid = wintypes.DWORD(0)
    thread = _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return thread, pid.value


def ctrl_key_down() -> bool:
    """Is Ctrl physically held right now? Tells a keyboard copy from a program writing the clipboard."""
    return bool(_user32().GetAsyncKeyState(_VK_CONTROL) & 0x8000)


def clipboard_written_by_ocean() -> bool:
    owner = _user32().GetClipboardOwner()
    return bool(owner) and _thread_and_process(owner)[1] == os.getpid()


def focus_window(hwnd: int) -> None:
    """Bring a window to the front even though no input went to Ocean (otherwise Windows only flashes
    its taskbar button): share the foreground thread's input state for the duration of the call."""
    try:
        user32 = _user32()
        ours = _kernel32().GetCurrentThreadId()
        foreground = _thread_and_process(user32.GetForegroundWindow())[0]
        attached = bool(foreground) and foreground != ours and bool(user32.AttachThreadInput(ours, foreground, True))
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, _SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(ours, foreground, False)
    except OSError:
        log.warning("Could not bring Ocean to the front", exc_info=True)


class DoubleCopyWatcher(QObject):
    """Windows: calls `callback(text)` when the user taps Ctrl+C twice on the same text in any app.
    QClipboard.dataChanged is driven by AddClipboardFormatListener, so it fires for background apps
    too, and no keyboard hook (which antivirus and game anti-cheat dislike) is involved."""

    def __init__(self, clipboard, callback: Callable[[str], None], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.clipboard = clipboard
        self.callback = callback
        self.detector = DoubleCopyDetector()
        clipboard.dataChanged.connect(self._on_changed)

    def _on_changed(self) -> None:
        try:
            # Cheap checks first: most clipboard changes are not Ctrl+C and their text is never read.
            ctrl = ctrl_key_down()
            own = ctrl and clipboard_written_by_ocean()
            if ctrl and not own:
                mime = self.clipboard.mimeData()
                if mime is None or not mime.hasText() or is_sensitive_clipboard(mime.formats()):
                    ctrl = False
            text = self.detector.on_clipboard_change(self.clipboard.text, ctrl, own)
        except Exception:   # a clipboard hiccup must never take the app down
            log.exception("Double-copy detection failed")
            self.detector.reset()
            return
        if text:
            self.callback(text)


# --- Windows global hotkeys (RegisterHotKey; WM_HOTKEY arrives on the GUI thread's queue) ---
class WindowsHotkeys(QAbstractNativeEventFilter):
    """Alt+Q (left hand, rarely taken) and the older Ctrl+Alt+O; both look up what is on the clipboard."""
    _WM_HOTKEY = 0x0312
    _MOD_ALT, _MOD_CONTROL, _MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    _VK_O, _VK_Q = 0x4F, 0x51
    _KEYS = (   # (id, modifiers, virtual key, label)
        (0x0CEA, _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, _VK_O, FALLBACK_LABEL),
        (0x0CEB, _MOD_ALT | _MOD_NOREPEAT, _VK_Q, CHORD_LABEL),
    )

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback

    def register(self) -> bool:
        """True if at least one chord is registered; the others are logged and skipped."""
        user32 = _user32()
        registered = 0
        for key_id, modifiers, vk, label in self._KEYS:
            if user32.RegisterHotKey(None, key_id, modifiers, vk):
                registered += 1
            else:
                log.warning("Could not register %s (already used by another app?)", label)
        return registered > 0

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self._WM_HOTKEY and msg.wParam in {key[0] for key in self._KEYS}:
                self.callback()
                return True, 0
        return False, 0
