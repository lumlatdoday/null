# =============================================================================
# IMPORTS
# =============================================================================

# Standard library
import json
import shutil
import uuid
import struct
import random
import asyncio
import ipaddress
import base64
import socket
import concurrent.futures
import urllib.request
from urllib.parse import urlsplit
import threading
import re
import sys
import os
import winreg
import ctypes
from ctypes import wintypes
import time
import subprocess
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

# Third-party
import psutil
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QCheckBox,
    QMainWindow, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QVBoxLayout, QWidget, QComboBox, QTabWidget, QTextEdit, QAbstractItemView, QLabel
)
import shiboken6

# Internal
from fingerprint import generate_fingerprint, build_profile_extension, append_load_extension_arg
import profile_ui

# =============================================================================
# PROXY: TIMEZONE & PUBLIC IP AUTO-DETECT
# =============================================================================

def fetch_timezone_from_proxy(proxy_url: str):
    """
    Automatically detect the timezone ID and public IP via the proxy.
    Returns a tuple: (timezone_str, public_ip_str)
    """
    if not proxy_url:
        return None, None

    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"

    print(f"[Auto-TZ-IP] Checking timezone & IP for proxy: {proxy_url}...")

    apis = [
        ("http://ip-api.com/json/", "timezone", "query"),
        ("https://ipwho.is/", "timezone.id", "ip"),
        ("https://ipinfo.io/json", "timezone", "ip")
    ]

    random.shuffle(apis)

    proxies = {'http': proxy_url, 'https': proxy_url}
    handler = urllib.request.ProxyHandler(proxies)
    opener = urllib.request.build_opener(handler)
    opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]

    for url, key_path_tz, key_ip in apis:
        try:
            req = urllib.request.Request(url)
            with opener.open(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))

                tz = data
                for k in key_path_tz.split('.'):
                    if isinstance(tz, dict):
                        tz = tz.get(k)

                public_ip = data.get(key_ip)

                valid_tz = (tz and isinstance(tz, str) and "/" in tz)
                valid_ip = (public_ip and isinstance(public_ip, str) and "." in public_ip)

                if valid_tz or valid_ip:
                    print(f"[Auto-TZ-IP] Success: TZ={tz}, IP={public_ip} (Source: {url})")

                    return (tz if valid_tz else None), (public_ip if valid_ip else None)

        except Exception as e:
            continue

    print("[Auto-TZ-IP] Failed (proxy error or timeout).")
    return None, None

def _proxy_display(proxy: dict) -> str:
    if not proxy or not proxy.get("enabled"):
        return ""
    ptype = str(proxy.get("type") or "").strip().lower() or "http"
    host = str(proxy.get("host") or "").strip()
    port = proxy.get("port")
    if not host or not port:
        return ""
    has_auth = bool((proxy.get("username") or "").strip() or (proxy.get("password") or "").strip())
    base = f"{ptype.upper()} {host}:{int(port)}"
    return base + (" [AUTH]" if has_auth else "")

# =============================================================================
# PROXY: CONNECTIVITY CHECKS
# =============================================================================

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise RuntimeError("Connection closed while reading")
        data += chunk
    return data

# =============================================================================
# PROXY: GEOIP LOOKUP
# =============================================================================

def fetch_proxy_geo(proxy: dict, timeout=5.0):
    """
    Use urllib to call ip-api.com through the proxy to fetch Country/City.
    Returns a dict or None.
    """
    try:
        ptype = str(proxy.get("type") or "http").strip().lower()
        host = str(proxy.get("host") or "").strip()
        port = str(proxy.get("port") or "").strip()
        user = str(proxy.get("username") or "").strip()
        pwd  = str(proxy.get("password") or "").strip()

        if not host or not port:
            return None

        proxy_url = f"{host}:{port}"
        if user and pwd:
            proxy_url = f"{user}:{pwd}@{host}:{port}"

        scheme = "https" if ptype == "http" else ptype

        proxies = {
            'http': f"http://{proxy_url}",
            'https': f"http://{proxy_url}"
        }

        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
        opener.addheaders = [('User-Agent', 'Mozilla/5.0 (SMCD Check)')]

        target_url = "http://ip-api.com/json/?fields=status,country,countryCode,city,query"
        req = urllib.request.Request(target_url)

        with opener.open(req, timeout=timeout) as response:
            body = response.read().decode('utf-8')
            return json.loads(body)
    except Exception:
        return None

def _proxy_check_http(host: str, port: int, user: str, pwd: str, timeout: float,
                      target_host: str, target_port: int) -> None:

    with socket.create_connection((host, port), timeout=timeout) as s:
        headers = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
            "Proxy-Connection: Keep-Alive",
            "Connection: Keep-Alive",
        ]
        if user or pwd:
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {token}")
        req = "\r\n".join(headers) + "\r\n\r\n"
        s.sendall(req.encode("utf-8"))

        s.settimeout(timeout)
        buf = s.recv(4096)
        if not buf:
            raise RuntimeError("No response from HTTP proxy")
        first = buf.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore").strip()
        parts = first.split()
        if len(parts) < 2:
            raise RuntimeError(f"Bad HTTP response: {first}")
        code = parts[1]
        if code == "200":
            return
        if code == "407":
            raise RuntimeError("HTTP proxy auth failed (407)")
        raise RuntimeError(f"HTTP proxy CONNECT failed ({code})")

def _proxy_check_socks4(host: str, port: int, timeout: float,
                        target_ip: str, target_port: int) -> None:

    ip_bytes = ipaddress.ip_address(target_ip).packed
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        req = b"\x04\x01" + target_port.to_bytes(2, "big") + ip_bytes + b"\x00"
        s.sendall(req)
        resp = _recv_exact(s, 8)

        if len(resp) < 2 or resp[1] != 90:
            cd = resp[1] if len(resp) >= 2 else None
            raise RuntimeError(f"SOCKS4 connect failed (CD={cd})")

def _proxy_check_socks5(host: str, port: int, user: str, pwd: str, timeout: float,
                        target_ip: str, target_port: int) -> None:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)

        if user or pwd:
            s.sendall(b"\x05\x02\x00\x02")
        else:
            s.sendall(b"\x05\x01\x00")

        ver_method = _recv_exact(s, 2)
        if ver_method[0] != 0x05:
            raise RuntimeError("SOCKS5 bad greeting response")
        method = ver_method[1]
        if method == 0xFF:
            raise RuntimeError("SOCKS5: no acceptable auth method")
        if method == 0x02:

            u = (user or "").encode("utf-8")
            p = (pwd or "").encode("utf-8")
            if len(u) > 255 or len(p) > 255:
                raise RuntimeError("SOCKS5: username/password too long")
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            auth = _recv_exact(s, 2)
            if auth[0] != 0x01 or auth[1] != 0x00:
                raise RuntimeError("SOCKS5 auth failed")

        ip_bytes = ipaddress.ip_address(target_ip).packed
        req = b"\x05\x01\x00\x01" + ip_bytes + target_port.to_bytes(2, "big")
        s.sendall(req)

        head = _recv_exact(s, 4)
        if head[0] != 0x05:
            raise RuntimeError("SOCKS5 bad connect response")
        rep = head[1]
        atyp = head[3]

        if atyp == 0x01:
            _ = _recv_exact(s, 4)
        elif atyp == 0x03:
            ln = _recv_exact(s, 1)[0]
            _ = _recv_exact(s, ln)
        elif atyp == 0x04:
            _ = _recv_exact(s, 16)
        _ = _recv_exact(s, 2)

        if rep != 0x00:
            raise RuntimeError(f"SOCKS5 connect failed (REP={rep})")

def proxy_check_live(proxy: Dict[str, Any],
                     timeout: float = 15.0,
                     target_ip: str = "1.1.1.1",
                     target_port: int = 443) -> (bool, float, str):
    """Return (ok, ms, message)."""

    MSG_MISSING = "Missing Host/Port."
    MSG_INCORRECT = "Invalid auth info (missing username or password)."

    if not proxy:
        return (False, 0.0, MSG_MISSING)

    host = str(proxy.get("host") or "").strip()
    port = proxy.get("port")

    if not host or not port:
        return (False, 0.0, MSG_MISSING)

    try:
        port = int(port)
    except Exception:
         return (False, 0.0, "Port must be a number.")

    ptype = str(proxy.get("type") or "http").strip().lower()
    user = str(proxy.get("username") or "").strip()
    pwd  = str(proxy.get("password") or "").strip()

    if (user and not pwd) or (not user and pwd):
        return (False, 0.0, MSG_INCORRECT)

    t0 = time.perf_counter()
    try:

        with socket.create_connection((host, port), timeout=timeout):
            pass
    except socket.timeout:
        return (False, 0.0, "Error: Connection to the proxy timed out.")
    except ConnectionRefusedError:
        return (False, 0.0, "Error: Proxy refused the connection. Re-check IP/Port.")
    except Exception as e:
        return (False, 0.0, f"TCP error: {str(e)}")

    tcp_ms = (time.perf_counter() - t0) * 1000.0

    try:
        if ptype == "http":
            _proxy_check_http(host, port, user, pwd, timeout, target_ip, target_port)
        elif ptype == "socks4":
            _proxy_check_socks4(host, port, timeout, target_ip, target_port)
        elif ptype == "socks5":
            _proxy_check_socks5(host, port, user, pwd, timeout, target_ip, target_port)
        else:
            _proxy_check_http(host, port, user, pwd, timeout, target_ip, target_port)

    except Exception as e:

        return (False, tcp_ms, f"Protocol error: {str(e)}")

    geo_msg = "Unknown"
    try:
        geo_info = fetch_proxy_geo(proxy, timeout=5.0)
        if geo_info and geo_info.get("status") == "success":
            country = geo_info.get("country", "Unknown")
            city = geo_info.get("city", "")
            geo_msg = f"{country}, {city}"
    except Exception:
        pass

    return (True, tcp_ms, f"Live | {geo_msg} | {int(tcp_ms)}ms")

class ProxyCheckWorker(QtCore.QThread):
    sig_done = Signal(bool, float, str)

    def __init__(self, proxy: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._proxy = proxy

    def run(self):
        ok, ms, msg = proxy_check_live(self._proxy)
        self.sig_done.emit(ok, ms, msg)
        
# =============================================================================
# APP PATHS & JSON HELPERS
# =============================================================================

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "app.config.json"

def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")

# =============================================================================
# LOGGING
# =============================================================================

_LOGGER_CACHE: Dict[str, logging.Logger] = {}

def _make_logger(name: str, logfile: Path) -> logging.Logger:
    """Create a file logger and avoid duplicate handlers."""
    key = f"{name}|{str(logfile)}"
    if key in _LOGGER_CACHE:
        return _LOGGER_CACHE[key]

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logfile.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )

    fh = RotatingFileHandler(
        filename=str(logfile),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)
    logger.addHandler(fh)

    _LOGGER_CACHE[key] = logger
    return logger

def get_app_logger() -> logging.Logger:
    return _make_logger("gologin_app", APP_DIR / "logs" / "app.log")

def new_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def get_profile_logger(profile_folder: str, run_id: str) -> logging.Logger:
    log_file = (APP_DIR / "profiles" / profile_folder / "logs" / f"run_{run_id}.log")
    return _make_logger(f"gologin_profile_{profile_folder}_{run_id}", log_file)

def plog_info(logger: logging.Logger, profile_id: Any, folder: str, run_id: str, msg: str) -> None:
    logger.info(f"[profile_id={profile_id} folder={folder} run_id={run_id}] {msg}")

def _close_logger_key(key: str) -> None:
    lg = _LOGGER_CACHE.pop(key, None)
    if not lg:
        return

    for h in list(lg.handlers):
        try:
            h.flush()
        except Exception:
            pass
        try:
            h.close()
        except Exception:
            pass
        try:
            lg.removeHandler(h)
        except Exception:
            pass

def close_profile_loggers_for_folder(profile_folder: str) -> None:
    """
    Close all loggers currently writing to profiles/<folder>/logs/*
    => release run_*.log file handles so renaming/deleting the folder won't hit WinError 32.
    """
    if not profile_folder:
        return

    needle_win = f"{os.sep}profiles{os.sep}{profile_folder}{os.sep}logs{os.sep}"
    needle_posix = f"/profiles/{profile_folder}/logs/"

    for key in list(_LOGGER_CACHE.keys()):
        parts = key.split("|", 1)
        if len(parts) != 2:
            continue
        logfile = parts[1]
        if (needle_win in logfile) or (needle_posix in logfile):
            _close_logger_key(key)

@dataclass
class ProfileRow:
    id: str
    folder: str
    name: str
    os: str
    status: str
    proxy_display: str = ""

# =============================================================================
# WINDOWS: WIN32 HELPERS
# =============================================================================

_WIN_OK = sys.platform.startswith("win")

if _WIN_OK:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL

    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int

    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int

    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SetWindowTextW.restype = wintypes.BOOL

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long)]

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL

    GWL_EXSTYLE = -20
    GWLP_HWNDPARENT = -8

    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000

    _SetWindowLongPtr = getattr(user32, "SetWindowLongPtrW", None)
    _GetWindowLongPtr = getattr(user32, "GetWindowLongPtrW", None)
    if _SetWindowLongPtr is None:
        _SetWindowLongPtr = user32.SetWindowLongW
        _GetWindowLongPtr = user32.GetWindowLongW

    _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
    _SetWindowLongPtr.restype = wintypes.LPARAM

    _GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLongPtr.restype = wintypes.LPARAM

    def _win_get_pid(hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def _win_get_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value or ""

    def _win_get_window_text(hwnd: int) -> str:
        n = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, n + 2)
        return buf.value or ""

    def _win_set_window_text(hwnd: int, text: str) -> bool:
        try:
            return bool(user32.SetWindowTextW(wintypes.HWND(hwnd), str(text)))
        except Exception:
            return False

    def _win_is_visible(hwnd: int) -> bool:
        try:
            return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))
        except Exception:
            return False

    def _win_is_iconic(hwnd: int) -> bool:
        try:
            return bool(user32.IsIconic(wintypes.HWND(hwnd)))
        except Exception:
            return False

    def _win_get_window_rect(hwnd: int):
        rc = RECT()
        ok = user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rc))
        if not ok:
            return None
        return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))

    def _win_get_client_origin(hwnd: int):
        pt = POINT(0, 0)
        ok = user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
        if not ok:
            return None
        return (int(pt.x), int(pt.y))

    def _win_find_main_window_for_pid(pid: int):
        pid = int(pid)
        found = []

        @EnumWindowsProc
        def _cb(hwnd, lparam):
            try:
                hp = _win_get_pid(hwnd)
                if hp != pid:
                    return True
                if not _win_is_visible(hwnd):
                    return True
                cls = _win_get_class(hwnd)

                if cls.startswith("Chrome_WidgetWin"):
                    found.append(int(hwnd))
                else:
                    found.append(int(hwnd))
            except Exception:
                pass
            return True

        try:
            user32.EnumWindows(_cb, 0)
        except Exception:
            return None

        if not found:
            return None

        best = None
        best_area = -1
        for hwnd in found:
            r = _win_get_window_rect(hwnd)
            if not r:
                continue
            l, t, rr, bb = r
            area = max(0, rr - l) * max(0, bb - t)
            if area > best_area:
                best_area = area
                best = hwnd
        return best

    def _win_make_overlay_native(hwnd_overlay: int, hwnd_owner: int):

        try:
            _SetWindowLongPtr(wintypes.HWND(hwnd_overlay), GWLP_HWNDPARENT, wintypes.LPARAM(int(hwnd_owner)))
        except Exception:
            pass
        try:
            ex = int(_GetWindowLongPtr(wintypes.HWND(hwnd_overlay), GWL_EXSTYLE))
            ex |= (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE)
            _SetWindowLongPtr(wintypes.HWND(hwnd_overlay), GWL_EXSTYLE, wintypes.LPARAM(ex))
        except Exception:
            pass

else:
    def _win_find_main_window_for_pid(pid: int):
        return None
    def _win_get_client_origin(hwnd: int):
        return None
    def _win_is_visible(hwnd: int) -> bool:
        return False
    def _win_is_iconic(hwnd: int) -> bool:
        return False
    def _win_get_window_text(hwnd: int) -> str:
        return ""
    def _win_set_window_text(hwnd: int, text: str) -> bool:
        return False
    def _win_make_overlay_native(hwnd_overlay: int, hwnd_owner: int):
        return

# =============================================================================
# WINDOWS: BROWSER NAME BADGE
# =============================================================================

class ProfileNameOverlay(QWidget):

    def __init__(self, name: str):
        super().__init__(None)
        self._name = name or ""
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        try:
            self.setWindowFlag(Qt.WindowTransparentForInput, True)
        except Exception:
            pass
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)

        self.lbl = QLabel(self._name)
        self.lbl.setTextInteractionFlags(Qt.NoTextInteraction)
        self.lbl.setStyleSheet(
            "QLabel{"
            "color: white;"
            "background: rgba(0,0,0,140);"
            "padding: 4px 8px;"
            "border-radius: 8px;"
            "font-weight: 600;"
            "}"
        )
        lay.addWidget(self.lbl)
        self.setLayout(lay)
        self.adjustSize()

    def set_name(self, name: str):
        self._name = name or ""
        self.lbl.setText(self._name)
        self.adjustSize()

@dataclass
class _BadgeEntry:
    pid: int
    name: str
    hwnd: int = 0
    overlay: ProfileNameOverlay = None
    native_ready: bool = False
    last_title_ts: float = 0.0
    last_pos: tuple = None

class BrowserBadgeManager(QtCore.QObject):
    """Best-effort browser name display.

    (v2) Based on feedback: disable the overlay badge because it is inconsistent across monitors.
    This manager keeps only the Chrome window-title prefix to distinguish profiles more reliably.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True
        self.interval_ms = 600
        self.title_interval_s = 1.2

        self._entries: Dict[int, dict] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def attach(self, pid: int, name: str):
        if not self.enabled or not _WIN_OK:
            return
        try:
            pid = int(pid)
        except Exception:
            return
        if pid <= 0:
            return
        display = (name or "").strip() or f"Profile {pid}"
        e = self._entries.get(pid)
        if e is None:
            self._entries[pid] = {"name": display, "hwnd": 0, "last": 0.0}
        else:
            e["name"] = display

    def detach(self, pid: int):
        try:
            pid = int(pid)
        except Exception:
            return
        self._entries.pop(pid, None)

    def _ensure_title_prefix(self, hwnd: int, profile_name: str):
        if not hwnd:
            return
        try:
            cur = _win_get_window_text(hwnd) or ""
        except Exception:
            cur = ""
        prefix = f"{profile_name} - "
        if cur.startswith(prefix):
            return
        new_title = prefix + cur if cur else prefix.rstrip()
        try:
            _win_set_window_text(hwnd, new_title)
        except Exception:
            pass

    def _tick(self):
        if not self.enabled or not _WIN_OK:
            return
        now = time.time()

        for pid, e in list(self._entries.items()):

            try:
                if not psutil.pid_exists(int(pid)):
                    self.detach(pid)
                    continue
            except Exception:
                pass

            hwnd = int(e.get("hwnd") or 0)
            if not hwnd:
                try:
                    hwnd = int(_win_find_main_window_for_pid(int(pid)) or 0)
                    e["hwnd"] = hwnd
                except Exception:
                    hwnd = 0

            if not hwnd:
                continue

            last = float(e.get("last") or 0.0)
            if now - last < float(self.title_interval_s):
                continue

            try:
                if _win_is_visible(hwnd) and (not _win_is_iconic(hwnd)):
                    self._ensure_title_prefix(hwnd, str(e.get("name") or ""))
            except Exception:
                pass

            e["last"] = now

    def shutdown(self):
        """Stop timer and clear tracking entries (safe to call multiple times)."""
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self._entries.clear()
        except Exception:
            pass

# =============================================================================
# STORAGE
# =============================================================================

class Storage:
    def __init__(self):

        self.config = load_json(CONFIG_PATH, {})
        storage_cfg = self.config.get("storage", {})
        profile_root_rel = storage_cfg.get("profileRootPath", ".\\profiles")
        self.profile_root = (APP_DIR / profile_root_rel).resolve()

    def index_path(self) -> Path:
        return self.profile_root / "index.json"

    def ensure(self):
        self.profile_root.mkdir(parents=True, exist_ok=True)
        if not self.index_path().exists():
            save_json(self.index_path(), {"profiles": []})

class ProxyBridgeError(Exception):
    pass

# =============================================================================
# PROXY BRIDGE: LOCAL HTTP CONNECT -> UPSTREAM SOCKS5
# =============================================================================

class ProxyBridgeInstance:
    """
    One local HTTP proxy server (loopback) that forwards traffic through an upstream SOCKS5 proxy with USER/PASS auth.
    Chrome will be configured to use: --proxy-server=http://127.0.0.1:<local_port>
    """

    def __init__(self, profile_id: str, folder: str, upstream: dict):
        self.profile_id = str(profile_id)
        self.folder = folder

        self.up_type = (upstream.get("type") or "socks5").strip().lower()
        self.up_host = (upstream.get("host") or "").strip()
        self.up_port = int(upstream.get("port") or 0)
        self.up_user = (upstream.get("username") or "").strip()
        self.up_pass = (upstream.get("password") or "").strip()

        self.local_host = "127.0.0.1"
        self.local_port = 0

        self.thread = None
        self.loop = None
        self.server = None
        self._ready_evt = threading.Event()
        self._stop_evt = threading.Event()
        self._start_error = None

        self._tasks = set()

    def masked_upstream(self) -> str:
        if self.up_user or self.up_pass:
            return f"{self.up_type}://{self.up_user}:***@{self.up_host}:{self.up_port}"
        return f"{self.up_type}://{self.up_host}:{self.up_port}"

class ProxyBridgeManager:
    """
    Manage per-profile local HTTP CONNECT proxy bridges that authenticate to upstream SOCKS5 (user/pass).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._bridges = {}

    def start_socks5_auth_bridge(self, profile_id: str, folder: str, upstream: dict, timeout: float = 4.0) -> ProxyBridgeInstance:
        pid = str(profile_id)
        inst = ProxyBridgeInstance(pid, folder, upstream)

        if inst.up_type != "socks5":
            raise ProxyBridgeError(f"Unsupported upstream type for SOCKS5-auth bridge: {inst.up_type}")
        if not inst.up_host or inst.up_port <= 0:
            raise ProxyBridgeError("Upstream SOCKS5 host/port is missing")
        if not (inst.up_user and inst.up_pass):
            raise ProxyBridgeError("Upstream SOCKS5 credentials must include BOTH username and password")

        with self._lock:
            old = self._bridges.pop(pid, None)
        if old:
            try:
                self._stop_instance(old, join_timeout=1.5)
            except Exception:
                pass

        t = threading.Thread(target=self._run_instance, args=(inst,), daemon=True)
        inst.thread = t
        t.start()

        ok = inst._ready_evt.wait(timeout=timeout)
        if not ok:
            try:
                self._stop_instance(inst, join_timeout=1.0)
            except Exception:
                pass
            raise ProxyBridgeError("Bridge start timeout")

        if inst._start_error:
            raise ProxyBridgeError(inst._start_error)

        with self._lock:
            self._bridges[pid] = inst

        return inst

    def stop_bridge(self, profile_id: str):
        pid = str(profile_id)
        with self._lock:
            inst = self._bridges.pop(pid, None)
        if not inst:
            return
        self._stop_instance(inst, join_timeout=2.0)

    def stop_all(self):
        with self._lock:
            items = list(self._bridges.items())
            self._bridges.clear()
        for _, inst in items:
            try:
                self._stop_instance(inst, join_timeout=2.0)
            except Exception:
                pass

    def _stop_instance(self, inst: ProxyBridgeInstance, join_timeout: float = 2.0):
        inst._stop_evt.set()

        loop = inst.loop
        if loop and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(inst), loop)
                fut.result(timeout=join_timeout)
            except Exception:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass

        if inst.thread and inst.thread.is_alive():
            inst.thread.join(timeout=join_timeout)

    def _run_instance(self, inst: ProxyBridgeInstance):
        try:
            if sys.platform.startswith("win"):
                loop = asyncio.SelectorEventLoop()
            else:
                loop = asyncio.new_event_loop()

            inst.loop = loop
            asyncio.set_event_loop(loop)

            def _exc_handler(loop, context):
                exc = context.get("exception")
                we = getattr(exc, "winerror", None)

                if isinstance(exc, OSError) and we in (10054, 10053, 10038):
                    return
                if isinstance(exc, BrokenPipeError):
                    return

                msg = context.get("message")
                try:
                    get_app_logger().warning(f"[bridge] loop exception: {msg} exc={repr(exc)}")
                except Exception:
                    pass

                loop.default_exception_handler(context)

            loop.set_exception_handler(_exc_handler)

            loop.run_until_complete(self._start_server_async(inst))
            if inst._stop_evt.is_set():
                loop.run_until_complete(self._shutdown_async(inst))
                return

            loop.run_forever()
        except Exception as e:
            inst._start_error = str(e)
            inst._ready_evt.set()
        finally:
            try:
                loop = inst.loop
                if loop and not loop.is_closed():

                    try:
                        asyncio.set_event_loop(loop)
                    except Exception:
                        pass

                    async def _drain_pending():

                        cur = asyncio.current_task()
                        tasks = [t for t in asyncio.all_tasks() if t is not cur]

                        for t in tasks:
                            try:
                                t.cancel()
                            except Exception:
                                pass

                        if tasks:

                            try:
                                await asyncio.wait_for(
                                    asyncio.gather(*tasks, return_exceptions=True),
                                    timeout=2.0
                                )
                            except Exception:
                                pass

                    try:
                        loop.run_until_complete(_drain_pending())
                    except Exception:
                        pass

                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:
                        pass

                    try:
                        loop.run_until_complete(loop.shutdown_default_executor())
                    except Exception:
                        pass

                    try:
                        loop.close()
                    except Exception:
                        pass
            except Exception:
                pass

    async def _start_server_async(self, inst: ProxyBridgeInstance):
        server = await asyncio.start_server(
            lambda r, w: self._handle_client(inst, r, w),
            host=inst.local_host,
            port=0
        )
        inst.server = server
        socks = getattr(server, "sockets", None) or []
        if socks:
            inst.local_port = int(socks[0].getsockname()[1])

        inst._ready_evt.set()

    async def _shutdown_async(self, inst: ProxyBridgeInstance):
        try:
            if inst.server:
                inst.server.close()
                await inst.server.wait_closed()
        except Exception:
            pass

        tasks = list(inst._tasks)
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass

        try:
            asyncio.get_running_loop().stop()
        except Exception:
            pass

    async def _handle_client(self, inst: ProxyBridgeInstance, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        if task:
            inst._tasks.add(task)

        try:
            line = await reader.readline()
            if not line:
                return

            req = line.decode("latin1", "ignore").strip()
            parts = req.split()
            if len(parts) < 3:
                await self._send_http(writer, 400, b"Bad Request")
                return

            method, target, version = parts[0].upper(), parts[1], parts[2]

            hdr_lines = []
            while True:
                h = await reader.readline()
                if not h or h in (b"\r\n", b"\n"):
                    break
                hdr_lines.append(h)

            if method == "CONNECT":
                dst_host, dst_port = self._parse_hostport(target)
                if not dst_host or dst_port <= 0:
                    await self._send_http(writer, 400, b"Bad CONNECT target")
                    return

                try:
                    up_r, up_w = await self._socks5_connect(inst, dst_host, dst_port)
                except Exception as e:
                    try:
                        get_app_logger().info(f"[bridge profile_id={inst.profile_id}] SOCKS5 connect fail {dst_host}:{dst_port} err={e}")
                    except Exception:
                        pass
                    await self._send_http(writer, 502, b"Bad Gateway")
                    return

                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()

                await self._tunnel(reader, writer, up_r, up_w)
                return

            if target.startswith("http://") or target.startswith("https://"):
                try:
                    u = urlsplit(target)
                    dst_host = u.hostname or ""
                    dst_port = u.port or (443 if u.scheme == "https" else 80)
                    path = (u.path or "/") + (("?" + u.query) if u.query else "")
                except Exception:
                    await self._send_http(writer, 400, b"Bad URL")
                    return

                try:
                    up_r, up_w = await self._socks5_connect(inst, dst_host, dst_port)
                except Exception:
                    await self._send_http(writer, 502, b"Bad Gateway")
                    return

                up_w.write(f"{method} {path} {version}\r\n".encode("latin1", "ignore"))

                has_host = any(h.lower().startswith(b"host:") for h in hdr_lines)
                if not has_host:
                    up_w.write(f"Host: {dst_host}\r\n".encode("latin1", "ignore"))

                for h in hdr_lines:
                    hl = h.lower()
                    if hl.startswith(b"proxy-connection:"):
                        continue
                    if hl.startswith(b"connection:"):
                        continue
                    up_w.write(h)

                up_w.write(b"Connection: close\r\n\r\n")
                await up_w.drain()

                await self._tunnel(reader, writer, up_r, up_w)
                return

            await self._send_http(writer, 405, b"Method Not Allowed")

        finally:
            try:
                writer.close()

                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.3)
                except Exception:
                    pass
            except Exception:
                pass

            if task:
                inst._tasks.discard(task)

    @staticmethod
    async def _send_http(writer: asyncio.StreamWriter, code: int, msg: bytes):
        writer.write(b"HTTP/1.1 " + str(code).encode("ascii") + b" " + msg + b"\r\n\r\n")
        await writer.drain()

    @staticmethod
    def _parse_hostport(target: str):
        t = target.strip()

        if t.startswith("[") and "]" in t:
            host = t[1:t.index("]")]
            rest = t[t.index("]") + 1:]
            if rest.startswith(":"):
                rest = rest[1:]
            try:
                port = int(rest)
            except Exception:
                port = 0
            return host, port

        if ":" not in t:
            return t, 0

        host, port_s = t.rsplit(":", 1)
        try:
            port = int(port_s)
        except Exception:
            port = 0
        return host, port

    async def _socks5_connect(self, inst: ProxyBridgeInstance, dst_host: str, dst_port: int, timeout: float = 8.0):
        r, w = await asyncio.wait_for(asyncio.open_connection(inst.up_host, inst.up_port), timeout=timeout)

        w.write(b"\x05\x01\x02")
        await w.drain()
        resp = await asyncio.wait_for(r.readexactly(2), timeout=timeout)
        if resp[0] != 0x05:
            raise ProxyBridgeError("Bad SOCKS version")
        if resp[1] == 0xFF:
            raise ProxyBridgeError("SOCKS5 auth method not accepted")
        if resp[1] != 0x02:
            raise ProxyBridgeError("Unexpected SOCKS5 method")

        u = inst.up_user.encode("utf-8")
        p = inst.up_pass.encode("utf-8")
        if len(u) > 255 or len(p) > 255:
            raise ProxyBridgeError("Username/password too long")

        w.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        await w.drain()
        a = await asyncio.wait_for(r.readexactly(2), timeout=timeout)
        if a[0] != 0x01 or a[1] != 0x00:
            raise ProxyBridgeError("SOCKS5 auth failed")

        try:
            ip = ipaddress.ip_address(dst_host)
            if ip.version == 4:
                atyp = 0x01
                addr = ip.packed
            else:
                atyp = 0x04
                addr = ip.packed
        except ValueError:
            host_b = dst_host.encode("idna")
            if len(host_b) > 255:
                raise ProxyBridgeError("Domain too long")
            atyp = 0x03
            addr = bytes([len(host_b)]) + host_b

        w.write(b"\x05\x01\x00" + bytes([atyp]) + addr + struct.pack("!H", int(dst_port)))
        await w.drain()

        head = await asyncio.wait_for(r.readexactly(4), timeout=timeout)
        if head[0] != 0x05:
            raise ProxyBridgeError("Bad SOCKS reply")
        rep = head[1]
        if rep != 0x00:
            raise ProxyBridgeError(f"SOCKS CONNECT failed rep={rep}")
        atyp_r = head[3]

        if atyp_r == 0x01:
            await r.readexactly(4)
        elif atyp_r == 0x04:
            await r.readexactly(16)
        elif atyp_r == 0x03:
            ln = (await r.readexactly(1))[0]
            await r.readexactly(ln)
        else:
            raise ProxyBridgeError("Unknown ATYP in reply")
        await r.readexactly(2)

        return r, w

    async def _tunnel(self, cr: asyncio.StreamReader, cw: asyncio.StreamWriter,
              ur: asyncio.StreamReader, uw: asyncio.StreamWriter):
        async def pump(r: asyncio.StreamReader, w: asyncio.StreamWriter):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except (ConnectionResetError, BrokenPipeError):
                return
            except OSError as e:
                we = getattr(e, "winerror", None)
                if we in (10054, 10053, 10038):
                    return
                return
            except asyncio.CancelledError:
                return
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        t1 = asyncio.create_task(pump(cr, uw))
        t2 = asyncio.create_task(pump(ur, cw))

        try:
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:

            for t in (t1, t2):
                try:
                    t.cancel()
                except Exception:
                    pass
            await asyncio.gather(t1, t2, return_exceptions=True)
            raise
        finally:
            for t in (t1, t2):
                if not t.done():
                    try:
                        t.cancel()
                    except Exception:
                        pass
            await asyncio.gather(t1, t2, return_exceptions=True)

# =============================================================================
# PROCESS CONTROL: GRACEFUL SHUTDOWN
# =============================================================================

if sys.platform.startswith("win"):

    if not hasattr(user32, "PostMessageW"):
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL

    WM_CLOSE = 0x0010

    def _win_graceful_close_pid(pid: int, timeout: float = 3.0) -> bool:
        """
        Send a window-close command (WM_CLOSE) to all windows belonging to this PID.
        Return True if the process exited; False if the timeout elapsed.
        """
        pid = int(pid)
        hwnds = []

        def _cb(hwnd, _):
            try:
                hp = _win_get_pid(hwnd)
                if hp == pid:
                    hwnds.append(hwnd)
            except: pass
            return True

        try:
            user32.EnumWindows(EnumWindowsProc(_cb), 0)
        except Exception:
            return False

        if not hwnds:
            return False

        for h in hwnds:
            try:
                user32.PostMessageW(wintypes.HWND(h), WM_CLOSE, 0, 0)
            except: pass

        try:
            proc = psutil.Process(pid)
            try:
                proc.wait(timeout=timeout)
                return True
            except psutil.TimeoutExpired:
                return False
        except psutil.NoSuchProcess:
            return True
else:

    def _win_graceful_close_pid(pid: int, timeout: float = 3.0) -> bool:
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout)
            return True
        except: return False

# =============================================================================
# PROFILE MANAGER
# =============================================================================

class ProfileManager:
    def __init__(self, storage: Storage):
        self.s = storage
        self.s.ensure()
        self._procs = {}
        self.on_profile_exit = None
        self.bridge = ProxyBridgeManager()

    def _read_index_profiles(self) -> List[Dict[str, Any]]:
        idx = load_json(self.s.index_path(), {"profiles": []})
        return idx.get("profiles", [])

    def _write_index(self, profiles: List[Dict[str, Any]]):
        save_json(self.s.index_path(), {"profiles": profiles})

    def reconcile_index(self) -> int:
        """Remove index entries whose folder/profile.json no longer exist on disk."""
        profiles = self._read_index_profiles()
        kept: List[Dict[str, Any]] = []
        removed = 0

        for p in profiles:
            folder = str(p.get("folder") or "").strip()
            if not folder:
                removed += 1
                continue

            pdir = self.s.profile_root / folder
            if not pdir.exists():
                removed += 1
                continue

            if not (pdir / "profile.json").exists():
                removed += 1
                continue

            kept.append(p)

        if removed:
            self._write_index(kept)

        return removed

    def _next_id(self) -> str:
        used = {p["id"] for p in self._read_index_profiles() if "id" in p}
        n = 1
        while True:
            pid = f"p_{n:04d}"
            if pid not in used:
                return pid
            n += 1

    def list_profiles(self) -> List[ProfileRow]:

        try:
            self.reconcile_index()
        except Exception:
            pass

        rows: List[ProfileRow] = []

        for p in self._read_index_profiles():
            folder = p.get("folder", "")
            pdir = self.s.profile_root / folder

            proxy_text = (p.get("proxyDisplay") or "").strip()
            if not proxy_text:
                prof = load_json(pdir / "profile.json", {})
                proxy_text = _proxy_display(prof.get("proxy") or {})

            rows.append(ProfileRow(
                id=p.get("id", ""),
                folder=folder,
                name=p.get("name", ""),
                os=p.get("os", "windows"),
                status=p.get("status", "stopped"),
                proxy_display=proxy_text,
            ))

        return rows

    def create_profile(self, name: str, os_name: str) -> ProfileRow:
        pid = self._next_id()
        safe = "".join([c for c in name if c.isalnum() or c in ("_", "-", " ")])[:40].strip() or "profile"
        folder = f"{pid}_{safe}".replace(" ", "_")
        pdir = self.s.profile_root / folder
        pdir.mkdir(parents=True, exist_ok=True)

        fp_data = generate_fingerprint(os_name)

        save_json(pdir / "profile.json", {
            "id": pid,
            "name": name,
            "os": os_name,
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "runtime": {"status": "stopped", "pid": None, "lastError": None},
            "paths": {"browserDataDir": ".\\chrome_data"},
            "fingerprint": fp_data,
            "webrtc": {"mode": "altered"}
        })
        (pdir / "chrome_data").mkdir(exist_ok=True)

        ps = self._read_index_profiles()
        ps.append({"id": pid, "folder": folder, "name": name, "os": os_name, "status": "stopped", "proxyDisplay": ""})
        self._write_index(ps)
        return ProfileRow(id=pid, folder=folder, name=name, os=os_name, status="stopped", proxy_display="")

    def update_profile(self, row: ProfileRow, name: str, os_name: str, notes: str = "", proxy: dict | None = None, webrtc: dict | None = None) -> None:
        """Update profile metadata. If profile name changes, also rename the profile folder on disk (stopped-only)."""
        if not row:
            raise ValueError("row is required")

        if getattr(row, "status", "") == "running":
            raise RuntimeError("Cannot rename the profile while it is running. Stop it first.")

        old_folder = str(row.folder)

        safe = "".join([c for c in (name or "") if c.isalnum() or c in ("_", "-", " ")])[:40].strip() or "profile"
        new_folder_base = f"{row.id}_{safe}".replace(" ", "_")
        new_folder = new_folder_base

        new_folder = new_folder.rstrip(" .")

        if new_folder and new_folder != old_folder:
            root = self.s.profile_root
            src_dir = root / old_folder

            dst_dir = root / new_folder
            if dst_dir.exists():
                n = 1
                while True:
                    cand = f"{new_folder_base}_{n}".rstrip(" .")
                    cand_dir = root / cand
                    if not cand_dir.exists():
                        new_folder = cand
                        dst_dir = cand_dir
                        break
                    n += 1

            close_profile_loggers_for_folder(old_folder)

            last_err = None
            for _ in range(12):
                try:
                    os.rename(str(src_dir), str(dst_dir))
                    last_err = None
                    break
                except PermissionError as e:
                    last_err = e
                    time.sleep(0.15)

            if last_err is not None:
                raise RuntimeError(f"Cannot rename the folder (file is in use): {last_err}")

            row.folder = new_folder

        pdir = self.s.profile_root / row.folder
        prof_path = pdir / "profile.json"
        prof = load_json(prof_path, {})

        prof["name"] = name
        prof["os"] = os_name
        prof["notes"] = notes
        if proxy is not None:
            prof["proxy"] = proxy
        if webrtc is not None:
            prof["webrtc"] = webrtc
        prof["updatedAt"] = now_iso()

        save_json(prof_path, prof)

        profiles = self._read_index_profiles()
        for p in profiles:
            if p.get("id") == row.id:
                p["name"] = name
                p["os"] = os_name
                p["folder"] = row.folder
                if proxy is not None:
                    p["proxyDisplay"] = _proxy_display(proxy)
                break
        self._write_index(profiles)

    def delete_profile(self, row: ProfileRow) -> None:
        if not row:
            raise ValueError("row is required")

        if getattr(row, "status", "") == "running":
            raise RuntimeError("Stop the profile before deleting.")

        close_profile_loggers_for_folder(str(row.folder))

        pdir = self.s.profile_root / row.folder

        last_err = None
        for _ in range(12):
            try:
                if pdir.exists():
                    shutil.rmtree(pdir, ignore_errors=False)
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.15)

        if last_err is not None:

            raise RuntimeError(f"Cannot delete the profile folder (file is in use): {last_err}")

        ps = [p for p in self._read_index_profiles() if p.get("id") != row.id]
        self._write_index(ps)

    def _get_browser_path(self) -> Path:
        cfg = load_json(CONFIG_PATH, {})
        rel_or_abs = cfg.get("browser", {}).get("binaryPath", ".\\chromium\\chrome.exe")
        p = Path(rel_or_abs)
        if p.is_absolute():
            return p
        return (APP_DIR / rel_or_abs).resolve()

    def start_profile(self, row: ProfileRow) -> int:
        app_log = get_app_logger()
        run_id = new_run_id()
        plog = get_profile_logger(row.folder, run_id)

        pdir = self.s.profile_root / row.folder
        prof_path = pdir / "profile.json"
        prof = load_json(prof_path, {})

        plog_info(plog, row.id, row.folder, run_id, f"PROFILE START name={row.name}")

        prof.setdefault("runtime", {})
        prof["runtime"]["lastRunId"] = run_id
        save_json(prof_path, prof)

        bridge_inst = None
        bridge_runtime = {"enabled": False}

        try:

            runtime = prof.get("runtime") or {}
            pid_existing = runtime.get("pid")

            if pid_existing:
                try:
                    pid_int = int(pid_existing)
                    if psutil.pid_exists(pid_int):
                        try:
                            p = psutil.Process(pid_int)
                            name = (p.name() or "").lower()
                            if "chrome" in name:
                                raise RuntimeError("Profile is running (Chrome PID is still alive).")
                        except psutil.NoSuchProcess:
                            pass
                except ValueError:
                    pass

            proxy = (prof.get("proxy") or {})
            is_enabled = proxy.get("enabled")

            if is_enabled:
                h = str(proxy.get("host") or "").strip()
                p = str(proxy.get("port") or "").strip()
                if h and p:
                    plog_info(plog, row.id, row.folder, run_id, "Checking Proxy...")
                    is_alive, ms, msg = proxy_check_live(proxy, timeout=15.0)
                    if not is_alive:
                        plog_info(plog, row.id, row.folder, run_id, f"PROXY DIED: {msg}")
                        raise RuntimeError(f"Proxy Connection Failed: {msg}")
                    plog_info(plog, row.id, row.folder, run_id, f"Proxy Alive: {int(ms)}ms")

            chrome = self._get_browser_path()
            if not chrome.exists():
                raise FileNotFoundError(f"Chromium not found: {chrome}")

            user_data_dir = (pdir / "chrome_data").resolve()

            args = [
                str(chrome),
                f"--user-data-dir={str(user_data_dir)}",
                "--no-first-run",
                "--no-default-browser-check",
                
                # [QUAN TRỌNG] Thorium hỗ trợ cờ này mượt mà (không hiện cảnh báo)
                # Cờ này giúp ẩn dấu hiệu automation cấp thấp
                "--disable-blink-features=AutomationControlled", 
                
                # Fix lỗi Extension trên bản Portable
                "--disable-encryption", 
                
                # Tắt tính năng dịch vụ nền không cần thiết
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-breakpad",
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-hang-monitor",
                "--disable-ipc-flooding-protection",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-renderer-backgrounding",
                "--disable-sync",
                "--metrics-recording-only",
                "--password-store=basic",
                "--use-mock-keychain",
            ]

            active_proxy_url = ""

            if is_enabled:
                ptype = (proxy.get("type") or "http").strip().lower()
                host = (proxy.get("host") or "").strip()
                port = proxy.get("port")
                user = (proxy.get("username") or "").strip()
                pw = (proxy.get("password") or "").strip()

                args = [a for a in args if not str(a).startswith("--proxy-server=")]

                if host and port:
                    if ptype == "socks5" and (user and pw):
                        plog_info(plog, row.id, row.folder, run_id, f"BRIDGE START {host}:{port}")
                        bridge_inst = self.bridge.start_socks5_auth_bridge(row.id, row.folder, proxy, timeout=4.0)
                        local = f"http://{bridge_inst.local_host}:{bridge_inst.local_port}"
                        args.append(f"--proxy-server={local}")
                        active_proxy_url = local
                        bridge_runtime = {
                            "enabled": True,
                            "localHost": bridge_inst.local_host,
                            "localPort": bridge_inst.local_port,
                            "upstreamMasked": bridge_inst.masked_upstream(),
                            "startedAt": now_iso(),
                        }
                    else:

                        args.append(f"--proxy-server={ptype}://{host}:{int(port)}")

                        auth_part = f"{user}:{pw}@" if user and pw else ""
                        active_proxy_url = f"http://{auth_part}{host}:{port}"

            final_timezone = prof.get("timezone", "") if is_enabled else ""
            public_ip_to_spoof = ""

            if is_enabled and active_proxy_url:
                try:
                    plog_info(plog, row.id, row.folder, run_id, "Auto-detecting Timezone & IP...")

                    detected_tz, detected_ip = fetch_timezone_from_proxy(active_proxy_url)

                    if detected_tz:
                        plog_info(plog, row.id, row.folder, run_id, f"==> Detected TZ: {detected_tz}")
                        final_timezone = detected_tz
                        prof["timezone"] = detected_tz
                        save_json(prof_path, prof)

                    if detected_ip:
                        plog_info(plog, row.id, row.folder, run_id, f"==> Detected IP: {detected_ip}")
                        public_ip_to_spoof = detected_ip
                    else:

                        if host.replace('.','').isdigit():
                             public_ip_to_spoof = host

                except Exception as e:
                    plog_info(plog, row.id, row.folder, run_id, f"TZ/IP Check Error: {e}")

            webrtc = (prof.get("webrtc") or {})
            raw_mode = str(webrtc.get("mode") or "altered").strip().lower()
            if raw_mode == "disabled":
                mode = "disabled"
            else:
                mode = "altered"
            fp_data = prof.get("fingerprint")

            if not fp_data or "salt" not in fp_data.get("canvasNoise", {}):
                fp_data = generate_fingerprint(prof.get("os", "windows"))
                prof["fingerprint"] = fp_data
                save_json(prof_path, prof)

            def _add_once(flag: str):
                if flag not in args:
                    args.append(flag)

            os_raw = str(prof.get("os", "windows")).lower()
            ch_platform = "Windows"
            if "mac" in os_raw: ch_platform = "macOS"
            elif "linux" in os_raw: ch_platform = "Linux"

            _add_once(f"--ch-ua-platform={ch_platform}")
            _add_once("--lang=en-US")

            mode_for_ext = mode

            if is_enabled:
                _add_once("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
            else:
                _add_once("--force-webrtc-ip-handling-policy=default")

            ext_dir = pdir / "ext_antidetect"
            
            #if fp_data.get("userAgent"):
            #    args.append(f"--user-agent={fp_data['userAgent']}")

            try:
                badge_js = profile_ui.get_badge_script(row.name)

                build_profile_extension(
                    ext_path=ext_dir,
                    fingerprint=fp_data,
                    webrtc_mode=mode_for_ext,
                    timezone=final_timezone,
                    public_ip=public_ip_to_spoof,
                    extra_scripts={"ui_badge.js": badge_js}
                )
                append_load_extension_arg(args, ext_dir)
            except Exception as e:
                plog_info(plog, row.id, row.folder, run_id, f"EXT ERROR: {e}")

            plog_info(plog, row.id, row.folder, run_id, "CMD: " + " ".join(args))

            proc = subprocess.Popen(args, cwd=str(APP_DIR))
            pid = proc.pid

            prof.setdefault("runtime", {})
            prof["runtime"]["status"] = "running"
            prof["runtime"]["pid"] = pid
            prof["runtime"]["lastError"] = None
            prof["runtime"]["bridge"] = bridge_runtime
            prof["updatedAt"] = now_iso()
            save_json(prof_path, prof)

            ps = self._read_index_profiles()
            for p in ps:
                if p["id"] == row.id: p["status"] = "running"
            self._write_index(ps)

            self._procs[row.id] = proc

            def _watch_exit_full(profile_id: int, folder: str, p: subprocess.Popen):
                pid = int(getattr(p, "pid", 0) or 0)
                while p.poll() is None:
                    time.sleep(0.5)
                try:
                    pdir2 = self.s.profile_root / folder
                    prof2 = load_json(pdir2 / "profile.json", {})
                    prof2.setdefault("runtime", {})
                    cur_pid = (prof2.get("runtime") or {}).get("pid")
                    if cur_pid is not None and int(cur_pid) == int(p.pid):
                        prof2["runtime"]["status"] = "stopped"
                        prof2["runtime"]["pid"] = None
                        prof2["runtime"]["bridge"] = {"enabled": False}
                        save_json(pdir2 / "profile.json", prof2)
                        ps2 = self._read_index_profiles()
                        for it in ps2:
                             if it.get("id") == profile_id: it["status"] = "stopped"
                        self._write_index(ps2)
                except: pass
                try:
                    if self.on_profile_exit: self.on_profile_exit(profile_id)
                except: pass
                try: self.bridge.stop_bridge(profile_id)
                except: pass
                try: close_profile_loggers_for_folder(folder)
                except: pass

            t = threading.Thread(target=_watch_exit_full, args=(row.id, row.folder, proc), daemon=True)
            t.start()

            return pid

        except Exception as e:
            plog.exception(f"START FAILED: {e}")
            raise

    def stop_profile(self, row: ProfileRow) -> None:
        app_log = get_app_logger()

        pdir = self.s.profile_root / row.folder
        prof_path = pdir / "profile.json"
        prof = load_json(prof_path, {})

        runtime = prof.get("runtime") or {}
        run_id = runtime.get("lastRunId") or new_run_id()
        plog = get_profile_logger(row.folder, run_id)

        pid = runtime.get("pid")
        plog_info(plog, row.id, row.folder, run_id, f"PROFILE STOP request pid={pid}")

        try:
            if pid:
                pid_int = int(pid)

                is_closed_gracefully = False

                if psutil.pid_exists(pid_int):
                    plog_info(plog, row.id, row.folder, run_id, "Attempting Graceful Close (WM_CLOSE)...")

                    is_closed_gracefully = _win_graceful_close_pid(pid_int, timeout=3.0)

                if is_closed_gracefully:
                     plog_info(plog, row.id, row.folder, run_id, "Graceful Close SUCCESS.")
                else:

                    if psutil.pid_exists(pid_int):
                        plog_info(plog, row.id, row.folder, run_id, "Graceful timeout. Force killing...")
                        r = subprocess.run(
                            ["taskkill", "/PID", str(pid), "/T", "/F"],
                            capture_output=True, text=True
                        )
                        plog_info(plog, row.id, row.folder, run_id, f"TASKKILL rc={r.returncode}")

                self._procs.pop(row.id, None)

                try:
                    self.bridge.stop_bridge(row.id)
                except Exception:
                    pass

            prof.setdefault("runtime", {})
            prof["runtime"]["status"] = "stopped"
            prof["runtime"]["pid"] = None
            prof["runtime"]["lastError"] = None
            prof["runtime"]["bridge"] = {"enabled": False}
            prof["updatedAt"] = now_iso()
            save_json(prof_path, prof)

            ps = self._read_index_profiles()
            for p in ps:
                if p["id"] == row.id:
                    p["status"] = "stopped"
            self._write_index(ps)

            plog_info(plog, row.id, row.folder, run_id, "PROFILE STOPPED")

        except Exception as e:
            plog.exception(f"[profile_id={row.id} folder={row.folder} run_id={run_id}] STOP FAILED: {e}")
            app_log.exception(f"[profile_id={row.id} folder={row.folder} run_id={run_id}] STOP FAILED: {e}")
            raise

    def sync_runtime_states(self) -> set[str]:
        """
        If a profile has a dead PID, automatically clean up runtime state:
        - If the index says 'running' but the PID is dead -> set to 'stopped' (and return changed_ids for UI refresh)
        - If the index says 'stopped' but runtime still has a pid (app exited too fast) -> just clear pid/bridge/lastError
        """
        ps = self._read_index_profiles()
        changed_index = False
        changed_ids: set[str] = set()

        for p in ps:
            folder = p.get("folder")
            if not folder:
                continue

            pdir = self.s.profile_root / folder
            prof_path = pdir / "profile.json"
            prof = load_json(prof_path, {})
            runtime = prof.get("runtime") or {}

            pid_raw = runtime.get("pid")
            pid_int = None
            if pid_raw is not None:
                try:
                    pid_int = int(pid_raw)
                except Exception:
                    pid_int = None

            alive = False
            if pid_int and pid_int > 0:
                try:
                    alive = psutil.pid_exists(pid_int)
                except Exception:
                    alive = False

            if alive:
                continue

            runtime_changed = False
            if pid_raw is not None:
                runtime["pid"] = None
                runtime_changed = True

            if runtime.get("lastError") is not None:
                runtime["lastError"] = None
                runtime_changed = True

            br = runtime.get("bridge") or {}
            if br.get("enabled") is True:
                runtime["bridge"] = {"enabled": False}
                runtime_changed = True

            if str(p.get("status")) == "running":
                runtime["status"] = "stopped"
                p["status"] = "stopped"
                changed_index = True
                try:
                    changed_ids.add(str(p.get("id")))
                except Exception:
                    pass
                runtime_changed = True

                try:
                    self.bridge.stop_bridge(str(p.get("id")))
                except Exception:
                    pass

            if runtime_changed:
                prof["runtime"] = runtime
                prof["updatedAt"] = now_iso()
                save_json(prof_path, prof)

        if changed_index:
            self._write_index(ps)

        return changed_ids

# =============================================================================
# UI: PROFILE EDITOR
# =============================================================================

class ProfileEditorDialog(QDialog):
    def __init__(self, parent=None, title="Profile Editor",
             name="", os_name="windows", notes="", proxy=None, webrtc=None, is_create: bool = False):
        super().__init__(parent)
        self._init_proxy = proxy or {}
        self._init_webrtc = webrtc or {"mode": "altered"}
        self._parsing_proxy = False
        self.setWindowTitle(title)
        self.resize(520, 320)

        self.is_create = is_create
        self.qty_combo = None

        self.tabs = QTabWidget()

        general = QWidget()
        form = QFormLayout()

        self.name_input = QLineEdit(name)

        self.os_combo = QComboBox()
        self.os_combo.addItems(["windows", "macos", "linux"])
        idx = self.os_combo.findText(os_name)
        if idx >= 0:
            self.os_combo.setCurrentIndex(idx)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Notes (optional)")
        self.notes_input.setPlainText(notes or "")

        form.addRow("Name", self.name_input)

        if self.is_create:
            self.qty_combo = QComboBox()
            self.qty_combo.addItems(["1", "5", "10", "20", "50", "100", "500"])
            self.qty_combo.setCurrentText("1")
            self.qty_combo.currentTextChanged.connect(self.on_qty_changed)
            form.addRow("Quantity", self.qty_combo)
        form.addRow("OS", self.os_combo)
        form.addRow("Notes", self.notes_input)

        general.setLayout(form)
        self.tabs.addTab(general, "General")

        proxy_w = QWidget()
        proxy_form = QFormLayout()

        self.proxy_enable = QCheckBox("Enable proxy")
        btn_paste_proxy = QPushButton("Paste")
        btn_clean_proxy = QPushButton("Clean")
        btn_copy_proxy = QPushButton("Copy")
        btn_check_proxy = QPushButton("Check")
        btn_paste_proxy.setFixedWidth(60)
        btn_copy_proxy.setFixedWidth(60)
        btn_clean_proxy.setFixedWidth(60)
        btn_check_proxy.setFixedWidth(60)

        btn_paste_proxy.clicked.connect(self.on_paste_proxy)
        btn_copy_proxy.clicked.connect(self.on_copy_proxy)
        btn_clean_proxy.clicked.connect(self.on_clean_proxy)
        btn_check_proxy.clicked.connect(self.on_check_proxy)
        self.btn_check_proxy = btn_check_proxy

        row_enable = QHBoxLayout()
        row_enable.addWidget(self.proxy_enable)
        row_enable.addStretch(1)
        row_enable.addWidget(btn_paste_proxy)
        row_enable.addWidget(btn_clean_proxy)
        row_enable.addWidget(btn_copy_proxy)
        row_enable.addWidget(btn_check_proxy)

        proxy_form.addRow("", row_enable)

        self.proxy_enable.setChecked(False)

        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["http", "socks4", "socks5"])

        self.proxy_type.currentTextChanged.connect(self.on_proxy_type_changed)

        self.proxy_host = QLineEdit()
        self.proxy_host.textChanged.connect(self.on_proxy_host_changed)
        self.proxy_port = QLineEdit()
        self.proxy_user = QLineEdit()
        self.proxy_pass = QLineEdit()
        self.proxy_pass.setEchoMode(QLineEdit.Password)

        self.proxy_enable.toggled.connect(lambda _=None: self._reset_proxy_status())
        self.proxy_port.textChanged.connect(lambda _=None: self._reset_proxy_status())
        self.proxy_user.textChanged.connect(lambda _=None: self._reset_proxy_status())
        self.proxy_pass.textChanged.connect(lambda _=None: self._reset_proxy_status())

        p = {}
        try:
            p = self._init_proxy or {}
        except Exception:
            p = {}

        if isinstance(p, dict) and p.get("enabled"):
            self.proxy_enable.setChecked(True)
            t = p.get("type", "http")
            idx2 = self.proxy_type.findText(t)
            if idx2 >= 0:
                self.proxy_type.setCurrentIndex(idx2)
            self.proxy_host.setText(p.get("host", "") or "")
            self.proxy_port.setText(str(p.get("port", "") or ""))
            self.proxy_user.setText(p.get("username", "") or "")
            self.proxy_pass.setText(p.get("password", "") or "")

        proxy_form.addRow("Type", self.proxy_type)
        proxy_form.addRow("Host", self.proxy_host)
        proxy_form.addRow("Port", self.proxy_port)
        proxy_form.addRow("Username", self.proxy_user)
        proxy_form.addRow("Password", self.proxy_pass)
        self.proxy_status = QLabel("Not checked yet")
        self.proxy_status.setWordWrap(True)
        self.proxy_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        proxy_form.addRow("Status", self.proxy_status)

        self._proxy_check_worker = None

        proxy_w.setLayout(proxy_form)
        self.tabs.addTab(proxy_w, "Proxy")

        webrtc_w = QWidget()
        webrtc_form = QFormLayout()

        self.webrtc_mode = QComboBox()
        self.webrtc_mode.addItem("Altered (default)", "altered")
        self.webrtc_mode.addItem("Disabled", "disabled")

        w0 = {}
        try:
            w0 = self._init_webrtc or {}
        except Exception:
            w0 = {}

        mode0 = str(w0.get("mode") or "altered").strip().lower()
        if mode0 not in ("altered", "disabled"):
            mode0 = "altered"
        idx_w = self.webrtc_mode.findData(mode0)
        if idx_w >= 0:
            self.webrtc_mode.setCurrentIndex(idx_w)

        note = QLabel("Altered: restrict WebRTC UDP from leaking directly.\nDisabled: block the WebRTC API (video calls may break).")
        note.setWordWrap(True)

        webrtc_form.addRow("Mode", self.webrtc_mode)
        webrtc_form.addRow("", note)

        webrtc_w.setLayout(webrtc_form)
        self.tabs.addTab(webrtc_w, "WebRTC")

        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")

        btn_save.setDefault(True)
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        layout.addLayout(btns)
        self.setLayout(layout)

    def on_proxy_type_changed(self, ptype):
        if ptype == "socks4":
            self.proxy_user.setEnabled(False)
            self.proxy_pass.setEnabled(False)
            self.proxy_user.setPlaceholderText("SOCKS4 does not support auth")
            self.proxy_pass.setPlaceholderText("SOCKS4 does not support auth")
        else:
            self.proxy_user.setEnabled(True)
            self.proxy_pass.setEnabled(True)
            self.proxy_user.setPlaceholderText("")
            self.proxy_pass.setPlaceholderText("")

        self._reset_proxy_status()

    def parse_proxy_text(self, text: str):
        """
        Smart Proxy Parser V3.1 (Fix Import & Regex):
        - Handle common scheme typos (ocks5, s5...).
        - Cleanly split username/password without stray characters.
        """
        if self._parsing_proxy:
            return
        self._parsing_proxy = True

        try:

            text = (text or "").strip()

            text = "".join(ch for ch in text if ch.isprintable())

            text = re.sub(r"\s+", "", text)
            if not text:
                return

            scheme = "http"
            host = ""
            port = ""
            user = ""
            pwd = ""
            body = text

            match_scheme = re.match(r"^([a-zA-Z0-9\.\-]+)://(.*)$", text)

            if match_scheme:
                raw_scheme = match_scheme.group(1).lower()
                body = match_scheme.group(2)

                if "5" in raw_scheme: scheme = "socks5"
                elif "4" in raw_scheme: scheme = "socks4"
                elif any(x in raw_scheme for x in ["tp", "ht", "web", "ssl"]): scheme = "http"
                elif any(x in raw_scheme for x in ["ck", "so", "ox"]): scheme = "socks5"
                else: scheme = "http"

            if "@" in body:

                auth_part, loc_part = body.rsplit("@", 1)

                if ":" in auth_part:
                    user, pwd = auth_part.split(":", 1)
                else:
                    user = auth_part

                if ":" in loc_part:
                    host, port = loc_part.split(":", 1)
                else:
                    host = loc_part

            else:
                parts = body.split(":")

                if len(parts) >= 1: host = parts[0]
                if len(parts) >= 2: port = parts[1]

                if len(parts) == 4:
                    user = parts[2]
                    pwd = parts[3]
                elif len(parts) == 3:
                    user = parts[2]

            port_digits = re.findall(r"\d+", port)
            if not port_digits:
                return

            final_port = port_digits[0]

            self.proxy_enable.setChecked(True)

            idx = self.proxy_type.findText(scheme)
            if idx >= 0:
                self.proxy_type.setCurrentIndex(idx)

            self.proxy_host.setText(host)
            self.proxy_port.setText(final_port)
            self.proxy_user.setText(user)
            self.proxy_pass.setText(pwd)

            self._reset_proxy_status()

        except Exception as e:
            print(f"Proxy Parse Error: {e}")
        finally:
            self._parsing_proxy = False

    def on_paste_proxy(self):
        cb = QApplication.clipboard()
        text = cb.text()
        self.parse_proxy_text(text)

    def on_copy_proxy(self):
        host = self.proxy_host.text().strip()
        port = self.proxy_port.text().strip()
        user = self.proxy_user.text().strip()
        pwd = self.proxy_pass.text().strip()

        if not host or not port:
            QMessageBox.information(self, "Copy proxy", "Nothing to copy")
            return

        if user and pwd:
            text = f"{host}:{port}:{user}:{pwd}"
        else:
            text = f"{host}:{port}"

        QApplication.clipboard().setText(text)

    def on_clean_proxy(self):

        self.proxy_enable.setChecked(False)
        self.proxy_type.setCurrentText("http")
        self.proxy_host.clear()
        self.proxy_port.clear()
        self.proxy_user.clear()
        self.proxy_pass.clear()
        self._reset_proxy_status()

    def on_proxy_host_changed(self, text):
        if ":" in text:
            self.parse_proxy_text(text)

    def _reset_proxy_status(self):
        if hasattr(self, "proxy_status"):
            self.proxy_status.setStyleSheet("")
            self.proxy_status.setText("Not checked yet")

    def _gather_proxy_for_check(self) -> Dict[str, Any]:
        enabled = self.proxy_enable.isChecked()
        host = self.proxy_host.text().strip()
        port_txt = self.proxy_port.text().strip()
        port = None
        if port_txt:
            try:
                port = int(port_txt)
            except Exception:
                port = None

        return {
            "enabled": enabled,
            "type": self.proxy_type.currentText(),
            "host": host,
            "port": port,
            "username": self.proxy_user.text().strip(),
            "password": self.proxy_pass.text().strip(),
        }

    def on_check_proxy(self):
        proxy = self._gather_proxy_for_check()

        if not proxy.get("enabled"):
            self.proxy_status.setStyleSheet("")
            self.proxy_status.setText("Proxy is disabled (Enable proxy = OFF)")
            return

        if not proxy.get("host") or not proxy.get("port"):
            self.proxy_status.setStyleSheet("")
            self.proxy_status.setText("Missing Host/Port")
            return

        self.btn_check_proxy.setEnabled(False)
        self.proxy_status.setStyleSheet("")
        self.proxy_status.setText("Checking...")

        self._proxy_check_worker = ProxyCheckWorker(proxy, self)
        self._proxy_check_worker.sig_done.connect(self._on_proxy_check_done)
        self._proxy_check_worker.finished.connect(self._proxy_check_worker.deleteLater)
        self._proxy_check_worker.start()

    def _on_proxy_check_done(self, ok: bool, ms: float, msg: str):
        try:
            if ok:
                self.proxy_status.setStyleSheet("color: #2e7d32;")
                self.proxy_status.setText(f"LIVE (tunnel {ms:.0f} ms, {msg})")
            else:
                self.proxy_status.setStyleSheet("color: #c62828;")
                self.proxy_status.setText(f"DIE (tunnel {ms:.0f} ms) - {msg}")
        finally:
            self.btn_check_proxy.setEnabled(True)
            self._proxy_check_worker = None

    def on_qty_changed(self, txt: str):
        try:
            q = int((txt or "1").strip())
        except Exception:
            q = 1
        if q > 1:
            self.name_input.setText("")
            self.name_input.setEnabled(False)
            self.name_input.setPlaceholderText("Auto-random when Quantity > 1")
        else:
            self.name_input.setEnabled(True)
            self.name_input.setPlaceholderText("")

    def values(self):

        enabled = self.proxy_enable.isChecked()

        host = self.proxy_host.text().strip()
        port_txt = self.proxy_port.text().strip()
        user = self.proxy_user.text().strip()
        pwd = self.proxy_pass.text().strip()

        port = None
        if port_txt:
            try:

                clean_port = "".join(filter(str.isdigit, port_txt))
                port = int(clean_port)
            except Exception:
                port = None

        proxy = {
            "enabled": enabled,
            "type": self.proxy_type.currentText(),
            "host": host,
            "port": port,
            "username": user,
            "password": pwd,
        }

        return (
            self.name_input.text().strip(),
            self.os_combo.currentText(),
            self.notes_input.toPlainText().strip(),
            proxy,
            {"mode": (self.webrtc_mode.currentData() or "altered") if hasattr(self, "webrtc_mode") else "altered"}
        )

# =============================================================================
# UI: SETTINGS
# =============================================================================

class SettingsDialog(QtWidgets.QDialog):
    """
    Minimal Settings dialog:
    - Chrome/Chromium path
    - Profile root folder
    Compatibility note: if the MainWindow has self.pm.s.* then this can update it directly.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(720, 220)

        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.ed_chrome = QtWidgets.QLineEdit()
        self.ed_profiles = QtWidgets.QLineEdit()

        try:
            cfg = load_json(CONFIG_PATH, {})
            chrome_path = cfg.get("browser", {}).get("binaryPath", r".\chromium\chrome.exe")
            profiles_path = cfg.get("storage", {}).get("profileRootPath", r".\profiles")
            self.ed_chrome.setText(str(chrome_path or r".\chromium\chrome.exe"))
            self.ed_profiles.setText(str(profiles_path or r".\profiles"))
        except Exception:
            self.ed_chrome.setText(r".\chromium\chrome.exe")
            self.ed_profiles.setText(r".\profiles")

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(self.ed_chrome, 1)
        btn_browse_chrome = QtWidgets.QPushButton("Browse…")
        row1.addWidget(btn_browse_chrome)
        form.addRow("Chromium / Chrome.exe", row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self.ed_profiles, 1)
        btn_browse_profiles = QtWidgets.QPushButton("Browse…")
        row2.addWidget(btn_browse_profiles)
        form.addRow("Profiles folder", row2)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btn_reset = btn_box.addButton("Reset", QtWidgets.QDialogButtonBox.ResetRole)
        layout.addWidget(btn_box)

        btn_browse_chrome.clicked.connect(self._pick_chrome)
        btn_browse_profiles.clicked.connect(self._pick_profiles)
        btn_reset.clicked.connect(self._reset_defaults)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

    def _reset_defaults(self):
        self.ed_chrome.setText(r".\chromium\chrome.exe")
        self.ed_profiles.setText(r".\profiles")

    def _pick_chrome(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select chrome.exe",
            self.ed_chrome.text() or "",
            "chrome.exe (chrome.exe);;Executable (*.exe)"
        )
        if path:
            if Path(path).name.lower() != "chrome.exe":
                QtWidgets.QMessageBox.warning(self, "Invalid", "You must select the chrome.exe file")
                return
            self.ed_chrome.setText(path)

    def _pick_profiles(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select profiles folder",
            self.ed_profiles.text() or ""
        )
        if path:
            self.ed_profiles.setText(path)

    def values(self):
        return self.ed_chrome.text().strip(), self.ed_profiles.text().strip()

# =============================================================================
# UI: TABLE HEADER CHECKBOX
# =============================================================================

class CheckBoxHeader(QtWidgets.QHeaderView):
    """
    Header checkbox for QTableWidget column 0.
    Supports Checked / Unchecked (2-state).
    Click checkbox toggles select-all.
    """
    toggled = Signal(bool)

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self._state = Qt.Unchecked

        self._checkbox_rect = (0, 0, 0, 0)

    def setCheckState(self, state: Qt.CheckState):
        if self._state != state:
            self._state = state
            self.viewport().update()

    def paintSection(self, painter: QtGui.QPainter, rect: QtCore.QRect, logicalIndex: int):
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex != 0:
            return

        opt = QtWidgets.QStyleOptionButton()
        opt.state = QtWidgets.QStyle.State_Enabled
        if self._state == Qt.Checked:
            opt.state |= QtWidgets.QStyle.State_On
        else:
            opt.state |= QtWidgets.QStyle.State_Off

        w = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth)
        h = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorHeight)
        x = rect.x() + (rect.width() - w) // 2
        y = rect.y() + (rect.height() - h) // 2
        opt.rect = QtCore.QRect(x, y, w, h)
        self._checkbox_rect = (x, y, w, h)
        self.style().drawControl(QtWidgets.QStyle.CE_CheckBox, opt, painter)

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        pos = event.position().toPoint()

        idx = self.logicalIndexAt(pos)
        if idx == 0:
            px = pos.x()
            py = pos.y()
            x, y, w, h = self._checkbox_rect

            if x <= px <= x + w and y <= py <= y + h:

                new_checked = (self._state != Qt.Checked)
                self._state = Qt.Checked if new_checked else Qt.Unchecked

                self.toggled.emit(new_checked)
                self.viewport().update()
                event.accept()
                return

        super().mousePressEvent(event)

# =============================================================================
# UI: BULK OPERATIONS
# =============================================================================

class BulkRunner(QtCore.QObject):
    """
    Run bulk operations on UI thread in small chunks to keep UI responsive.
    Avoids thread-safety issues with ProfileManager internals.
    """
    progress = Signal(int, int, str)
    finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []
        self._total = 0
        self._done = 0
        self._running = False
        self._per_tick = 1
        self._fn = None
        self._summary = {}

    def start(self, rows, per_tick: int, fn, summary: dict):
        if self._running:
            return
        self._queue = list(rows)
        self._total = len(self._queue)
        self._done = 0
        self._per_tick = max(1, int(per_tick or 1))
        self._fn = fn
        self._summary = summary or {}
        self._running = True
        QtCore.QTimer.singleShot(0, self._tick)

    def _tick(self):
        if not self._running:
            return

        n = 0
        while n < self._per_tick and self._queue:
            row = self._queue.pop(0)
            try:
                msg = self._fn(row, self._summary) or ""
            except Exception as e:
                msg = f"Error: {e}"
                self._summary.setdefault("errors", []).append((getattr(row, "id", None), str(e)))
            self._done += 1
            n += 1
            self.progress.emit(self._done, self._total, msg)

        if self._queue:
            QtCore.QTimer.singleShot(0, self._tick)
        else:
            self._running = False
            self.finished.emit(self._summary)

# =============================================================================
# UI: MAIN WINDOW
# =============================================================================

class MainWindow(QMainWindow):
    sig_profile_exited = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Offline Browser Profile")
        self.resize(900, 520)

        self.storage = Storage()
        self.pm = ProfileManager(self.storage)
        self.pm.on_profile_exit = lambda pid: self.sig_profile_exited.emit(pid)

        try:
            self._badge_mgr = BrowserBadgeManager(self)
        except Exception:
            self._badge_mgr = None

        root = QWidget()
        layout = QVBoxLayout()

        top = QHBoxLayout()
        self.btn_create = QPushButton("+ Create Profile")
        self.btn_create.clicked.connect(self.on_create)
        top.addWidget(self.btn_create)
        top.addStretch(1)
        self.btn_settings = QPushButton("⚙ Settings")
        self.btn_settings.clicked.connect(self.on_settings)
        top.addWidget(self.btn_settings)
        layout.addLayout(top)

        self.selected_ids: set[str] = set()
        self._reloading_table = False
        self._table_rows: List[ProfileRow] = []
        self._row_index_by_id = {}
        self._row_widgets = {}

        bulk = QHBoxLayout()

        self.lbl_selected = QtWidgets.QLabel("Selected: 0/0")

        self.cmb_bulk_action = QComboBox()
        self.cmb_bulk_action.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        self.cmb_bulk_action.addItem("Delete selected")
        self.cmb_bulk_action.addItem("Start selected")
        self.cmb_bulk_action.addItem("Stop selected")
        self.cmb_bulk_action.addItem("Clean proxy selected")

        self.cmb_bulk_action.setCurrentIndex(0)

        self.btn_bulk_apply = QPushButton("Apply")
        self.btn_bulk_apply.setEnabled(False)

        self.cmb_bulk_action.currentIndexChanged.connect(self._on_bulk_action_changed)
        self.btn_bulk_apply.clicked.connect(self.on_bulk_apply)

        bulk.addWidget(self.lbl_selected)
        bulk.addStretch(1)
        bulk.addWidget(self.cmb_bulk_action)
        bulk.addWidget(self.btn_bulk_apply)

        layout.addLayout(bulk)

        self.table = QTableWidget(0, 6)

        self._bulk_busy = False
        self._pending_apply_widths = False

        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        hdr = CheckBoxHeader(Qt.Horizontal, self.table)
        self.table.setHorizontalHeader(hdr)
        hdr.toggled.connect(self._on_header_toggle)

        self.table.setHorizontalHeaderLabels(["", "Name", "OS", "Status", "Proxy", "Actions"])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        self.table.horizontalHeader().setStyleSheet("""
        QHeaderView::section { qproperty-alignment: AlignCenter; }
        """)

        layout.addWidget(self.table)

        sb = self.table.verticalScrollBar()
        sb.rangeChanged.connect(
            lambda _min, _max: (
                setattr(self, "_pending_apply_widths", True)
                if getattr(self, "_bulk_busy", False)
                else QtCore.QTimer.singleShot(0, self.apply_column_widths)
            )
        )

        root.setLayout(layout)
        self.setCentralWidget(root)

        self.reload_table()
        QTimer.singleShot(0, self.apply_column_widths)
        self.apply_column_widths()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_timer_refresh)
        self.timer.start(2000)

        self._init_tray()
        self.sig_profile_exited.connect(self._on_profile_exited)

    def _get_or_create_row_widgets(self, rid: str):
        """
        LV2 internal:
        Create row widgets/items once and cache them by profile_id.
        UI layout, texts, styles are IDENTICAL to lv1.
        """
        wmap = self._row_widgets.get(rid)
        if wmap is not None:

            try:
                cw = wmap.get("chk_w")
                aw = wmap.get("actions_w")
                cb = wmap.get("chk_cb")
                if (cw is not None and not shiboken6.isValid(cw)) or (aw is not None and not shiboken6.isValid(aw)) or (cb is not None and not shiboken6.isValid(cb)):
                    self._row_widgets.pop(rid, None)
                else:
                    return wmap
            except Exception:

                self._row_widgets.pop(rid, None)

        chk_w, chk_cb = self._make_center_checkbox_widget(
            rid=rid,
            checked=False,
            enabled=True
        )

        item_name = QTableWidgetItem("")
        item_name.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)

        item_os = QTableWidgetItem("")
        item_os.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)

        item_status = QTableWidgetItem("")
        item_status.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)

        item_proxy = QTableWidgetItem("")
        item_proxy.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)

        actions_w = QWidget()
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)

        btn_start = QPushButton("Start")
        btn_start.setFocusPolicy(Qt.NoFocus)
        btn_start.setAutoDefault(False)

        btn_stop = QPushButton("Stop")
        btn_stop.setFocusPolicy(Qt.NoFocus)
        btn_stop.setAutoDefault(False)

        btn_edit = QPushButton("Edit")
        btn_edit.setFocusPolicy(Qt.NoFocus)
        btn_edit.setAutoDefault(False)

        btn_del = QPushButton("Delete")
        btn_del.setFocusPolicy(Qt.NoFocus)
        btn_del.setAutoDefault(False)

        btn_start.clicked.connect(lambda _=None, rrid=rid: self._on_action_start(rrid))
        btn_stop.clicked.connect(lambda _=None, rrid=rid: self._on_action_stop(rrid))
        btn_edit.clicked.connect(lambda _=None, rrid=rid: self._on_action_edit(rrid))
        btn_del.clicked.connect(lambda _=None, rrid=rid: self._on_action_delete(rrid))

        h.addWidget(btn_start)
        h.addWidget(btn_stop)
        h.addWidget(btn_edit)
        h.addWidget(btn_del)

        actions_w.setLayout(h)

        wmap = {

            "chk_w": chk_w,
            "chk_cb": chk_cb,

            "item_name": item_name,
            "item_os": item_os,
            "item_status": item_status,
            "item_proxy": item_proxy,

            "actions_w": actions_w,
            "btn_start": btn_start,
            "btn_stop": btn_stop,
            "btn_edit": btn_edit,
            "btn_del": btn_del,
        }

        self._row_widgets[rid] = wmap
        return wmap

    def _on_action_start(self, rid: str):
        row = self._row_by_id.get(rid)
        if row:
            self.on_start(row)

    def _on_action_stop(self, rid: str):
        row = self._row_by_id.get(rid)
        if row:
            self.on_stop(row)

    def _on_action_edit(self, rid: str):
        row = self._row_by_id.get(rid)
        if row:
            self.on_edit(row)

    def _on_action_delete(self, rid: str):
        row = self._row_by_id.get(rid)
        if row:
            self.on_delete(row)

    def _refresh_rows_by_ids(self, ids) -> None:
        """LV2 incremental refresh: update only changed rows; fallback to full reload when needed."""
        try:
            if not ids:
                return

            try:
                idset = set(str(x) for x in ids)
            except Exception:
                idset = {str(ids)}

            rows = self.pm.list_profiles()
            self._table_rows = rows
            self._row_by_id = {str(getattr(r, 'id', '')): r for r in rows}

            for rid in idset:
                row_idx = self._row_index_by_id.get(rid)
                r = self._row_by_id.get(rid)
                if row_idx is None or r is None:

                    self.reload_table()
                    return

                it = self.table.item(row_idx, 1)
                if it is None:
                    it = QTableWidgetItem("")
                    it.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                    self.table.setItem(row_idx, 1, it)
                it.setText(r.name)

                it = self.table.item(row_idx, 2)
                if it is None:
                    it = QTableWidgetItem("")
                    it.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                    self.table.setItem(row_idx, 2, it)
                it.setText(r.os)

                it = self.table.item(row_idx, 3)
                if it is None:
                    it = QTableWidgetItem("")
                    it.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                    self.table.setItem(row_idx, 3, it)
                it.setText(r.status)
                if r.status == "running":
                    it.setBackground(Qt.darkGreen)
                    it.setForeground(Qt.white)
                else:
                    it.setBackground(Qt.transparent)
                    it.setForeground(Qt.white)

                proxy_text = getattr(r, "proxy_display", "") or ""
                it = self.table.item(row_idx, 4)
                if it is None:
                    it = QTableWidgetItem("")
                    it.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                    self.table.setItem(row_idx, 4, it)
                it.setText(proxy_text)

                wmap = self._get_or_create_row_widgets(rid)
                self.table.setCellWidget(row_idx, 0, wmap.get("chk_w"))
                self.table.setCellWidget(row_idx, 5, wmap.get("actions_w"))

                selectable = self._is_row_selectable(r)
                if not selectable:
                    self.selected_ids.discard(rid)
                checked = (rid in self.selected_ids) and selectable
                cb = wmap.get("chk_cb")
                if cb:
                    cb.blockSignals(True)
                    cb.setEnabled(bool(selectable))
                    cb.setChecked(bool(checked))
                    cb.blockSignals(False)

                btn_start = wmap.get("btn_start")
                btn_stop = wmap.get("btn_stop")
                btn_edit = wmap.get("btn_edit")
                btn_del = wmap.get("btn_del")
                if r.status == "running":
                    if btn_start: btn_start.setEnabled(False)
                    if btn_edit: btn_edit.setEnabled(False)
                    if btn_del: btn_del.setEnabled(False)
                    if btn_stop: btn_stop.setEnabled(True)
                else:
                    if btn_start: btn_start.setEnabled(True)
                    if btn_edit: btn_edit.setEnabled(True)
                    if btn_del: btn_del.setEnabled(True)
                    if btn_stop: btn_stop.setEnabled(False)

            self._update_selection_ui()
        except Exception:

            try:
                self.reload_table()
            except Exception:
                pass

    def pause_refresh(self):
        try:
            if hasattr(self, "timer") and self.timer.isActive():
                self.timer.stop()
        except Exception:
            pass

    def resume_refresh(self):
        try:
            if hasattr(self, "timer") and (not self.timer.isActive()):
                self.timer.start(2000)
        except Exception:
            pass

    def on_timer_refresh(self):

        try:
            if QApplication.mouseButtons() != Qt.NoButton:
                return
        except Exception:
            pass
        try:
            changed_ids = self.pm.sync_runtime_states()
            if changed_ids:
                self._refresh_rows_by_ids(changed_ids)
        except Exception as e:
            try:
                get_app_logger().exception(f"Timer refresh failed: {e}")
            except Exception:
                pass

    def _on_profile_exited(self, profile_id):
        try:
            self._refresh_rows_by_ids([str(profile_id)])
        except Exception:
            self.reload_table()

    def _is_row_selectable(self, row: ProfileRow) -> bool:
        """
        Rules for row selection under the current bulk action.
        - Stop selected (index 2): select RUNNING only
        - Otherwise: select NOT RUNNING only
        """
        idx = self.cmb_bulk_action.currentIndex()
        if idx == 2:
            return getattr(row, "status", "") == "running"
        return getattr(row, "status", "") != "running"

    def _on_bulk_action_changed(self):

        kept = set()
        row_by_id = getattr(self, "_row_by_id", {})
        for rid in list(self.selected_ids):
            row = row_by_id.get(rid)
            if row is not None and self._is_row_selectable(row):
                kept.add(rid)
        self.selected_ids = kept

        self.reload_table()

        self._update_selection_ui()

    def reload_table(self):

        try:
            self.pm.sync_runtime_states()
        except Exception as e:
            try:
                get_app_logger().exception(f"sync_runtime_states failed: {e}")
            except Exception:
                pass

        rows = self.pm.list_profiles()
        self._table_rows = rows
        self._row_by_id = {str(getattr(r, 'id', '')): r for r in rows}

        try:
            existing_ids = set(self._row_by_id.keys())
            self.selected_ids.intersection_update(existing_ids)
        except Exception:
            pass
        try:
            self._row_index_by_id.clear()
        except Exception:
            self._row_index_by_id = {}

        self._reloading_table = True
        try:

            self.table.setUpdatesEnabled(False)
            self.table.blockSignals(True)
            self.table.setRowCount(len(rows))

            for i, r in enumerate(rows):

                rid = str(r.id)
                self._row_index_by_id[rid] = i

                selectable = self._is_row_selectable(r)

                if not selectable:
                    self.selected_ids.discard(rid)

                checked = (rid in self.selected_ids) and selectable

                wmap = self._get_or_create_row_widgets(rid)
                cell_w = wmap.get("chk_w")
                cb = wmap.get("chk_cb")
                if cb:
                    try:
                        cb.blockSignals(True)
                        cb.setEnabled(bool(selectable))
                        cb.setChecked(bool(checked))
                        cb.blockSignals(False)
                    except Exception:
                        try:
                            cb.blockSignals(False)
                        except Exception:
                            pass

                self.table.setCellWidget(i, 0, cell_w)

                item_name = QTableWidgetItem(r.name)
                item_name.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                self.table.setItem(i, 1, item_name)

                item_os = QTableWidgetItem(r.os)
                item_os.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                self.table.setItem(i, 2, item_os)

                item_status = QTableWidgetItem(r.status)
                item_status.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                self.table.setItem(i, 3, item_status)

                status_item = self.table.item(i, 3)
                if status_item:
                    if r.status == "running":
                        status_item.setBackground(Qt.darkGreen)
                        status_item.setForeground(Qt.white)
                    else:
                        status_item.setBackground(Qt.transparent)
                        status_item.setForeground(Qt.white)

                proxy_text = getattr(r, "proxy_display", "") or ""
                item_proxy = QTableWidgetItem(proxy_text)

                item_proxy.setTextAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
                self.table.setItem(i, 4, item_proxy)

                wmap = self._get_or_create_row_widgets(rid)
                w = wmap.get("actions_w")

                btn_start = wmap.get("btn_start")
                btn_stop = wmap.get("btn_stop")
                btn_edit = wmap.get("btn_edit")
                btn_del = wmap.get("btn_del")

                if r.status == "running":
                    if btn_start: btn_start.setEnabled(False)
                    if btn_edit: btn_edit.setEnabled(False)
                    if btn_del: btn_del.setEnabled(False)
                    if btn_stop: btn_stop.setEnabled(True)
                else:
                    if btn_start: btn_start.setEnabled(True)
                    if btn_edit: btn_edit.setEnabled(True)
                    if btn_del: btn_del.setEnabled(True)
                    if btn_stop: btn_stop.setEnabled(False)

                self.table.setCellWidget(i, 5, w)

        finally:
            self.table.blockSignals(False)
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass

            try:
                self.table.viewport().update()
            except Exception:
                pass
            self._reloading_table = False

        self.apply_column_widths()
        self._update_selection_ui()

    def _make_center_checkbox_widget(self, rid: str, checked: bool, enabled: bool):
        """Create a centered checkbox widget for table cell (row select)."""
        cb = QCheckBox()
        cb.setStyleSheet("""
        QCheckBox {
            background-color: rgba(255,255,255,0.06);
        }
        """)

        cb.setFocusPolicy(Qt.NoFocus)
        cb.setChecked(bool(checked))
        cb.setEnabled(bool(enabled))
        cb.stateChanged.connect(lambda state, rrid=rid: self._on_row_checkbox_state_changed(rrid, state))

        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        lay.addWidget(cb)
        lay.addStretch(1)
        return w, cb

    def _set_row_checkbox(self, rid: str, checked: bool):
        """Set a row checkbox without emitting selection signals."""
        try:
            row_idx = getattr(self, "_row_index_by_id", {}).get(rid)
            if row_idx is None:
                return
            w = self.table.cellWidget(row_idx, 0)
            if not w:
                return
            cb = w.findChild(QCheckBox)
            if not cb:
                return
            cb.blockSignals(True)
            cb.setChecked(bool(checked))
            cb.blockSignals(False)
        except Exception:
            pass

    def _on_row_checkbox_state_changed(self, rid: str, state: int):
        if self._reloading_table:
            return

        row = getattr(self, "_row_by_id", {}).get(rid)
        if row is not None and (not self._is_row_selectable(row)):
            self.selected_ids.discard(rid)
            self._reloading_table = True
            try:
                self._set_row_checkbox(rid, False)
            finally:
                self._reloading_table = False
            self._update_selection_ui()
            return

        checked = False
        try:

            if state == Qt.Checked or state == Qt.CheckState.Checked:
                checked = True
            else:

                sv = getattr(state, "value", state)
                cv = getattr(Qt.Checked, "value", 2)
                try:
                    checked = (int(sv) == int(cv))
                except Exception:
                    checked = False
        except Exception:
            checked = False

        if checked:
            self.selected_ids.add(rid)
        else:
            self.selected_ids.discard(rid)

        self._update_selection_ui()

    def _selected_rows(self) -> List[ProfileRow]:
        return [r for r in self._table_rows if str(r.id) in self.selected_ids]

    def _update_selection_ui(self):
        n = len(self.selected_ids)
        total = len(self._table_rows)
        self.lbl_selected.setText(f"Selected: {n}/{total}")

        self.btn_bulk_apply.setEnabled(n >= 2)

        selection_active = (n > 0)
        try:
            for r in (self._table_rows or []):
                rid = str(getattr(r, 'id', ''))
                wmap = self._row_widgets.get(rid)
                if not wmap:
                    continue

                btn_start = wmap.get("btn_start")
                btn_stop = wmap.get("btn_stop")
                btn_edit = wmap.get("btn_edit")
                btn_del = wmap.get("btn_del")

                if selection_active:
                    for b in (btn_start, btn_stop, btn_edit, btn_del):
                        try:
                            if b is not None and shiboken6.isValid(b):
                                b.setEnabled(False)
                        except Exception:
                            pass
                else:

                    st = getattr(r, "status", "")
                    if st == "running":
                        if btn_start is not None and shiboken6.isValid(btn_start): btn_start.setEnabled(False)
                        if btn_edit is not None and shiboken6.isValid(btn_edit): btn_edit.setEnabled(False)
                        if btn_del is not None and shiboken6.isValid(btn_del): btn_del.setEnabled(False)
                        if btn_stop is not None and shiboken6.isValid(btn_stop): btn_stop.setEnabled(True)
                    else:
                        if btn_start is not None and shiboken6.isValid(btn_start): btn_start.setEnabled(True)
                        if btn_edit is not None and shiboken6.isValid(btn_edit): btn_edit.setEnabled(True)
                        if btn_del is not None and shiboken6.isValid(btn_del): btn_del.setEnabled(True)
                        if btn_stop is not None and shiboken6.isValid(btn_stop): btn_stop.setEnabled(False)
        except Exception:
            pass

        try:
            hdr = self.table.horizontalHeader()
            if isinstance(hdr, CheckBoxHeader):
                total_selectable = sum(1 for r in self._table_rows if self._is_row_selectable(r))
                if total_selectable == 0 or n == 0:
                    hdr.setCheckState(Qt.Unchecked)
                elif n >= total_selectable:

                    hdr.setCheckState(Qt.Checked)
                else:
                    hdr.setCheckState(Qt.Unchecked)
        except Exception:
            pass

    def _on_header_toggle(self, checked: bool):

        if self._reloading_table:
            return

        self._reloading_table = True
        try:
            if checked:

                self.selected_ids = {str(r.id) for r in self._table_rows if self._is_row_selectable(r)}
            else:
                self.selected_ids.clear()

            self.table.setUpdatesEnabled(False)
            try:
                for r in self._table_rows:

                    if not self._is_row_selectable(r):
                        continue

                    rid = str(r.id)
                    self._set_row_checkbox(rid, (rid in self.selected_ids))
            finally:
                self.table.setUpdatesEnabled(True)
        finally:
            self._reloading_table = False

        self._update_selection_ui()

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._reloading_table:
            return
        if item.column() != 0:
            return
        rid = item.data(Qt.UserRole)
        if not rid:
            return

        row = getattr(self, "_row_by_id", {}).get(rid)
        if row is not None and getattr(row, "status", "") == "running":
            self.selected_ids.discard(rid)
            self._reloading_table = True
            try:
                item.setCheckState(Qt.Unchecked)
            finally:
                self._reloading_table = False
            self._update_selection_ui()
            return

        if item.checkState() == Qt.Checked:
            self.selected_ids.add(rid)
        else:
            self.selected_ids.discard(rid)

        self._update_selection_ui()

    def _ensure_bulk_runner(self):
        if not hasattr(self, "_bulk_runner") or self._bulk_runner is None:
            self._bulk_runner = BulkRunner(self)
            self._bulk_runner.progress.connect(self._on_bulk_progress)
            self._bulk_runner.finished.connect(self._on_bulk_finished)

    def _on_bulk_progress(self, done: int, total: int, msg: str):

        try:
            base = "Offline Browser Profile"
            self.setWindowTitle(f"{base}  |  Wating... {done}/{total}")
        except Exception:
            pass

    def _on_bulk_finished(self, summary: dict):

        try:
            self.table.setEnabled(True)
        except Exception:
            pass
        try:
            self.btn_bulk_apply.setEnabled(True)
        except Exception:
            pass

        try:
            self.setWindowTitle("GoLoginOffline (MVP - Profiles)")
        except Exception:
            pass

        action = summary.get("action")
        done = int(summary.get("done", 0) or 0)

        if action == "delete" and done > 0:
            try:
                self.selected_ids.clear()
            except Exception:
                pass
            try:
                self._row_widgets.clear()
            except Exception:
                pass
            try:
                self._row_index_by_id.clear()
            except Exception:
                pass

        if action in ("clean_proxy", "start", "stop") and done > 0:
            self.selected_ids.clear()

        self.reload_table()

        self._bulk_busy = False
        if getattr(self, "_pending_apply_widths", False):
            self._pending_apply_widths = False
            try:
                self.apply_column_widths()
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass

        self.resume_refresh()

        try:
            errors = summary.get("errors", []) or []
            act = action

            def _show_msg():
                try:
                    if errors:
                        QMessageBox.warning(self, "Warning", "Error.")
                    else:

                        if act in ("delete", "clean_proxy", "stop"):
                            QMessageBox.information(self, "Notification", "Completed.")
                except Exception:
                    pass

            try:

                self.table.viewport().update()
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass

            QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, _show_msg))
        except Exception:
            pass

    def _bulk_rows_guard(self) -> List[ProfileRow]:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Notification", "No selected profiles.")
            return []
        return rows

    def on_bulk_apply(self):
        """
        Apply bulk action for currently selected profiles.
        IMPORTANT: Do NOT reset cmb_bulk_action after applying.
        """

        idx = self.cmb_bulk_action.currentIndex()

        if idx == 0:
            self.on_bulk_delete()
        elif idx == 1:
            self.on_bulk_start()
        elif idx == 2:
            self.on_bulk_stop()
        elif idx == 3:
            self.on_bulk_clean_proxy()

    def on_bulk_start(self):
        rows = self._bulk_rows_guard()
        if not rows:
            return

        count = len(rows)
        ok = QMessageBox.question(
            self,
            "Confirm action",
            f"Starting {count} profiles at once may use significant CPU/RAM and could cause lag or crashes. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            self.table.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_bulk_apply.setEnabled(False)
        except Exception:
            pass

        self._bulk_busy = True
        self.pause_refresh()
        self._ensure_bulk_runner()

        def _fn(row: ProfileRow, summary: dict):
            if row.status == "running":
                summary.setdefault("skipped", []).append((row.id, "already running"))
                return f"skip {row.id}"
            try:
                pid = self.pm.start_profile(row)
                try:
                    if getattr(self, "_badge_mgr", None) is not None:
                        self._badge_mgr.attach(pid, row.name or row.folder or "")
                except Exception:
                    pass
                summary["done"] = summary.get("done", 0) + 1
                return f"start {row.id}"
            except Exception as e:
                summary.setdefault("errors", []).append((row.id, str(e)))
                return f"error {row.id}"

        summary = {"action": "start", "done": 0, "total": len(rows)}
        self._bulk_runner.start(rows, per_tick=1, fn=_fn, summary=summary)

    def on_bulk_stop(self):
        rows = self._bulk_rows_guard()
        if not rows:
            return

        count = len(rows)
        ok = QMessageBox.question(
            self,
            "Confirm action",
            f"This will force-stop all running profiles in the selection. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            self.table.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_bulk_apply.setEnabled(False)
        except Exception:
            pass
        self._bulk_busy = True

        self.pause_refresh()
        self._ensure_bulk_runner()

        def _fn(row: ProfileRow, summary: dict):
            if row.status != "running":
                summary.setdefault("skipped", []).append((row.id, "not running"))
                return f"skip {row.id}"
            try:
                self.pm.stop_profile(row)
                summary["done"] = summary.get("done", 0) + 1
                return f"stop {row.id}"
            except Exception as e:
                summary.setdefault("errors", []).append((row.id, str(e)))
                return f"error {row.id}"

        summary = {"action": "stop", "done": 0, "total": len(rows)}
        self._bulk_runner.start(rows, per_tick=1, fn=_fn, summary=summary)

    def on_bulk_clean_proxy(self):
        rows = self._bulk_rows_guard()
        if not rows:
            return

        count = len(rows)
        ok = QMessageBox.question(
            self,
            "Confirm action",
            f"This will remove all saved proxy settings from {count} selected profiles. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            self.table.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_bulk_apply.setEnabled(False)
        except Exception:
            pass
        self._bulk_busy = True

        self.pause_refresh()
        self._ensure_bulk_runner()

        def _fn(row: ProfileRow, summary: dict):
            try:
                pdir = self.storage.profile_root / row.folder
                prof_path = pdir / "profile.json"
                prof = load_json(prof_path, {})
                prof["proxy"] = {
                    "enabled": False,
                    "type": "http",
                    "host": "",
                    "port": None,
                    "username": "",
                    "password": "",
                }
                prof["updatedAt"] = now_iso()
                save_json(prof_path, prof)

                try:
                    ps = self.pm._read_index_profiles()
                    for p in ps:
                        if str(p.get("id")) == str(row.id):
                            p["proxyDisplay"] = ""
                            break
                    self.pm._write_index(ps)
                except Exception:
                    pass

                summary["done"] = summary.get("done", 0) + 1
                return f"clean proxy {row.id}"
            except Exception as e:
                summary.setdefault("errors", []).append((row.id, str(e)))
                return f"error {row.id}"

        summary = {"action": "clean_proxy", "done": 0, "total": len(rows)}
        self._bulk_runner.start(rows, per_tick=10, fn=_fn, summary=summary)

    def on_bulk_delete(self):
        rows = self._bulk_rows_guard()
        if not rows:
            return

        count = len(rows)
        ok = QMessageBox.question(
            self,
            "Confirm delete",
            f"Are you sure you want to delete {count} profiles?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            self.table.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_bulk_apply.setEnabled(False)
        except Exception:
            pass
        self._bulk_busy = True

        self.pause_refresh()
        self._ensure_bulk_runner()

        def _fn(row: ProfileRow, summary: dict):
            if row.status == "running":
                summary.setdefault("skipped", []).append((row.id, "running"))
                return f"skip {row.id}"
            try:
                self.pm.delete_profile(row)

                self.selected_ids.discard(str(row.id))
                summary["done"] = summary.get("done", 0) + 1
                return f"delete {row.id}"
            except Exception as e:
                summary.setdefault("errors", []).append((row.id, str(e)))
                return f"error {row.id}"

        summary = {"action": "delete", "done": 0, "total": len(rows)}
        self._bulk_runner.start(rows, per_tick=3, fn=_fn, summary=summary)

    def apply_column_widths(self):

        total = self.table.viewport().width()

        sel_w = 36
        actions_min = 360

        actions_w = max(actions_min, int(total * 0.33))
        remaining = max(0, total - sel_w - actions_w)

        ratios = [0.18, 0.12, 0.14, 0.56]
        widths = [int(remaining * r) for r in ratios]
        diff = remaining - sum(widths)
        if diff != 0:
            widths[-1] += diff

        self.table.setColumnWidth(0, sel_w)
        self.table.setColumnWidth(1, widths[0])
        self.table.setColumnWidth(2, widths[1])
        self.table.setColumnWidth(3, widths[2])
        self.table.setColumnWidth(4, widths[3])
        self.table.setColumnWidth(5, actions_w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_column_widths()

    def on_create(self):
        dlg = ProfileEditorDialog(
            self,
            "Create Profile",
            "",
            "windows",
            "",
            {},
            {"mode": "altered"},
            is_create=True
        )

        if dlg.exec() != QDialog.Accepted:
            return

        vals = dlg.values()
        if len(vals) < 4:
            QMessageBox.warning(self, "Error", "Invalid dialog data.")
            return

        name, os_name, notes, proxy = vals[0], vals[1], vals[2], vals[3]
        webrtc = vals[4] if len(vals) >= 5 else {"mode": "altered"}

        qty = 1
        if hasattr(dlg, "qty_combo") and dlg.qty_combo is not None:
            try:
                qty = int(dlg.qty_combo.currentText())
            except Exception:
                qty = 1
        if qty < 1:
            qty = 1

        created = 0

        base_title = self.windowTitle()

        if qty > 1:
            try:
                self.table.setEnabled(False)
            except Exception:
                pass
            try:
                self.btn_create.setEnabled(False)
            except Exception:
                pass
            try:
                self.btn_bulk_apply.setEnabled(False)
            except Exception:
                pass

        try:

            if qty == 1:
                if not name:
                    QMessageBox.warning(self, "Invalid", "Name is required.")
                    return

                row_created = self.pm.create_profile(name, os_name)
                self.pm.update_profile(row_created, name, os_name, notes, proxy, webrtc)
                created = 1

            else:
                stamp = time.strftime("%Y%m%d_%H%M%S")

                for i in range(1, qty + 1):
                    rnd = uuid.uuid4().hex[:6].upper()
                    pname = f"Profile_{stamp}_{i:03d}_{rnd}"

                    row_created = self.pm.create_profile(pname, os_name)
                    self.pm.update_profile(row_created, pname, os_name, notes, proxy, webrtc)
                    created += 1

                    if (i % 1) == 0 or i == qty:
                        try:
                            self.setWindowTitle(f"{base_title}  |  Creating... {i}/{qty}")
                        except Exception:
                            pass
                        try:

                            QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
                        except Exception:
                            pass

        finally:

            if qty > 1:
                try:
                    self.table.setEnabled(True)
                except Exception:
                    pass
                try:
                    self.btn_create.setEnabled(True)
                except Exception:
                    pass
                try:
                    self.btn_bulk_apply.setEnabled(True)
                except Exception:
                    pass

            try:
                self.setWindowTitle(base_title)
            except Exception:
                pass

        get_app_logger().info(f"[ui] Created {created} profile(s)")

        self.reload_table()

    def on_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        chrome_path, profiles_dir = dlg.values()

        cfg = load_json(CONFIG_PATH, {})
        cfg.setdefault("browser", {})
        cfg.setdefault("storage", {})
        cfg["browser"]["binaryPath"] = chrome_path or r".\chromium\chrome.exe"
        cfg["storage"]["profileRootPath"] = profiles_dir or r".\profiles"
        save_json(CONFIG_PATH, cfg)

        rel_or_abs = cfg["storage"]["profileRootPath"]
        p = Path(rel_or_abs)
        if p.is_absolute():
            self.storage.profile_root = p
        else:
            self.storage.profile_root = (APP_DIR / rel_or_abs).resolve()

        self.storage.ensure()

        self.reload_table()

    def on_edit(self, row: ProfileRow):
        if not row:
            return
        pdir = self.pm.s.profile_root / row.folder
        prof_path = pdir / "profile.json"
        prof = load_json(prof_path, {})

        notes = (prof.get("notes") or "")
        proxy = (prof.get("proxy") or {})
        webrtc = (prof.get("webrtc") or {"mode": "altered"})

        dlg = ProfileEditorDialog(
            self,
            "Edit Profile",
            row.name,
            row.os,
            notes,
            proxy,
            webrtc,
            is_create=False
        )

        if dlg.exec() != QDialog.Accepted:
            return

        vals = dlg.values()
        if len(vals) < 4:
            QMessageBox.warning(self, "Error", "Invalid dialog data.")
            return

        name, os_name, notes, proxy = vals[0], vals[1], vals[2], vals[3]
        webrtc = vals[4] if len(vals) >= 5 else {"mode": "altered"}

        if not name:
            QMessageBox.warning(self, "Invalid", "Name is required.")
            return

        self.pm.update_profile(row, name, os_name, notes, proxy, webrtc)

        get_app_logger().info(f"[ui] Edited profile id={row.id} folder={row.folder}")
        self._refresh_rows_by_ids([row.id])

    def on_delete(self, row: ProfileRow):
        if not row:
            return
        if row.status == "running":
            QMessageBox.warning(self, "Not allowed", "Stop the profile before deleting.")
            return

        ok = QMessageBox.question(self, "Delete", f"Delete profile '{row.name}'?")
        if ok != QMessageBox.Yes:
            return

        rid = str(getattr(row, "id", ""))
        try:
            self.pm.delete_profile(row)
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))
            return

        if rid:
            try:
                self.selected_ids.discard(rid)
            except Exception:
                pass
            try:
                self._row_widgets.pop(rid, None)
            except Exception:
                pass

        try:
            self._row_widgets.clear()
        except Exception:
            pass
        try:
            self._row_index_by_id.clear()
        except Exception:
            pass

        try:
            self.reload_table()
        except RuntimeError as e:

            try:
                self._row_widgets.clear()
            except Exception:
                pass
            try:
                self._row_index_by_id.clear()
            except Exception:
                pass
            try:
                self.reload_table()
            except Exception:
                try:
                    get_app_logger().exception(f"reload_table after delete failed: {e}")
                except Exception:
                    pass

    def on_start(self, row: ProfileRow):
        if not row:
            return
        try:
            pid = self.pm.start_profile(row)

            try:
                if getattr(self, "_badge_mgr", None) is not None:
                    self._badge_mgr.attach(pid, row.name or row.folder or "")
            except Exception:
                pass

            self._refresh_rows_by_ids([row.id])
        except Exception as e:
            QMessageBox.critical(self, "Start failed", str(e))

    def on_stop(self, row: ProfileRow):
        if not row:
            return
        try:
            self.pm.stop_profile(row)
            self._refresh_rows_by_ids([row.id])
        except Exception as e:
            QMessageBox.critical(self, "Stop failed", str(e))

    def _init_tray(self):

        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return

        self._tray = QtWidgets.QSystemTrayIcon(self)

        try:
            icon = self.windowIcon()
            if icon is None or icon.isNull():
                icon = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
            self._tray.setIcon(icon)
        except Exception:
            try:
                self._tray.setIcon(QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))
            except Exception:
                pass

        menu = QtWidgets.QMenu()

        act_open = menu.addAction("Open")
        act_open.triggered.connect(self._tray_open)

        act_settings = menu.addAction("Settings")
        act_settings.triggered.connect(self.on_settings)

        menu.addSeparator()

        act_exit = menu.addAction("Exit")
        act_exit.triggered.connect(self._tray_exit_requested)

        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)

        self._tray.show()

    def _on_tray_activated(self, reason):
        try:
            if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
                self._tray_open()
        except Exception:
            pass

    def _tray_open(self):
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            try:
                self.show()
            except Exception:
                pass

    def _tray_exit_requested(self):

        try:
            self.pm.sync_runtime_states()
        except Exception:
            pass

        running = [r for r in (self._table_rows or []) if getattr(r, "status", "") == "running"]
        if running:
            ok = QMessageBox.question(
                self,
                "Confirm exit",
                f"There are {len(running)} running profile(s). Exiting will stop them. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if ok != QMessageBox.Yes:
                return

            for r in running:
                try:
                    self.pm.stop_profile(r)
                except Exception:
                    pass

        try:
            if getattr(self, "_badge_mgr", None) is not None:
                self._badge_mgr.shutdown()
        except Exception:
            pass

        try:
            if getattr(self, "pm", None) is not None and getattr(self.pm, "bridge", None) is not None:
                self.pm.bridge.stop_all()
        except Exception:
            pass

        QApplication.quit()

    def closeEvent(self, event: QtGui.QCloseEvent):

        if getattr(self, "_tray", None) is not None:
            try:
                event.ignore()
                self.hide()
                return
            except Exception:
                pass

        try:
            super().closeEvent(event)
        except Exception:
            try:
                event.accept()
            except Exception:
                pass

# =============================================================================
# ENTRYPOINT
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()