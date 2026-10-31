"""Privacy-first context signals for Nova's proactive companion engine.

This module deliberately reports only coarse foreground-app context by default.
It does not capture keystrokes, page contents, screenshots, or audio content.
"""
from dataclasses import dataclass, asdict
import os
import re
import time


_GENERIC_TITLE_WORDS = {
    "youtube", "instagram", "facebook", "tiktok", "twitter", "x", "spotify",
    "netflix", "google", "chrome", "edge", "microsoft", "firefox", "brave",
    "browser", "window", "tab", "app", "home", "feed", "website", "web",
    "music", "video", "playing", "watch", "watching", "media", "new",
    "profile", "explore", "search", "results", "official", "site", "untitled",
    "yandex", "opera", "visual", "studio", "code", "vscode", "microsoft365",
    "word", "document", "document1", "powerpoint", "excel", "settings", "blank",
    "page", "start", "welcome", "home", "inprivate", "incognito", "open",
    # Product/host labels alone do not tell us what the user is looking at.
    "chatgpt", "openai",
}

_NON_USER_FACING_PROCESSES = {
    "shellexperiencehost", "startmenuexperiencehost", "searchhost",
    "textinputhost", "sihost", "dwm", "lockapp",
}


def _unicode_words(text):
    """Split titles using Unicode letters so non-English video/file names count."""
    return re.findall(r"[^\W_]+", str(text or "").casefold(), flags=re.UNICODE)


def _title_clues(snapshot):
    title = str((snapshot or {}).get("window_title") or "")
    return {
        token.rstrip("0123456789")
        for token in _unicode_words(title)
        if len(token.rstrip("0123456789")) > 2
        and any(char.isalpha() for char in token)
        and token.rstrip("0123456789") not in _GENERIC_TITLE_WORDS
    }

@dataclass
class ContextSnapshot:
    app_category: str = "Other"
    app_name: str = "unknown"
    window_title: str = ""
    idle_seconds: float = 0.0
    captured_at: float = 0.0
    # Internal-only ownership signal used to recognize any Study Buddy-owned
    # window even if its visible title is customized. It is never serialized.
    process_id: int = 0

    def safe_summary(self):
        # Hide both title and proactive interpretation for likely private/auth/payment windows.
        title = self.window_title[:200].strip()
        sensitive = bool(re.search(
            r"password|sign.?in|log.?in|authentication|bank|payment|checkout|private|incognito|inprivate|"
            r"password manager|credit card|billing|medical|patient portal|tax return|one.?time code|verification code",
            title, re.I))
        if sensitive:
            title = "[title hidden for privacy]"
        return {
            "app_category": "Private" if sensitive else self.app_category,
            "app_name": self.app_name[:40],
            "window_title": title,
            "private_context": sensitive,
            "idle_seconds": max(0, int(self.idle_seconds)),
            "captured_at": self.captured_at or time.time(),
        }

class ContextEngine:
    """Collect low-cost desktop context; browser page content needs a separate opt-in bridge."""
    def __init__(self):
        self.last_snapshot = ContextSnapshot()
        # In-memory-only latest external window. This is not a conversation memory
        # store and is never used by unsolicited proactive generations.
        self.last_external_snapshot = None
        self.last_external_at = 0.0

    @staticmethod
    def is_studybuddy_window(snapshot):
        if snapshot is None:
            return False
        try:
            if int(getattr(snapshot, "process_id", 0) or 0) == os.getpid():
                return True
        except (TypeError, ValueError):
            pass
        process_name = str(getattr(snapshot, "app_name", "") or "").casefold()
        title = str(getattr(snapshot, "window_title", "") or "").casefold()
        return (any(token in process_name for token in ("python", "pythonw", "studybuddy", "study_buddy"))
                and "study buddy" in title) or title.strip() in {"study buddy", "study buddy — intelligence center", "study buddy - intelligence center"}

    @staticmethod
    def is_non_user_facing_system_window(snapshot):
        """Exclude Windows shell/overlay processes from unsolicited title guesses."""
        if snapshot is None:
            return False
        if isinstance(snapshot, dict):
            app_name = snapshot.get("app_name", "")
        else:
            app_name = getattr(snapshot, "app_name", "")
        process_name = os.path.splitext(str(app_name or "").casefold())[0]
        return process_name in _NON_USER_FACING_PROCESSES

    def last_external_summary(self, max_age_seconds=600):
        """Return a fresh, safe title snapshot from the last non-StudyBuddy window."""
        snap = self.last_external_snapshot
        if snap is None or not self.last_external_at:
            return None
        age = max(0.0, time.time() - self.last_external_at)
        if age > max(0, float(max_age_seconds)):
            return None
        safe = snap.safe_summary()
        if safe.get("private_context") or safe.get("app_category") in ("Private", "Meeting"):
            return None
        safe["observation_source"] = "last_external_window"
        safe["age_seconds"] = int(age)
        return safe

    @staticmethod
    def _categorize(process_name, title):
        p = (process_name or "").lower()
        t = (title or "").lower()
        if re.search(r"password|sign.?in|log.?in|authentication|bank|payment|checkout|private|incognito|inprivate|password manager|credit card|billing|medical|patient portal|tax return|one.?time code|verification code", t, re.I):
            return "Private"
        if any(x in p for x in ("teams", "zoom", "webex", "meet")) or any(x in t for x in ("meeting", "in a call", "webinar")):
            return "Meeting"
        if any(x in p for x in ("code", "pycharm", "devenv", "idea64", "sublime_text", "notepad++")):
            return "Coding"
        if any(x in p for x in ("chrome", "msedge", "firefox", "brave", "opera")):
            if any(x in t for x in ("chatgpt", "claude", "perplexity", "research", "scholar")):
                return "Browser research"
            if any(x in t for x in ("youtube", "netflix", "spotify", "music")):
                return "Media"
            return "Browser"
        if any(x in p for x in ("word", "powerpnt", "excel", "acrobat", "notepad")):
            return "Study/Docs"
        if any(x in p for x in ("vlc", "spotify", "wmplayer")):
            return "Media"
        return "Other"

    @staticmethod
    def capture(idle_seconds=0):
        process_name, title = "unknown", ""
        process_id = 0
        if os.name == "nt":
            try:
                import ctypes
                import psutil
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(min(max(length + 1, 1), 2048))
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, len(buf))
                title = buf.value
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                process_id = int(pid.value or 0)
                process_name = psutil.Process(process_id).name()
            except Exception:
                pass
        snap = ContextSnapshot(
            app_category=ContextEngine._categorize(process_name, title),
            app_name=os.path.splitext(process_name)[0][:40],
            window_title=title[:200], idle_seconds=float(idle_seconds or 0), captured_at=time.time(),
            process_id=process_id)
        return snap

    def observe(self, idle_seconds=0):
        self.last_snapshot = self.capture(idle_seconds)
        snap = self.last_snapshot
        safe = snap.safe_summary()
        # Cache only non-private, non-meeting, external application metadata. Keep it
        # in RAM, not SQLite; the proactive engine still uses the current snapshot only.
        if (not self.is_studybuddy_window(snap) and not safe.get("private_context")
                and safe.get("app_category") not in ("Private", "Meeting")
                and safe.get("window_title") and safe.get("window_title") != "[title hidden for privacy]"):
            self.last_external_snapshot = snap
            self.last_external_at = time.time()
        return self.last_snapshot


    @staticmethod
    def has_specific_title_clue(snapshot):
        """Return True only when the current title contains identifiable content.

        App names such as Instagram or YouTube alone are not enough to guess what
        the user is viewing; one non-generic title word is needed as a clue.
        """
        snapshot = snapshot or {}
        if snapshot.get("private_context") or snapshot.get("app_category") in ("Private", "Meeting"):
            return False
        title = str(snapshot.get("window_title") or "").strip()
        if not title or title == "[title hidden for privacy]":
            return False
        return bool(_title_clues(snapshot))

    @staticmethod
    def reply_is_grounded(reply, snapshot):
        """Reject proactive text that adds content not supported by the current title.

        This is a conservative lexical guard, not a semantic truth verifier. If the
        model adds unsupported nouns/adjectives, the safest result is no check-in.
        """
        snapshot = snapshot or {}
        title = str(snapshot.get("window_title") or "")
        app_name = str(snapshot.get("app_name") or "")
        evidence = set(_unicode_words(title + " " + app_name))
        title_clues = _title_clues(snapshot)
        reply_tokens = set(_unicode_words(reply))
        if not title_clues.intersection(reply_tokens):
            return False
        neutral = {
            "looks", "like", "you", "may", "might", "be", "seem", "seems", "seemed",
            "possibly", "perhaps", "probably", "currently", "are", "on", "the", "a",
            "an", "this", "that", "with", "at", "in", "through", "to", "from",
            "viewing", "watching", "listening", "reading", "browsing", "using",
            "working", "studying", "editing", "writing", "coding", "researching",
            "searching", "learning", "reviewing", "building", "practicing",
            "open", "opened", "active", "window", "tab", "app", "title", "current",
            "right", "now", "it", "may", "only", "clue", "suggests", "suggest",
            "suggesting", "mentions", "mention", "says", "saying", "named", "called",
            "appears", "appearing", "on", "is", "this", "that", "the", "a", "an",
            "hey", "hi", "i", "m", "re", "s", "was", "noticed", "curious", "free", "for", "quick", "chat",
            "busy", "focused", "focus", "leave", "guess", "guessed", "did", "what",
            "about", "whether", "or", "and", "tell", "me", "doing", "watch", "read",
            "edit", "write", "code", "research", "look", "looking", "at", "up", "there",
            "video", "movie", "clip", "document", "file", "page", "article", "site", "say", "something"
        }
        # Recognized app purpose supports only a broad activity guess. Keep these
        # words app-scoped so they cannot justify unrelated claims about content.
        app_context = (title + " " + app_name).casefold()
        if "google keep" in app_context:
            neutral.update({"check", "checking", "organize", "organizing", "note", "notes", "take", "taking", "create", "creating"})
        if "linkedin" in app_context:
            neutral.update({"scroll", "scrolling", "feed", "profile", "posts", "post", "networking"})
        for token in reply_tokens:
            if len(token) > 2 and token not in evidence and token not in neutral:
                return False
        return True

    def proactive_decision(self, snapshot, quiet=False, user_busy=False, last_interaction_age=0):
        """Conservatively gate check-ins before spending an LLM generation."""
        if quiet or user_busy or snapshot.idle_seconds >= 900:
            return {"act": False, "reason": "quiet_or_busy_or_long_idle"}
        if snapshot.app_category == "Meeting":
            return {"act": False, "reason": "meeting"}
        safe = snapshot.safe_summary()
        if snapshot.app_category == "Private" or safe.get("private_context"):
            return {"act": False, "reason": "private_context"}
        if not self.has_specific_title_clue(safe):
            return {"act": False, "reason": "insufficient_title_evidence"}
        if last_interaction_age < 120:
            return {"act": False, "reason": "recent_interaction"}
        return {"act": True, "reason": "context_allows_checkin", "context": safe}

