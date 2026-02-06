# =============================================================================
# IMPORTS
# =============================================================================

import sys
import json
import os
import threading
import re
import traceback
import shutil
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtGui import QIcon, QColor, QDesktopServices, QAction
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QDialog,
                               QFileDialog, QMessageBox, QSystemTrayIcon, QMenu)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from core import (
    Storage, ProfileManager, ProfileRow,
    CONFIG_PATH, load_json, save_json,
    SettingsDialog, ProfileEditorDialog,
    proxy_check_live,
)

# =============================================================================
# WINDOWS INTEGRATION HELPERS
# =============================================================================

def _try_enable_dark_titlebar(win: QWidget):
    """
    [Unverified] Depending on the Windows build, the title bar may not change.
    If it doesn't work, the app will still run normally.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(win.winId()))
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)

        DWMWA_USE_IMMERSIVE_DARK_MODE_19 = 19
        DWMWA_USE_IMMERSIVE_DARK_MODE_20 = 20

        val = ctypes.c_int(1)
        for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE_20, DWMWA_USE_IMMERSIVE_DARK_MODE_19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass

def _set_windows_appusermodel_id(app_id: str):

    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

# =============================================================================
# WEBCHANNEL BACKEND
# =============================================================================

class Backend(QObject):
    profilesChanged = Signal(str)
    toast = Signal(str)
    proxyChecked = Signal(str, str)

    def __init__(self, host_window: QWidget, parent=None):
        super().__init__(parent)
        self._host_window = host_window

        self.storage = Storage()
        self.pm = ProfileManager(self.storage)

        self._checking_ids = set()

        self._runtime_error_ids = set()

        self.pm.on_profile_exit = lambda pid: self._on_profile_exit(str(pid))

        self._force_reset_all_to_ready()

    # =============================================================================
    # PROFILE STATE NORMALIZATION
    # =============================================================================

    def _force_reset_all_to_ready(self):
        """
        Cleans up index.json and profile.json.
        Automatically removes malformed entries (avoids errors like "'str' object has no attribute 'get'").
        """

        try:
            index_path = self.storage.profile_root / "index.json"
            if index_path.exists():
                data = load_json(index_path, [])
                if not isinstance(data, list): data = []

                valid_data = []
                is_changed = False

                for item in data:

                    if isinstance(item, dict):

                        if item.get("status") != "stopped":
                            item["status"] = "stopped"
                            is_changed = True
                        valid_data.append(item)
                    else:

                        is_changed = True

                if is_changed:
                    save_json(index_path, valid_data)
        except Exception as e:
            print(f"Index cleanup error: {e}")

        try:

            if hasattr(self.pm, "_read_index_profiles"):
                 self.pm._profiles = self.pm._read_index_profiles()

            rows = self.pm.list_profiles()
            for r in rows:
                r.status = "stopped"

                try:
                    pdir = self.storage.profile_root / r.folder
                    prof_path = pdir / "profile.json"
                    if prof_path.exists():
                        prof = load_json(prof_path, {})
                        rt = prof.get("runtime", {})
                        if rt.get("status") != "stopped":
                            rt["status"] = "stopped"
                            rt["pid"] = None
                            rt["lastError"] = None
                            rt["bridge"] = {"enabled": False}
                            prof["runtime"] = rt
                            save_json(prof_path, prof)
                except: pass
        except Exception: pass

    def _rows_to_json(self, rows: List[ProfileRow]) -> str:
        out = []
        for r in rows:
            notes = ""
            try:
                pdir = self.storage.profile_root / r.folder
                prof_path = pdir / "profile.json"
                pj = load_json(prof_path, {})
                if isinstance(pj, dict):
                    notes = (pj.get("notes") or "")
            except Exception:
                notes = ""

            current_status = r.status

            if r.id in self._checking_ids:
                current_status = "checking"

            elif r.id in self._runtime_error_ids:
                current_status = "conn-error"

            out.append({
                "id": r.id,
                "folder": r.folder,
                "name": r.name,
                "os": r.os,
                "status": current_status,
                "proxy": r.proxy_display,
                "notes": notes,
            })
        return json.dumps(out, ensure_ascii=False)

    def _emit_profiles(self):
        try:
            rows = self.pm.list_profiles()
            self.profilesChanged.emit(self._rows_to_json(rows))
        except Exception:

            self.toast.emit("Failed to load profiles.")

    def _find_row(self, profile_id: str) -> Optional[ProfileRow]:
        try:
            for r in self.pm.list_profiles():
                if r.id == profile_id: return r
        except: pass
        return None

    def _on_profile_exit(self, profile_id: str):

        if profile_id in self._runtime_error_ids:
            self._runtime_error_ids.discard(profile_id)
        self._emit_profiles()

    # =============================================================================
    # PROFILES API
    # =============================================================================

    @Slot()
    def refresh(self):
        threading.Thread(target=self._emit_profiles, daemon=True).start()

    @Slot(str, str)
    def setNote(self, profile_id: str, note: str):
        profile_id = (profile_id or "").strip()
        if not profile_id: return
        note = "" if note is None else str(note)
        def _run():
            try:
                row = self._find_row(profile_id)
                if not row: return
                pdir = self.storage.profile_root / row.folder
                prof_path = pdir / "profile.json"
                if not prof_path.exists(): return
                prof = load_json(prof_path, {})
                prof["notes"] = note
                tmp = prof_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(prof_path)
                self.toast.emit("Note saved.")
                self._emit_profiles()
            except Exception as e:
                self.toast.emit(f"Failed to save note: {e}")
        threading.Thread(target=_run, daemon=True).start()

    @Slot(str, str, int, str, str, str)
    def createProfileFull(self, name_prefix: str, os_name: str, quantity: int, notes: str, proxy_json: str, webrtc_json: str):
        name_prefix = (name_prefix or "").strip()
        os_name = (os_name or "windows").strip().lower()
        quantity = max(1, int(quantity))
        try: proxy_dict = json.loads(proxy_json or "{}")
        except: proxy_dict = {}
        try: webrtc_dict = json.loads(webrtc_json or "{}")
        except: webrtc_dict = {"mode":"altered"}

        def _run():
            try:
                created_count = 0
                import re
                match = re.search(r'(\d+)$', name_prefix)
                if match:
                    number_part = match.group(1)
                    start_idx = int(number_part)
                    base_name = name_prefix[:match.start()]
                else:
                    base_name = name_prefix + " " if name_prefix else "New Profile "
                    start_idx = 1

                if quantity == 1:
                     row = self.pm.create_profile(name_prefix, os_name)
                     self.pm.update_profile(row, name_prefix, os_name, notes, proxy_dict, webrtc_dict)
                     created_count = 1
                else:
                    for i in range(quantity):
                        current_idx = start_idx + i
                        if not base_name.strip() and start_idx == int(name_prefix):
                             final_name = str(current_idx)
                        else:
                             final_name = f"{base_name}{current_idx}".strip()
                        row = self.pm.create_profile(final_name, os_name)
                        self.pm.update_profile(row, final_name, os_name, notes, proxy_dict, webrtc_dict)
                        created_count += 1
                self.toast.emit(f"Created {created_count} profile(s).")
                self._emit_profiles()
            except Exception as e:
                self.toast.emit(f"Failed to create profile: {e}")
        threading.Thread(target=_run, daemon=True).start()

    @Slot(str)
    def startProfile(self, profile_id: str):
        profile_id = (profile_id or "").strip()
        if not profile_id: return

        def _run():
            try:
                row = self._find_row(profile_id)
                if not row:
                    self.toast.emit("Profile not found.")
                    return

                if row.status == "running":
                    self.toast.emit("Profile is already running.")
                    return

                self._runtime_error_ids.discard(profile_id)

                self._checking_ids.add(profile_id)
                self._emit_profiles()

                try:
                    self.pm.start_profile(row)
                except Exception as e:

                    print(f"Start Exception: {e}")
                    self._runtime_error_ids.add(profile_id)

                finally:

                    self._checking_ids.discard(profile_id)

                self._emit_profiles()

            except Exception as e:

                print(f"Start fail: {e}")
                self._runtime_error_ids.add(profile_id)
                self._checking_ids.discard(profile_id)
                self._emit_profiles()

        threading.Thread(target=_run, daemon=True).start()

    @Slot(str)
    def stopProfile(self, profile_id: str):
        profile_id = (profile_id or "").strip()
        if not profile_id: return
        def _run():
            try:
                row = self._find_row(profile_id)
                if not row: return
                self.pm.stop_profile(row)
                self._emit_profiles()
            except Exception as e:
                self.toast.emit(f"Failed to stop: {e}")
        threading.Thread(target=_run, daemon=True).start()

    @Slot(str)
    def viewProfile(self, profile_id: str):
        row = self._find_row((profile_id or "").strip())
        if not row: return
        try:
            pdir = (self.storage.profile_root / row.folder)
            prof = load_json(pdir / "profile.json", {})
            runtime = prof.get("runtime") if isinstance(prof, dict) else None
            pid = runtime.get("pid") if isinstance(runtime, dict) else None
            if not pid:
                self.toast.emit("Profile is not running.")
                return
            pid = int(pid)
        except Exception: return

        if not sys.platform.startswith("win"):
            self.toast.emit("View is currently supported on Windows only.")
            return

        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd_found = wintypes.HWND(0)
            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, lParam):
                nonlocal hwnd_found
                try:
                    if not user32.IsWindowVisible(hwnd): return True
                    GW_OWNER = 4
                    if user32.GetWindow(hwnd, GW_OWNER): return True
                    pid_ = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_))
                    if pid_.value != pid: return True
                    cls = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cls, 256)
                    if "Chrome_WidgetWin" not in cls.value:
                        if user32.GetWindowTextLengthW(hwnd) == 0: return True
                    hwnd_found = hwnd
                    return False
                except Exception: return True
            user32.EnumWindows(EnumWindowsProc(_cb), 0)
            if not hwnd_found:
                self.toast.emit("Chrome window not found.")
                return
            if user32.IsIconic(hwnd_found):
                SW_RESTORE = 9
                user32.ShowWindow(hwnd_found, SW_RESTORE)
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None)
            tid_hwnd = user32.GetWindowThreadProcessId(hwnd_found, None)
            tid_me = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(tid_me, tid_fg, True)
            user32.AttachThreadInput(tid_me, tid_hwnd, True)
            user32.SetForegroundWindow(hwnd_found)
            user32.BringWindowToTop(hwnd_found)
            user32.SetFocus(hwnd_found)
            user32.AttachThreadInput(tid_me, tid_hwnd, False)
            user32.AttachThreadInput(tid_me, tid_fg, False)
        except Exception as e:
            self.toast.emit(f"Failed to focus Chrome window: {e}")

    @Slot(str, result=str)
    def getProfileDetail(self, profile_id: str) -> str:
        row = self._find_row((profile_id or "").strip())
        if not row: return "{}"
        try:
            pdir = self.storage.profile_root / row.folder
            prof_path = pdir / "profile.json"
            prof = load_json(prof_path, {}) if prof_path.exists() else {}
            out = {
                "id": row.id,
                "folder": row.folder,
                "name": (prof.get("name") or row.name or ""),
                "os": (prof.get("os") or row.os or "windows"),
                "notes": (prof.get("notes") or ""),
                "proxy": (prof.get("proxy") or {}),
                "webrtc": (prof.get("webrtc") or {"mode": "altered"}),
            }
            return json.dumps(out, ensure_ascii=False)
        except Exception: return "{}"

    @Slot(str, str, result=bool)
    def saveProfileDetail(self, profile_id: str, payload_json: str) -> bool:
        row = self._find_row((profile_id or "").strip())
        if not row:
            self.toast.emit("Profile does not exist.")
            return False
        try:
            data = json.loads(payload_json or "{}")
            name = str(data.get("name") or "").strip()
            os_name = str(data.get("os") or "windows").strip().lower()
            notes = str(data.get("notes") or "")
            if not name: return False
            proxy = data.get("proxy") if isinstance(data.get("proxy"), dict) else {}
            webrtc = data.get("webrtc") if isinstance(data.get("webrtc"), dict) else {}
            self.pm.update_profile(row, name, os_name, notes, proxy, webrtc)
            self.toast.emit("Profile saved.")
            self._emit_profiles()
            return True
        except Exception as e:
            self.toast.emit(f"Error: {e}")
            return False

    @Slot(str, str)
    def checkProxy(self, req_id: str, proxy_json: str):
        req_id = str(req_id or "").strip()
        if not req_id: return
        def _run():
            try:
                try: data = json.loads(proxy_json or "{}")
                except: data = {}
                ok, ms, msg = proxy_check_live(data, timeout=20.0)
                payload = json.dumps({"ok": bool(ok), "ms": float(ms), "msg": str(msg)}, ensure_ascii=False)
            except Exception as e:
                payload = json.dumps({"ok": False, "ms": 0.0, "msg": f"Exception: {e}"}, ensure_ascii=False)
            try: self.proxyChecked.emit(req_id, payload)
            except: pass
        threading.Thread(target=_run, daemon=True).start()

    # =============================================================================
    # PROXY UTILITIES
    # =============================================================================

    @Slot(str, result=str)
    def parseProxyString(self, text: str) -> str:
        """
        Smart parser API: accepts messy input from JS and returns clean JSON.
        Supports: socks5://, s5://, user:pass@host, and extra whitespace...
        """
        try:

            text = (text or "").strip()

            text = "".join(ch for ch in text if ch.isprintable())
            text = re.sub(r"\s+", "", text)

            if not text: return "{}"

            scheme = "http"
            host, port, user, pwd = "", "", "", ""
            body = text

            match = re.match(r"^([a-zA-Z0-9\.\-]+)://(.*)$", text)
            if match:
                raw_scheme = match.group(1).lower()
                body = match.group(2)

                if "5" in raw_scheme: scheme = "socks5"
                elif "4" in raw_scheme: scheme = "socks4"
                elif any(x in raw_scheme for x in ["tp", "ht", "web", "ssl"]): scheme = "http"
                elif any(x in raw_scheme for x in ["ck", "so", "ox"]): scheme = "socks5"

            if "@" in body:
                auth, loc = body.rsplit("@", 1)
                if ":" in auth: user, pwd = auth.split(":", 1)
                else: user = auth
                if ":" in loc: host, port = loc.split(":", 1)
                else: host = loc

            else:
                parts = body.split(":")
                if len(parts) >= 1: host = parts[0]
                if len(parts) >= 2: port = parts[1]
                if len(parts) == 4: user, pwd = parts[2], parts[3]
                elif len(parts) == 3: user = parts[2]

            port_digits = re.findall(r"\d+", str(port))
            final_port = port_digits[0] if port_digits else ""

            return json.dumps({
                "ok": True,
                "type": scheme,
                "host": host,
                "port": final_port,
                "username": user,
                "password": pwd
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    # =============================================================================
    # CLIPBOARD UTILITIES
    # =============================================================================

    @Slot(str)
    def clipboardCopy(self, text: str):
        QApplication.clipboard().setText(text or "")

    @Slot(result=str)
    def clipboardPaste(self) -> str:
        return QApplication.clipboard().text() or ""

    @Slot(str)
    def editProfile(self, profile_id: str):
        self.toast.emit("Please use the Edit button in the list.")

    @Slot(str)
    def deleteProfile(self, profile_id: str):
        row = self._find_row((profile_id or "").strip())
        if not row: return
        if row.status == "running":
            self.toast.emit("Stop the profile before deleting.")
            return
        def _run():
            try:
                self.pm.delete_profile(row)
                self.toast.emit("Profile deleted.")
                self._emit_profiles()
            except Exception as e:
                self.toast.emit(f"Failed to delete profile: {e}")
        threading.Thread(target=_run, daemon=True).start()

    @Slot(str)
    def deleteProfiles(self, json_ids: str):
        """Bulk delete safely to avoid index.json file contention errors"""
        try:
            ids = json.loads(json_ids or "[]")
        except: ids = []

        if not ids: return

        def _run():
            count = 0

            for pid in ids:
                row = self._find_row(pid)
                if not row: continue
                if row.status == "running": continue
                try:
                    self.pm.delete_profile(row)
                    count += 1
                except Exception as e:
                    print(f"Error deleting {pid}: {e}")

            if count > 0:
                self.toast.emit(f"Deleted {count} profile(s).")
                self._emit_profiles()

        threading.Thread(target=_run, daemon=True).start()

    # =============================================================================
    # SETTINGS API
    # =============================================================================

    @Slot(result=str)
    def getSettings(self) -> str:
        try:
            cfg = load_json(CONFIG_PATH, {})
            return json.dumps({
                "chromePath": str(cfg.get("browser", {}).get("binaryPath", r".\chromium\chrome.exe")),
                "profilesDir": str(cfg.get("storage", {}).get("profileRootPath", r".\profiles")),
            }, ensure_ascii=False)
        except: return "{}"

    def _reload_storage_pm(self):
        self.storage = Storage()
        self.pm = ProfileManager(self.storage)
        self.pm.on_profile_exit = lambda pid: self._on_profile_exit(str(pid))

    @Slot(str, str, result=bool)
    def saveSettings(self, chrome_path: str, profiles_dir: str) -> bool:
        try:
            cfg = load_json(CONFIG_PATH, {})
            cfg.setdefault("browser", {})
            cfg.setdefault("storage", {})
            cfg["browser"]["binaryPath"] = (chrome_path or "").strip() or r".\chromium\chrome.exe"
            cfg["storage"]["profileRootPath"] = (profiles_dir or "").strip() or r".\profiles"
            save_json(CONFIG_PATH, cfg)
            self._reload_storage_pm()
            self._emit_profiles()
            return True
        except Exception: return False

    @Slot(result=bool)
    def resetSettings(self) -> bool:
        return self.saveSettings(r".\chromium\chrome.exe", r".\profiles")

    @Slot(result=str)
    def browseChrome(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self._host_window, "Select chrome.exe", "", "Exe (*.exe)")
        return path or ""

    @Slot(result=str)
    def browseProfiles(self) -> str:
        return QFileDialog.getExistingDirectory(self._host_window, "Select profiles folder", "") or ""

    @Slot()
    def openSettings(self):
        try:
            dlg = SettingsDialog(self._host_window)
            dlg.exec()
            self._emit_profiles()
        except Exception: pass

    @Slot(str)
    def openLocal(self, path_str: str):
        try: QDesktopServices.openUrl(QUrl.fromLocalFile(path_str))
        except: pass

    # =============================================================================
    # EXIT CONFIRMATION
    # =============================================================================

    @Slot(bool)
    def confirmExit(self, confirmed: bool):
        if confirmed:

            self._host_window.perform_force_exit()
        else:

            pass

# =============================================================================
# WEBENGINE PAGE: EXTERNAL LINKS
# =============================================================================

class ExternalLinkPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, isMainFrame):

        try:
            scheme = (url.scheme() or "").lower()
            if scheme in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
                return False
        except Exception:
            pass
        return super().acceptNavigationRequest(url, nav_type, isMainFrame)

    def createWindow(self, _type):

        return ExternalLinkPage(self.profile(), self)

# =============================================================================
# MAIN WINDOW & SYSTEM TRAY
# =============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Offline Browser Profile")
        ico = Path(__file__).resolve().parent / "icon.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))

        self.view = QWebEngineView(self)

        self.page = ExternalLinkPage(self.view)
        self.view.setPage(self.page)

        self.setCentralWidget(self.view)

        self.view.setStyleSheet("background: #191a23;")
        try:
            self.page.setBackgroundColor(QColor("#191a23"))
        except Exception:
            pass

        self.backend = Backend(self, self)

        self.channel = QWebChannel(self.page)
        self.channel.registerObject("backend", self.backend)
        self.page.setWebChannel(self.channel)

        ui_dir = Path(__file__).resolve().parent / "webui"
        index = ui_dir / "index.html"
        self.view.load(QUrl.fromLocalFile(str(index)))
        self.setup_tray()

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return

        self.tray_icon = QSystemTrayIcon(self)

        icon = self.windowIcon()
        if icon.isNull():
            icon = QApplication.style().standardIcon(QApplication.style().SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()

        action_open = QAction("Open", self)
        action_open.triggered.connect(self.tray_open_window)
        tray_menu.addAction(action_open)

        action_settings = QAction("Settings", self)
        action_settings.triggered.connect(self.tray_open_settings)
        tray_menu.addAction(action_settings)

        action_about = QAction("About", self)
        action_about.triggered.connect(self.tray_open_about)
        tray_menu.addAction(action_about)

        tray_menu.addSeparator()

        action_exit = QAction("Exit", self)
        action_exit.triggered.connect(self.tray_exit_app)
        tray_menu.addAction(action_exit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def tray_open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.page.runJavaScript("if(typeof showProfilesView === 'function') showProfilesView();")

    def tray_open_settings(self):
        self.tray_open_window()

        self.page.runJavaScript("if(typeof openSettingsView === 'function') openSettingsView();")

    def tray_open_about(self):
        self.tray_open_window()

        self.page.runJavaScript("if(typeof openAboutView === 'function') openAboutView();")

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.tray_open_window()

    def tray_exit_app(self):

        try:
            self.backend.pm.sync_runtime_states()
        except: pass

        rows = self.backend.pm.list_profiles()
        running_profiles = [r for r in rows if r.status == "running"]
        count = len(running_profiles)

        if count > 0:
            self.tray_open_window()

            self.page.runJavaScript(f"if(typeof openExitDlg === 'function') openExitDlg({count});")
            return

        self.perform_force_exit()

    def perform_force_exit(self):
        """Exit the app immediately and stop all running profiles"""

        try:
            rows = self.backend.pm.list_profiles()
            running = [r for r in rows if r.status == "running"]
            for r in running:
                try: self.backend.pm.stop_profile(r)
                except: pass
        except: pass

        try:
            if hasattr(self.backend.pm, "bridge"):
                self.backend.pm.bridge.stop_all()
        except: pass

        QApplication.quit()

    def closeEvent(self, event):
        if hasattr(self, 'tray_icon') and self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            try:
                if hasattr(self.backend.pm, "bridge"): self.backend.pm.bridge.stop_all()
            except: pass
            event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        _try_enable_dark_titlebar(self)

# =============================================================================
# APPLICATION ENTRY POINT
# =============================================================================

def main():

    _set_windows_appusermodel_id("Offline Browser Profile")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    base = Path(__file__).resolve().parent
    ico = base / "icon.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))

    w = MainWindow()
    w.resize(1200, 720)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()