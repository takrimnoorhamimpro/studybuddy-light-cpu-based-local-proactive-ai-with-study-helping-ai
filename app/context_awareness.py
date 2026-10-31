"""Low-cost, local-only Windows activity signals for Study Buddy.

Reads only foreground process/app category, idle duration, and optional Windows
per-app audio-session names. It does not inspect window titles, browser tabs,
keystrokes, microphone input, or audio content. Private-window keywords are
checked before returning any app context.
"""
import os
import sys
import time

_PRIVATE_WORDS = ("bank", "password", "login", "incognito", "private", "payment", "wallet", "1password", "bitwarden")
_MEETING = ("zoom", "teams", "webex", "meet", "skype", "slack huddle")
_VIDEO = ("vlc", "media player", "netflix", "disney", "potplayer", "mpv")
_MUSIC = ("spotify", "foobar", "musicbee", "itunes", "tidal", "deezer", "music")
_CODING = ("code.exe", "pycharm", "devenv", "sublime_text", "notepad++", "cursor", "windsurf", "idea64")
_STUDY = ("word", "excel", "powerpoint", "acrobat", "onenote", "onenote", "notion", "obsidian", "sumatra")
_SOCIAL = ("discord", "whatsapp", "telegram", "messenger", "instagram")
_BROWSER = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "browser.exe")


def _category(app_name):
    s = (app_name or "").lower()
    if any(w in s for w in _PRIVATE_WORDS): return "Private (hidden)"
    if any(w in s for w in _MEETING): return "Meeting / call"
    if any(w in s for w in _VIDEO): return "Video / media"
    if any(w in s for w in _MUSIC): return "Music"
    if any(w in s for w in _CODING): return "Coding"
    if any(w in s for w in _BROWSER): return "Browser"
    if any(w in s for w in _STUDY): return "Study / documents"
    if any(w in s for w in _SOCIAL): return "Social / messaging"
    if s in ("explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "applicationframehost.exe"):
        return "Desktop"
    return "Other app" if s else "Unknown"


class WindowsContextMonitor:
    def __init__(self):
        self._last_app = None
        self._app_since = time.monotonic()

    @property
    def supported(self):
        return sys.platform == "win32"

    def _foreground_process(self):
        if not self.supported:
            return None
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil
            return os.path.basename(psutil.Process(pid.value).exe())
        except Exception:
            # Fallback to PID only is not useful for categories; do not guess.
            return None

    def _idle_seconds(self):
        if not self.supported: return None
        import ctypes
        from ctypes import Structure, c_uint
        class LASTINPUTINFO(Structure):
            _fields_ = [("cbSize", c_uint), ("dwTime", c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)): return None
        now = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0)

    def _audio_apps(self):
        """Return app process names with active render sessions; optional pycaw."""
        if not self.supported: return []
        try:
            from pycaw.pycaw import AudioUtilities
            found = []
            for session in AudioUtilities.GetAllSessions():
                if not session.Process: continue
                # State 1 = active on common Core Audio session APIs.
                state = getattr(session, "State", None)
                if state == 1:
                    found.append(os.path.basename(session.Process.exe()))
            return sorted(set(found))[:5]
        except Exception:
            return []

    def snapshot(self):
        app = self._foreground_process()
        category = _category(app)
        now = time.monotonic()
        if app != self._last_app:
            self._last_app, self._app_since = app, now
        private = category == "Private (hidden)"
        audio = [] if private else self._audio_apps()
        return {
            "supported": self.supported,
            "app_name": None if private else app,
            "category": category,
            "app_seconds": int(now - self._app_since),
            "idle_seconds": self._idle_seconds(),
            "audio_apps": audio,
            "audio_active": bool(audio),
            "meeting": category == "Meeting / call",
            "private": private,
        }

    @staticmethod
    def situation_line(s):
        if not s.get("supported"):
            return "Desktop awareness is unavailable on this operating system."
        if s.get("private"):
            return "The foreground app looks private; hide all app and audio details."
        bits = ["Foreground app category: " + s.get("category", "Unknown")]
        if s.get("app_name"): bits.append("app: " + s["app_name"])
        if s.get("audio_active"):
            bits.append("audio appears active in: " + ", ".join(s.get("audio_apps", [])))
        if s.get("idle_seconds") is not None: bits.append("user idle seconds: " + str(int(s["idle_seconds"])))
        bits.append("time in current app seconds: " + str(s.get("app_seconds", 0)))
        return "; ".join(bits)
