"""Small, deterministic user-boundary rules for desktop proactive check-ins."""
import re
import datetime
import hashlib
import json
import os
import tempfile
import threading
import time

PAUSE_PATTERNS = (
    r"\b(?:pause|hold|stop)\s+(?:the\s+)?(?:proactive\s+)?(?:check.?ins?|nudges?|interruptions?|reminders?)\b",
    r"\b(?:pause|hold)\s+for\b",
    r"\b(?:pause|hold)\s+until\b",
    r"\b(?:pause|hold)\s+(?:\d{1,4}|a|an|one|half)\b",
    r"\b(?:pause|hold)\s*[.!?]?\s*$",
    r"\b(?:don't|do not) interrupt me(?:\s+for\b|\s*$)",
    r"\b(?:don['’]?t|do not) disturb me\b",
    r"\b(?:leave me alone|give me some space)\b",
    r"\b(?:i said )?don't ask me (?:now|yet)\b",
)
BUSY_PATTERNS = (
    r"\s*(?:busy|focused|concentrating)(?:\s+(?:right now|for now|yeah|yes|though|right))?\s*[.!?]?\s*$",
    r"\bdon['’]?t ask me (?:now|yet)\b",
    r"\bi (?:can['’]?t|cannot) (?:talk|chat) (?:right now|now)\b",
    r"\bi(?:'m| am) (?:still )?(?:watching|busy|in a meeting)\b",
    r"\bi(?:'m| am) (?:still )?(?:focused|concentrating)(?:\s+(?:right now|at the moment|for now)|\s+on (?:this|that|it|my work|work|studying|the video))?(?:\s+(?:yeah|yes|though|right))?\s*[.!?]?\s*$",
    r"\b(?:focused|concentrating) right now\b",
    r"\bstill watching\b",
    r"\bi(?:'ll| will) (?:tell|let you know) (?:you )?later\b",
    r"\bi(?:'ll| will) tell you when i(?:'m| am) done\b",
    r"\blet me (?:watch|finish|focus|concentrate)\b",
    r"\bleave me to it\b",
    r"\bwait (?:until|till) i(?:'m| am) done\b",
    r"\bnot now\b",
    r"\b(?:no|nope|nah|skip|later|maybe later|not at the moment|not free|not available)\b",
)
RESUME_PATTERNS = (
    r"\b(?:finished|done) watching\b",
    r"\bi(?:'m| am) done (?:watching|now)\b",
    r"\bi finished (?:watching|the video|that)\b",
    r"\byou can ask me now\b",
    r"\bnow you can ask\b",
    r"\bi(?:'m| am) back\b",
    r"\bready to talk\b",
    r"\blet(?:'s| us) talk\b",
)
CLOSE_TASK_PATTERNS = (
    r"\bi quit (?:that|the project|working on (?:that|it|the project))\b",
    r"\bi(?:'m| am) not working on (?:that|it|the project) anymore\b",
    r"\bi will not work on (?:that|it|the project)\b",
    r"\bi won['’]?t work on (?:that|it|the project)\b",
    r"\bdropped (?:that|the project)\b",
    r"\babandoned (?:that|the project)\b",
)

def classify_user_boundary(text: str) -> str:
    """Classify an explicit pause request, a busy reply, resume, or closed task.

    Busy/context statements do not themselves pause check-ins. The UI asks before
    applying any pause and requires a duration.
    """
    value = (text or "").lower().replace("’", "'")
    if any(re.search(p, value, re.I) for p in PAUSE_PATTERNS):
        return "pause"
    if any(re.search(p, value, re.I) for p in RESUME_PATTERNS):
        return "resume"
    if any(re.search(p, value, re.I) for p in BUSY_PATTERNS):
        return "busy"
    if any(re.search(p, value, re.I) for p in CLOSE_TASK_PATTERNS):
        return "close_task"
    return "none"


_PAUSE_DURATION_RE = re.compile(
    r"(?i)\b(?:(?P<half>half\s+an?\s+hour)|"
    r"(?P<amount>\d{1,4}|a|an|one)\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d))\b"
)


def parse_pause_duration(text: str, *, allow_bare=False):
    """Return (seconds, label) for a bounded, explicit pause duration.

    When ``allow_bare`` is true, a short duration answer such as ``30 minutes``
    is accepted after the UI has asked how long. Otherwise a pause command must
    be present in the text.
    """
    value = " ".join(str(text or "").strip().lower().replace("’", "'").split())
    match = _PAUSE_DURATION_RE.search(value)
    if not match:
        return None
    if not allow_bare:
        prefix = value[:match.start()]
        command = re.search(
            r"\b(?:pause|hold|stop|interrupt|disturb|leave me alone|give me some space)\b",
            value, re.I)
        if not command:
            return None
    else:
        candidate = value
        candidate = re.sub(r"^[\s,;:.-]*(?:yes|okay|ok|sure|please)?[\s,;:.-]*", "", candidate, flags=re.I)
        candidate = re.sub(r"^(?:pause|hold)\s+(?:for\s+)?", "", candidate, flags=re.I)
        candidate = re.sub(r"^for\s+", "", candidate, flags=re.I)
        candidate = re.sub(r"\s+(?:please|pause)[\s.!?]*$", "", candidate, flags=re.I)
        if not re.fullmatch(
                r"(?:half\s+an?\s+hour|\d{1,4}\s*(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)|(?:a|an|one)\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?))",
                candidate, re.I):
            return None
    if match.group("half"):
        amount, factor, singular = 30, 60, "minute"
    else:
        amount_text = match.group("amount").lower()
        unit = match.group("unit").lower()
        amount = 1 if amount_text in ("a", "an", "one") else int(amount_text)
        factor = (1 if unit.startswith(("second", "sec")) or unit == "s" else
                  60 if unit.startswith(("minute", "min")) or unit == "m" else
                  3600 if unit.startswith(("hour", "hr")) or unit == "h" else 86400)
        singular = ("second" if factor == 1 else "minute" if factor == 60 else
                    "hour" if factor == 3600 else "day")
    seconds = min(max(1, amount * factor), 7 * 86400)
    if seconds == 7 * 86400 and amount * factor > seconds:
        label = "7 days (the maximum pause)"
    else:
        display_amount = seconds // factor if seconds % factor == 0 else amount
        label = f"{display_amount} {singular}{'' if display_amount == 1 else 's'}"
    return seconds, label


class ProactivePauseFlow:
    """Ephemeral pause state; any new user turn or app restart clears it."""
    def __init__(self):
        self.paused_until = 0.0
        self.awaiting_duration = False
        self.awaiting_until = 0.0

    def expire_pending(self, now):
        if self.awaiting_duration and float(now) >= float(self.awaiting_until):
            self.awaiting_duration = False
            self.awaiting_until = 0.0
            return True
        return False

    def is_paused(self, now):
        return float(now) < float(self.paused_until)

    def user_turn(self, text, *, now, pause_request=False, busy_reply=False):
        """Handle one user turn and report visible response guidance.

        Returns a dict with optional ``reply`` (handled without the LLM),
        ``notice`` (append after normal chat), and ``direct`` flag.
        """
        now = float(now)
        notices = []
        if self.expire_pending(now):
            notices.append("Your pause question expired; check-ins remain active.")
        if self.is_paused(now):
            self.paused_until = 0.0
            notices.append("Check-ins are active again because you messaged me.")
        if self.paused_until and now >= self.paused_until:
            self.paused_until = 0.0
        if self.awaiting_duration:
            duration = parse_pause_duration(text, allow_bare=True)
            if duration:
                seconds, label = duration
                self.awaiting_duration = False
                self.awaiting_until = 0.0
                self.paused_until = now + seconds
                return {"direct": True, "reply": (
                    f"Okay, proactive check-ins are paused for {label}. "
                    "Any new message from you or restarting the app will resume them early."
                )}
            if re.fullmatch(
                    r"\s*(?:i(?:'m| am)\s+)?(?:focused|concentrating|busy)\s*[.!]?\s*",
                    str(text or ""), re.I):
                self.awaiting_duration = False
                self.awaiting_until = 0.0
                self.paused_until = float("inf")
                return {"direct": True, "reply": (
                    "Okay—I’ll pause proactive check-ins until you message me again or restart Study Buddy."
                )}
            if re.fullmatch(r"\s*(?:no|no pause|don't pause|do not pause|keep them active|keep check-ins active)\s*[.!]?\s*", str(text or ""), re.I):
                self.awaiting_duration = False
                self.awaiting_until = 0.0
                return {"direct": True, "reply": "Okay—no pause. Proactive check-ins remain active."}
            self.awaiting_duration = False
            self.awaiting_until = 0.0
            notices.append("No pause was started; proactive check-ins remain active.")
        if pause_request:
            duration = parse_pause_duration(text, allow_bare=False)
            if duration:
                seconds, label = duration
                self.paused_until = now + seconds
                return {"direct": True, "reply": (
                    f"Okay, proactive check-ins are paused for {label}. "
                    "Any new message from you or restarting the app will resume them early."
                )}
            # A plain pause command is actionable on its own. Keep it paused
            # until the user returns to chat or restarts the app; explicit
            # durations remain available when the user includes one.
            self.paused_until = float("inf")
            return {"direct": True, "reply": (
                "Okay—I’ll pause proactive check-ins until you message me again or restart Study Buddy."
            )}
        if busy_reply:
            return {"direct": True, "reply": (
                "Got it—I’ll leave you to focus."
            )}
        return {"direct": False, "notice": "\n\n".join(notices) if notices else None}


def clear_legacy_automatic_pause_state(memory, personal_companion):
    """Clear obsolete automatic mutes; preserve explicit trigger Settings choices."""
    with memory._db_lock:
        keys = [str(row[0]) for row in memory.db.execute(
            "SELECT key FROM companion_settings WHERE key='initiative_pause_until' "
            "OR key LIKE 'trigger_mute_until_%'").fetchall()]
        title_halving = memory.db.execute(
            "SELECT 1 FROM companion_settings WHERE key='trigger_halved_until_title_clue' LIMIT 1"
        ).fetchone()
        disabled = [str(row[0]) for row in memory.db.execute(
            "SELECT key FROM companion_settings WHERE key LIKE 'trigger_enabled_%' "
            "AND lower(value) IN ('0','false','off','no')").fetchall()]
    for key in keys:
        personal_companion.set_setting(key, "0")
    if title_halving:
        # Older builds treated two unanswered window prompts as disinterest and
        # suppressed title curiosity for a week. Restore the user's enabled choice.
        personal_companion.set_setting("trigger_halved_until_title_clue", "0")
    restored = []
    for key in disabled:
        trigger = key[len("trigger_enabled_"):]
        with memory._db_lock:
            explicit = memory.db.execute(
                "SELECT granted FROM consent_ledger WHERE scope=? ORDER BY ts DESC LIMIT 1",
                ("initiative:" + trigger,)).fetchone()
        # Old negative-feedback code disabled a trigger without an audit row.
        # A user's Settings toggle always writes a ledger row; preserve that choice.
        if not explicit or bool(explicit[0]):
            personal_companion.set_setting(key, "1")
            restored.append(key)
    return {"cleared_automatic_mutes": len(keys), "cleared_automatic_title_halving": int(bool(title_halving)),
            "restored_automatic_disables": len(restored)}

def mentions_closed_task(text: str) -> bool:
    value = (text or "").lower().replace("’", "'")
    return any(re.search(p, value, re.I) for p in CLOSE_TASK_PATTERNS)


class ProactiveRateLimiter:
    """Persistent anti-spam gate for desktop check-ins.

    Only hashes of app/category keys and message text are stored; raw window titles
    and generated messages are deliberately not written to this state file.
    """
    def __init__(self, state_path=None, *, global_cooldown_seconds=0,
                 same_app_cooldown_seconds=0, attempt_cooldown_seconds=0,
                 daily_limit=3, clock=None):
        self.state_path = state_path
        self.global_cooldown_seconds = max(0, int(global_cooldown_seconds))
        self.same_app_cooldown_seconds = max(self.global_cooldown_seconds, int(same_app_cooldown_seconds))
        self.attempt_cooldown_seconds = max(0, int(attempt_cooldown_seconds))
        self.daily_limit = max(0, int(daily_limit))
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self.state = {
            "day": self._day(self.clock()), "daily_attempts": 0,
            "last_attempt_at": 0.0, "last_sent_at": 0.0,
            "last_sent_by_app": {}, "recent_message_hashes": []
        }
        self._load()
        self._roll_day()

    @classmethod
    def _day(cls, now):
        return datetime.datetime.fromtimestamp(float(now)).date().isoformat()

    @classmethod
    def _digest(cls, value):
        return hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()[:24]

    @classmethod
    def _app_key(cls, app_name, category):
        return cls._digest((str(app_name or "unknown").casefold().strip() + "|" +
                            str(category or "Other").casefold().strip()))

    @classmethod
    def _message_key(cls, text):
        import re
        normalized = re.sub(r"\s+", " ", str(text or "").casefold()).strip(" .!?\t\r\n")
        return cls._digest(normalized)

    def _load(self):
        if not self.state_path:
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as stream:
                stored = json.load(stream)
            if not isinstance(stored, dict):
                return
            if isinstance(stored.get("day"), str):
                self.state["day"] = stored["day"]
            if isinstance(stored.get("daily_attempts"), int):
                self.state["daily_attempts"] = max(0, stored["daily_attempts"])
            for key in ("last_attempt_at", "last_sent_at"):
                try: self.state[key] = max(0.0, float(stored.get(key, 0.0)))
                except (TypeError, ValueError): pass
            if isinstance(stored.get("last_sent_by_app"), dict):
                self.state["last_sent_by_app"] = {
                    str(k): max(0.0, float(v)) for k, v in stored["last_sent_by_app"].items()
                    if isinstance(k, str) and isinstance(v, (int, float))
                }
            if isinstance(stored.get("recent_message_hashes"), list):
                self.state["recent_message_hashes"] = [
                    {"hash": item["hash"], "ts": float(item["ts"])}
                    for item in stored["recent_message_hashes"][-20:]
                    if isinstance(item, dict) and isinstance(item.get("hash"), str)
                    and isinstance(item.get("ts"), (int, float))
                ]
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        if not self.state_path:
            return
        folder = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(folder, exist_ok=True)
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix="proactive_", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.state, stream, ensure_ascii=True, indent=2)
            os.replace(temp_path, self.state_path)
        except OSError:
            if temp_path:
                try: os.remove(temp_path)
                except OSError: pass

    def _roll_day(self):
        today = self._day(self.clock())
        if self.state.get("day") != today:
            self.state["day"] = today
            self.state["daily_attempts"] = 0
            self._save()

    def refresh_day(self):
        """Roll the shared limiter's daily counter without requiring a title-clue attempt."""
        with self._lock:
            self._roll_day()
            return int(self.state.get("daily_attempts", 0))

    def clear_history(self):
        """Clear hashed nudge history and cooldown state at the student's request."""
        with self._lock:
            self.state={"day":self._day(self.clock()),"daily_attempts":0,"last_attempt_at":0.0,
                        "last_sent_at":0.0,"last_sent_by_app":{},"recent_message_hashes":[]}
            self._save()

    def can_attempt(self, app_name, category):
        with self._lock:
            now = float(self.clock())
            self._roll_day()
            if int(self.state.get("daily_attempts", 0)) >= self.daily_limit:
                return False
            if now - float(self.state.get("last_attempt_at", 0.0)) < self.attempt_cooldown_seconds:
                return False
            if now - float(self.state.get("last_sent_at", 0.0)) < self.global_cooldown_seconds:
                return False
            sent = self.state.get("last_sent_by_app", {})
            app_ts = float(sent.get(self._app_key(app_name, category), 0.0))
            if now - app_ts < self.same_app_cooldown_seconds:
                return False
            return True

    def record_attempt(self, app_name, category):
        with self._lock:
            self._roll_day()
            self.state["last_attempt_at"] = float(self.clock())
            self.state["daily_attempts"] = int(self.state.get("daily_attempts", 0)) + 1
            self._save()

    def is_duplicate(self, text, window_seconds=86400):
        key = self._message_key(text)
        now = float(self.clock())
        return any(item.get("hash") == key and now - float(item.get("ts", 0)) < window_seconds
                   for item in self.state.get("recent_message_hashes", []))

    def record_sent(self, app_name, category, text):
        with self._lock:
            now = float(self.clock())
            key = self._app_key(app_name, category)
            self.state["last_sent_at"] = now
            sent = self.state.setdefault("last_sent_by_app", {})
            sent[key] = now
            hashes = self.state.setdefault("recent_message_hashes", [])
            hashes.append({"hash": self._message_key(text), "ts": now})
            self.state["recent_message_hashes"] = hashes[-20:]
            self._save()
