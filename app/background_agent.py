"""Lightweight per-user background agent for reminders and nightly lifecycle jobs.
Run at Windows sign-in via the user's Startup folder. No chat text is written to logs.
"""
import ctypes
import datetime
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from memory import Memory
from llm import OllamaClient, resolve_keep_alive
from lifecycle import LifecycleService


def idle_seconds():
    if os.name != "nt":
        return 0
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    tick = ctypes.windll.kernel32.GetTickCount()
    return max(0, (tick - info.dwTime) / 1000.0)


def desktop_is_running():
    """True while the desktop process owns its named mutex."""
    if os.name != "nt":
        return False
    handle = None
    try:
        handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\StudyBuddyDesktopApp")
        return bool(handle)
    except Exception:
        return False
    finally:
        if handle:
            try: ctypes.windll.kernel32.CloseHandle(handle)
            except Exception: pass


def notify(title, message):
    try:
        from winotify import Notification
        Notification(app_id="Study Buddy", title=title, msg=str(message)[:240]).show()
        return True
    except Exception:
        # Do not mark a reminder delivered if toast support is missing.
        return False


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    if os.name != "nt":
        return
    # Named Windows mutex: GUI launches the agent and the Startup entry may also launch it.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, r"Local\StudyBuddyBackgroundAgent")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    settings_path = os.path.join(ROOT, "config", "settings.json")
    settings = load_json(settings_path, {})
    if not settings.get("background_agent_enabled", True):
        return
    profile = settings.get("profile", "hamim")
    user_dir = os.path.join(ROOT, "users", profile)
    os.makedirs(user_dir, exist_ok=True)
    state_path = os.path.join(user_dir, "background_state.json")
    state = load_json(state_path, {})
    db = Memory(os.path.join(user_dir, "memory.db"))
    def _tuned_options(current):
        # Must match the GUI's options exactly, otherwise Ollama reloads the model on every switch.
        fallback = {"num_gpu": 0, "num_thread": 4, "num_ctx": 3072, "num_predict": 96, "temperature": 0.2}
        merged = dict(current, ollama_options=current.get("ollama_options", fallback))
        try:
            import hardware_profile
            return hardware_profile.resolve_options(merged, os.path.join(ROOT, "config"))[0]
        except Exception:
            return merged["ollama_options"]
    llm = OllamaClient(
        url=settings.get("ollama_url", "http://localhost:11434"),
        model=settings.get("model_chat", "llama3.2:3b"),
        options=_tuned_options(settings),
        timeout=240,
        keep_alive=resolve_keep_alive(settings),
        embedding_model=settings.get("model_embed", "nomic-embed-text"),
    )
    lifecycle = LifecycleService(ROOT, db, llm, profile=profile)
    try:
        while True:
            # Pick up changed preferences (including disabling the background agent) live.
            settings = load_json(settings_path, settings)
            if not settings.get("background_agent_enabled", True):
                break
            llm.keep_alive = resolve_keep_alive(settings)
            now = datetime.datetime.now()
            # Reminders are delivered independently of the desktop GUI. Claiming is atomic,
            # so the UI and agent cannot both deliver the same reminder.
            try:
                # When the desktop is alive, let its scheduler deliver reminders into
                # the chat. When it is closed, this agent owns delivery via Windows toast.
                if not desktop_is_running():
                    for reminder in db.claim_due_reminders(time.time()):
                        if not notify("Study Buddy reminder", reminder["text"]):
                            # Toast delivery unavailable: release for a later retry.
                            with db._db_lock:
                                db.db.execute("UPDATE reminders SET fired=0 WHERE id=?", (reminder["id"],))
                                db.db.commit()
                        else:
                            # Keep a durable record so the reminder is visible in chat history next launch.
                            db.add_turn("assistant", "Reminder delivered: " + reminder["text"])
            except Exception:
                pass

            # Optional late-night wind-down reminder, independent of the GUI.
            if settings.get("late_night_nudge_enabled", False):
                try:
                    hm = datetime.datetime.strptime(settings.get("late_night_nudge_time", "22:30"), "%H:%M").time()
                    target = now.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
                    if now < target:
                        target -= datetime.timedelta(days=1)
                    key = target.date().isoformat()
                    elapsed = now - target
                    if (datetime.timedelta(0) <= elapsed <= datetime.timedelta(hours=2)
                            and state.get("last_late_night_nudge_date") != key):
                        if notify("Study Buddy", "It's getting late. Want to wrap up gently and get some rest?"):
                            state["last_late_night_nudge_date"] = key
                            with open(state_path, "w", encoding="utf-8") as f:
                                json.dump(state, f, indent=2)
                except Exception:
                    pass

            # CPU-only nightly work is allowed only in the configured window and after 10 min idle.
            hour = now.hour
            try:
                work_start = datetime.datetime.strptime(settings.get("background_work_start", "02:00"), "%H:%M").time()
                work_end = datetime.datetime.strptime(settings.get("background_work_end", "05:00"), "%H:%M").time()
                in_window = work_start <= now.time() < work_end if work_start < work_end else (now.time() >= work_start or now.time() < work_end)
            except ValueError:
                in_window = 2 <= hour < 5
            today_key = now.date().isoformat()
            now_epoch = time.time()
            retry_due = now_epoch - float(state.get("last_lifecycle_attempt", 0)) >= 3600
            idle_required = int(settings.get("background_min_idle_seconds", 600))
            try:
                aged_backlog = bool(db.days_with_turns_before(now_epoch - 3 * 86400))
            except Exception:
                aged_backlog = False
            # Normal nightly window is retained, but a 3-day-old backlog may also be
            # consolidated at the next idle opportunity instead of waiting for another night.
            scheduled_run_due = in_window and state.get("last_lifecycle_day") != today_key
            aged_backlog_due = aged_backlog and retry_due
            if (idle_seconds() >= idle_required and (scheduled_run_due or aged_backlog_due)
                    and settings.get("memory_daily_consolidation_enabled", True) and retry_due):
                state["last_lifecycle_attempt"] = now_epoch
                try:
                    report = lifecycle.consolidate_daily(
                        today=now.date(),
                        limit_days=int(settings.get("memory_max_days_per_night", 3)),
                        older_than_days=3,
                    )
                    # Evolve only from a successfully summarized day, not from an unreviewed raw transcript.
                    yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
                    personality_period = report.get("days_summarized", [])[-1] if report.get("days_summarized") else yesterday
                    personality = (lifecycle.evolve_personality(personality_period) if settings.get("personality_evolution_enabled", True) else {"status": "disabled"})
                    if personality.get("status") != "skipped":
                        state["last_personality_day"] = personality_period
                    state["last_lifecycle_day"] = today_key if scheduled_run_due else state.get("last_lifecycle_day")
                    state["last_lifecycle_result"] = {
                        "daily_summaries": len(report.get("days_summarized", [])),
                        "turns_archived": report.get("turns_deleted", 0),
                        "personality_status": personality.get("status", "unknown"),
                    }
                    # Weekly roll-up is retried until all seven daily notes exist.
                    this_monday = now.date() - datetime.timedelta(days=now.date().weekday())
                    week_start = (this_monday - datetime.timedelta(days=7)).isoformat()
                    if (settings.get("memory_weekly_consolidation_enabled", True)
                            and state.get("last_weekly_period") != week_start):
                        weekly = lifecycle.consolidate_previous_week(today=now.date())
                        if weekly.get("created"):
                            state["last_weekly_period"] = week_start
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(state, f, indent=2)
                except Exception:
                    # Generic error only; never log conversation text.
                    state["last_lifecycle_error_date"] = today_key
                    try:
                        with open(state_path, "w", encoding="utf-8") as f:
                            json.dump(state, f, indent=2)
                    except Exception:
                        pass
            time.sleep(30)
    finally:
        db.close()


if __name__ == "__main__":
    main()
