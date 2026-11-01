"""Built-in, permission-aware tools for Milo. UI actions stay behind callbacks."""
from __future__ import annotations
from datetime import datetime
import sys
import time

from .tool_registry import ToolRegistry, ToolSpec
from safety import unsafe_reminder_content


def _obj(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


def build_builtin_tool_registry(core, app=None, capability_registry=None):
    registry = ToolRegistry()
    memory = core.memory

    def add(name, description, props, required, handler, capability="", category="General"):
        registry.register(ToolSpec(name, description, _obj(props, required), handler, capability, category))

    def capabilities():
        snapshot = capability_registry.snapshot() if capability_registry else []
        return {"capabilities": snapshot, "tools": registry.describe(),
                "limitations": ["No arbitrary Python or shell execution", "No raw screen-pixel or browser-page inspection",
                                "Camera frames are not sent to the language model"]}

    def profile():
        if memory is None:
            return {"available": False, "reason": "Memory database is not connected"}
        # This tool answers questions about the USER, not the assistant's identity.
        # Including buddy_name here made a question like "what do you know about me?"
        # misleadingly report the bot's name as a saved user profile field.
        keys = ("user_name",)
        return {key: memory.get_profile(key) for key in keys if memory.get_profile(key)}

    def search_memory(query="", scope="all", limit=12):
        if memory is None:
            return {"available": False, "reason": "Memory database is not connected"}
        query = (query or "").strip()
        limit = max(1, min(int(limit), 20))
        # A topicless "tell me about our previous chats" request should retrieve recent
        # turns, not run a literal keyword search for words like "previous" and "chats".
        generic_history = {
            "previous", "previously", "past", "earlier", "recent", "chat", "chats",
            "conversation", "conversations", "history", "before", "remember", "recall",
            "discussed", "said", "told", "what", "tell", "about", "our", "we", "us",
            "can", "you", "me", "my", "do", "have", "access", "memory",
        }
        query_words = {word.casefold().strip(".,?!:;()[]{}\"'") for word in query.split()}
        topic_words = {word for word in query_words if len(word) > 2 and word not in generic_history}
        history_words = query_words & generic_history
        if query and history_words and not topic_words:
            query = ""
            explicit_past_chat = bool(query_words & {
                "previous", "previously", "past", "earlier", "recent", "chat", "chats",
                "conversation", "conversations", "history", "before", "discussed", "said", "told",
            })
            if scope == "all" and explicit_past_chat:
                scope = "conversations"
        result = {"scope": scope, "query": query, "profile": profile(), "facts": [], "notes": [], "conversation_matches": []}
        terms = [w.casefold() for w in query.split() if len(w) > 2]
        if scope in ("all", "facts"):
            facts = memory.list_facts(30)
            result["facts"] = [f for f in facts if not terms or any(t in f.casefold() for t in terms)][:limit]
        if scope in ("all", "notes"):
            if query:
                notes = memory.search_notes(query, limit)
            else:
                notes = memory.list_notes(limit)
            result["notes"] = [{"period": n.get("period"), "level": n.get("level"), "text": n.get("text")}
                               for n in notes[:limit]]
        if scope in ("all", "conversations"):
            turns = memory.search_turns(query, limit=limit) if hasattr(memory, "search_turns") else memory.recent_turns(limit * 4)
            result["conversation_matches"] = [
                {"role": t.get("role"), "text": t.get("text", t.get("content", ""))[:700],
                 "time": datetime.fromtimestamp(t["ts"]).isoformat(timespec="minutes") if t.get("ts") else None}
                for t in turns[:limit]
            ]
        result["notice"] = "Only information present in this Study Buddy profile's local database is searchable; other apps' chats are not accessible."
        return result

    def save_memory(text, kind="fact"):
        if memory is None:
            return {"saved": False, "reason": "Memory database is not connected"}
        value = (text or "").strip()
        if not value:
            return {"saved": False, "reason": "The memory text is empty"}
        import re
        if re.search(r"\b(password|passcode|pin|recovery code|secret key|api key)\b", value, re.I):
            return {"saved": False, "reason": "Authentication secrets are not stored"}
        if kind == "fact":
            memory.add_fact(value, source="owner")
        else:
            memory.add_note("manual", datetime.now().strftime("%Y-%m-%d"), value)
        return {"saved": True, "kind": kind, "message": "Memory stored in the local database"}

    def add_task(title, details="", due_at=""):
        if memory is None:
            return {"created": False, "reason": "Memory database is unavailable"}
        title = (title or "").strip()
        if not title:
            return {"created": False, "reason": "Task title is required"}
        due_ts = None
        if due_at:
            due_ts = datetime.fromisoformat(due_at).timestamp()
        task_id = memory.add_task(title, details=details, due_ts=due_ts)
        task = next((t for t in memory.list_tasks(include_done=True) if int(t["id"]) == int(task_id)), None)
        core.last_created_task_id = int(task_id)
        core.last_task_snapshot = [task] if task else []
        return {"created": bool(task_id), "task": task}

    def list_tasks(include_completed=False, status="open"):
        if memory is None:
            return {"available": False, "tasks": []}
        # Structured state comes from SQLite, not conversation-text guessing.
        all_tasks = memory.list_tasks(include_done=True) if (include_completed or status in {"completed", "all"}) else memory.list_tasks()
        if status == "completed":
            tasks = [t for t in all_tasks if str(t.get("status", "")).casefold() in {"done", "completed"}]
            task_filter = "completed_only"
        elif status == "all" or include_completed:
            tasks = all_tasks
            task_filter = "open_and_completed"
        else:
            tasks = all_tasks
            task_filter = "open_only"
        core.last_task_snapshot = tasks
        if tasks:
            core.last_listed_task_ids = [int(t["id"]) for t in tasks]
        return {"count": len(tasks), "filter": task_filter,
                "tasks": [{"id": int(t["id"]), "title": t["title"], "details": t.get("details", ""),
                           "status": t.get("status"),
                           "due_at": datetime.fromtimestamp(t["due_ts"]).isoformat(timespec="minutes") if t.get("due_ts") else None}
                          for t in tasks]}

    def resolve_task(title):
        tasks = memory.list_tasks()
        # Ollama models frequently include a separator copied from the user's
        # phrasing (": title", "- title"). Normalize outer punctuation here so
        # a valid task is not lost because of harmless formatting.
        wanted = " ".join((title or "").strip(" \t\r\n:;,.—-=\"'").casefold().split())
        aliases = {
            "this", "that", "it", "this task", "that task", "the task", "last task", "the last one",
            "whatever in my tasks", "whatever is in my tasks", "anything in my tasks",
            "whatever is on my task list", "anything on my task list", "the only task",
        }
        if wanted in aliases or wanted in {
            "whatever in my task list", "whatever is in my task list", "anything in my task list",
        }:
            if wanted not in {"this", "that", "it", "this task", "that task", "the task", "last task", "the last one", "the only task"}:
                if len(tasks) == 1:
                    return tasks[0], []
                if len(tasks) > 1:
                    return None, tasks
                return None, []
            if len(core.last_task_snapshot) == 1:
                task_id = int(core.last_task_snapshot[0]["id"])
                return next((t for t in tasks if int(t["id"]) == task_id), None), []
            if len(core.last_task_snapshot) > 1:
                ids = {int(t["id"]) for t in core.last_task_snapshot}
                candidates = [t for t in tasks if int(t["id"]) in ids]
                return None, candidates
            if getattr(core, "last_created_task_id", None):
                return next((t for t in tasks if int(t["id"]) == core.last_created_task_id), None), []
            return None, []
        def norm_title(value):
            return " ".join(str(value or "").strip(" \t\r\n:;,.—-=\"'").casefold().split())

        wanted = norm_title(wanted)
        exact = [t for t in tasks if norm_title(t["title"]) == wanted]
        if len(exact) == 1:
            return exact[0], []
        partial = [t for t in tasks if wanted and (wanted in norm_title(t["title"]) or norm_title(t["title"]) in wanted)]
        if len(partial) == 1:
            return partial[0], []
        return None, partial or [t for t in tasks if not wanted]

    def complete_task(task_title):
        if memory is None:
            return {"completed": False, "reason": "Memory database is unavailable"}
        task, candidates = resolve_task(task_title)
        if task is None:
            return {"completed": False, "ambiguous": bool(candidates),
                    "candidates": [{"id": int(t["id"]), "title": t["title"]} for t in candidates[:10]],
                    "reason": "Please identify one open task by its title; no task was changed."}
        changed = memory.complete_task(task["id"])
        # Read-after-write verification prevents a false success message.
        still_open = any(int(t["id"]) == int(task["id"]) for t in memory.list_tasks())
        if not changed or still_open:
            return {"completed": False, "task": task["title"], "reason": "Database did not confirm the task as completed"}
        core.last_created_task_id = None
        core.last_task_snapshot = []
        return {"completed": True, "task_id": int(task["id"]), "title": task["title"],
                "remaining_open_count": len(memory.list_tasks())}

    def delete_task(task_title):
        if memory is None:
            return {"deleted": False, "reason": "Memory database is unavailable"}
        task, candidates = resolve_task(task_title)
        if task is None:
            return {"deleted": False, "candidates": [{"title": t["title"], "id": int(t["id"])} for t in candidates],
                    "reason": "No unique open task matched"}
        deleted = memory.delete_task(task["id"])
        return {"deleted": bool(deleted), "title": task["title"] if deleted else None}

    def create_reminder(text="", delay_seconds=0, due_at=""):
        if memory is None:
            return {"created": False, "reason": "Memory database is unavailable"}
        delay = int(delay_seconds or 0)
        if not delay and due_at:
            delay = int(datetime.fromisoformat(due_at).timestamp() - time.time())
        if not delay and getattr(core, "pending_reminder_seconds", None):
            delay = int(core.pending_reminder_seconds)
        if delay <= 0 or delay > 365 * 86400:
            return {"created": False, "needs_followup": True, "reason": "Provide a positive reminder time within one year"}
        text = (text or "").strip()
        if text and unsafe_reminder_content(text):
            return {"created": False, "reason": "This reminder would encourage ingesting a hazardous substance, so it was not saved."}
        if not text:
            core.pending_reminder_seconds = delay
            core.pending_reminder_created_at = time.time()
            return {"created": False, "needs_followup": True, "delay_seconds": delay,
                    "question": "What should I remind you about?"}
        reminder_id = memory.add_reminder(text, time.time() + delay)
        core.pending_reminder_seconds = None
        core.pending_reminder_created_at = None
        return {"created": bool(reminder_id), "reminder_id": reminder_id, "text": text,
                "due_at": datetime.fromtimestamp(time.time() + delay).isoformat(timespec="seconds")}

    def list_reminders(include_fired=False):
        if memory is None:
            return {"available": False, "reminders": []}
        rows = memory.list_reminders(include_fired=bool(include_fired))
        return {"count": len(rows), "reminders": [{"id": int(r["id"]), "text": r["text"],
                    "due_at": datetime.fromtimestamp(r["remind_ts"]).isoformat(timespec="seconds"),
                    "fired": bool(r.get("fired"))} for r in rows[:20]]}

    def start_timer(seconds, label="Focus timer"):
        if memory is None:
            return {"started": False, "reason": "Reminder database is unavailable"}
        seconds = int(seconds)
        if seconds < 1 or seconds > 7 * 86400:
            return {"started": False, "reason": "Timer must be between 1 second and 7 days"}
        label = (label or "Focus timer").strip()[:80]
        due = time.time() + seconds
        reminder_id = memory.add_reminder(f"{label} finished.", due)
        if callable(getattr(core, "timer_callback", None)):
            core.timer_callback(seconds, label)
        return {"started": bool(reminder_id), "label": label, "seconds": seconds,
                "reminder_id": reminder_id, "due_at": datetime.fromtimestamp(due).isoformat(timespec="seconds")}

    def camera_status():
        if app is None:
            return {"available": False, "reason": "Desktop camera controller is not connected"}
        active = bool(getattr(app, "camera_active", False))
        analyzer = getattr(app, "expression_analyzer", None)
        analysis_available = bool(analyzer is not None and getattr(analyzer, "available", False))
        analysis_reason = str(getattr(analyzer, "last_error", "") or "") if analyzer is not None else "The local FER+ model has not been loaded"
        visual = ""
        provider = getattr(core, "visual_context_provider", None)
        if active and callable(provider):
            try:
                visual = str(provider() or "")
            except Exception:
                visual = ""
        return {"preview_active": active, "raw_frames_sent_to_llm": False,
                "latest_expression_sample": visual or None,
                "analysis_available": analysis_available,
                "analysis_reason": analysis_reason,
                "camera_enabled_in_settings": bool(getattr(app, "settings", {}).get("webcam_enabled", False)),
                "can_describe_raw_image": False,
                "message": ("A recent local expression feature sample is available." if visual else
                            "Preview may be active, but no fresh local expression sample is available yet.")}

    def desktop_context():
        if app is None:
            return {"available": False}
        enabled = bool(getattr(app, "awareness_enabled", False))
        if not enabled:
            return {"available": False, "awareness_enabled": False,
                    "scope": "Desktop Awareness is disabled; no current foreground context is supplied."}
        if sys.platform != "win32":
            return {"available": False, "awareness_enabled": True,
                    "scope": "Desktop Awareness currently requires Windows; no live foreground context is available on this platform."}
        try:
            idle = app._idle_seconds()
        except Exception:
            idle = None
        snapshot = None
        try:
            engine = getattr(app, "context_engine", None)
            if engine is not None:
                snapshot = engine.observe(idle_seconds=idle).safe_summary()
        except Exception:
            snapshot = None
        try:
            category = app._foreground_category()
        except Exception:
            category = (snapshot or {}).get("app_category")
        return {"available": bool(snapshot is not None or category),
                "awareness_enabled": enabled,
                "foreground_category": category,
                "idle_seconds": idle,
                "snapshot": snapshot,
                "scope": "Foreground app/category and a privacy-filtered window title as clues only; no screen pixels or browser page content"}

    def speak_text(text):
        if app is None or not callable(getattr(app, "_speak", None)):
            return {"started": False, "reason": "Local text-to-speech is not connected"}
        import threading
        text = (text or "").strip()
        if not text:
            return {"started": False, "reason": "There is no text to speak"}
        threading.Thread(target=app._speak, args=(text[:2000],), daemon=True).start()
        return {"started": True, "message": "Local speech playback was started"}

    add("get_capabilities", "Inspect the assistant's real registered tools, available features, and current runtime status: which systems are active, ready, unavailable, or connected, including local AI, memory, tasks, reminders, camera, desktop awareness, voice, and proactive features. Use for capability/tool inventory questions or to verify whether a feature is available.", {}, [], capabilities, "local_ai", "Introspection")
    add("get_user_profile", "Retrieve saved USER profile fields, such as the user's stated name. Do not use this to retrieve the assistant/Buddy name; that is not a fact about the user.", {}, [], profile, "memory", "Memory")
    add("search_memory", "Search this Study Buddy profile's saved facts, notes, summaries, and past or previous conversation turns/chats. Use when asked what we discussed, what the user told you before, earlier conversations, what happened in a past chat, or what you remember. Always search before claiming you do not remember. For a broad request about previous chats with no topic, pass query='' and scope='conversations' to retrieve recent saved turns. For a topic, search the topic terms. Use list_tasks for structured task status/history; task records are not inferred from chat transcripts.",
        {"query": {"type": "string", "description": "Topic or keyword to search; use an empty string for recent history"},
         "scope": {"type": "string", "enum": ["all", "conversations", "notes", "facts"], "description": "Which memory store to search"},
         "limit": {"type": "integer", "description": "Maximum results from 1 to 20"}}, ["query"], search_memory, "memory", "Memory")
    add("save_memory", "Save a non-sensitive user-provided fact or note for future conversations.",
        {"text": {"type": "string", "description": "Exact information the user asked to remember"},
         "kind": {"type": "string", "enum": ["fact", "note"]}}, ["text"], save_memory, "memory", "Memory")
    add("add_task", "Add a task or to-do item using the title the user supplies, including casual wording or typos; do not reject a title just because it is unusual. Use when asked to add, create, put, or save something to the user's task list. Treat titles as text unless the user explicitly asks to launch or play something.",
        {"title": {"type": "string"}, "details": {"type": "string"}, "due_at": {"type": "string", "description": "Optional ISO datetime"}}, ["title"], add_task, "tasks", "Productivity")
    add("list_tasks", "Read the user's task list from the database. Use for show/list my tasks, what tasks remain, which task was previously added, task history, completed/done/finished tasks, or what the user completed before. Use status='completed' for only completed tasks; status='all' for open plus completed task history; status='open' for pending tasks. Returns stable task IDs and statuses; this is the source of truth for task state.",
        {"include_completed": {"type": "boolean", "description": "Legacy flag to include both open and completed tasks"},
         "status": {"type": "string", "enum": ["open", "completed", "all"], "description": "Which task states to retrieve"}}, [], list_tasks, "tasks", "Productivity")
    add("complete_task", "Mark one specific open task as complete/done/finished. Use list_tasks first when the reference is ambiguous (e.g. 'that one' or 'the task I just added'); never claim success unless the tool confirms it.",
        {"task_title": {"type": "string", "description": "Exact task title or a clear reference such as this task when uniquely resolvable"}}, ["task_title"], complete_task, "tasks", "Productivity")
    add("delete_task", "Delete one open task by its title. Use list_tasks first if the target is ambiguous.",
        {"task_title": {"type": "string"}}, ["task_title"], delete_task, "tasks", "Productivity")
    add("create_reminder", "Create a persistent reminder. If time is supplied without reminder text, ask what the reminder is for.",
        {"text": {"type": "string"}, "delay_seconds": {"type": "integer"}, "due_at": {"type": "string"}}, [], create_reminder, "reminders", "Productivity")
    add("list_reminders", "List pending reminders; include fired reminders only when explicitly requested.",
        {"include_fired": {"type": "boolean"}}, [], list_reminders, "reminders", "Productivity")
    add("start_timer", "Start a persistent countdown or focus timer with a duration in seconds.",
        {"seconds": {"type": "integer"}, "label": {"type": "string"}}, ["seconds"], start_timer, "focus_timer", "Productivity")
    add("get_camera_status", "Check live camera status and local facial-expression summary. Use when asked whether you can see the user, if the camera is on, to look at them, how they look, what they look like, or whether an expression estimate is available. The model never receives raw camera images.",
        {}, [], camera_status, "camera_preview", "Perception")
    add("get_desktop_context", "Get the current foreground application/category, idle time, and a privacy-filtered active-window title (which usually reflects the selected browser tab title) as tentative clues when Desktop Awareness is enabled. Use for questions about what the user may be doing right now, what they are looking at, which app/window/browser tab is open, what tab they are viewing, or to make an educated guess from the active-window title. When the title gives a clue, give a brief tentative interpretation of the likely activity and mention the title-based evidence. Never claim to inspect screen pixels, browser page contents, page text, or video content.",
        {}, [], desktop_context, "desktop_awareness", "Context")
    add("speak_text", "Speak supplied text using local text-to-speech. Use only when the user explicitly asks you to read or say something aloud.",
        {"text": {"type": "string"}}, ["text"], speak_text, "voice_output", "Voice")
    return registry
