"""Study Buddy desktop application: chat, floating face, optional voice, tray and awareness."""
import os, sys, json, time, queue, threading, datetime, subprocess, ctypes, platform, math
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from chat_core import ChatCore
from memory import Memory
from personal_companion import PersonalCompanion
from backup_manager import create_backup, restore_backup
from lifecycle import LifecycleService
from features.camera.policy import CameraPolicy
from features.camera.preview import open_camera_preview
from features.camera.expression import CameraExpressionAnalyzer
import re
from security.windows_efs import encrypt_folder, check_efs_file
from context_engine import ContextEngine
from proactive_policy import (classify_user_boundary, mentions_closed_task,
                              clear_legacy_automatic_pause_state,
                              ProactivePauseFlow, ProactiveRateLimiter)
from intelligence import ToolMonitor, build_default_registry
from intelligence.dashboard import IntelligenceDashboard
from intelligence.capability_context import build_capability_context
from agent import ToolOrchestrator, build_builtin_tool_registry

MEMORY_REVIEW_QUIET_SECONDS = 90


def read_settings():
    with open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8") as f:
        return json.load(f)

class StudyBuddyApp:
    def __init__(self):
        # Lets the background agent know the desktop is alive so the GUI can show
        # due reminders directly in the conversation instead of racing for them.
        self._desktop_mutex = None
        if os.name == "nt":
            try:
                self._desktop_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, r"Local\StudyBuddyDesktopApp")
            except Exception:
                self._desktop_mutex = None
        self.root_dir = ROOT
        self.settings = read_settings()
        self.ollama_available = None
        self._ollama_probe_running = False
        self.voice_listening = False
        profile = self.settings.get("profile", "hamim")
        self.user_dir = os.path.join(ROOT, "users", profile)
        self.memory = Memory(os.path.join(self.user_dir, "memory.db"))
        self.personal_companion = PersonalCompanion(self.memory)
        clear_legacy_automatic_pause_state(self.memory, self.personal_companion)
        self.proactive_pause_flow = ProactivePauseFlow()
        self._pause_reply_only = False
        self.core = ChatCore(ROOT, memory=self.memory)
        self.intelligence_monitor = ToolMonitor(os.path.join(ROOT, "logs", "intelligence.sqlite3"))
        self.capability_registry = build_default_registry(self)
        self.lifecycle = LifecycleService(ROOT, self.memory, self.core.llm, profile=profile)
        self.lifecycle.ensure_baseline()
        self._attach_intelligence_hooks()
        self.events = queue.Queue()
        self.core.timer_callback = lambda seconds, label: self.events.put(("set_chat_timer", int(seconds), str(label)))
        self.busy = False
        self.closing = False
        self.awareness_enabled = bool(self.settings.get("awareness_enabled", False))
        self.voice_enabled = bool(self.settings.get("voice_enabled", False))
        self.tray_icon = None
        self.tray_thread = None
        self.face_window = None
        self.face_canvas = None
        self.face_mode = "neutral"
        self.face_speaking = False
        self.activity_mode = "idle"
        self.voice_levels = [0.0] * 28
        self.voice_frequency = 0
        self.camera_active = False
        self.camera_consent_pending = False
        self.camera_declined_this_chat = False
        self.last_camera_topic_ts = 0.0
        self.expression_analyzer = None
        self.core.visual_context_provider = self._visual_context
        self.camera_policy = CameraPolicy(
            enabled=bool(self.settings.get("webcam_enabled", False)),
            minimum_interval_minutes=int(self.settings.get("camera_min_interval_minutes", 5)),
        )
        self._whisper_model = None
        self._whisper_lock = threading.Lock()
        self.face_expression_until = 0.0
        self.face_started_at = time.perf_counter()
        self.last_nudge = 0.0
        self.proactive_limiter = ProactiveRateLimiter(
            os.path.join(self.user_dir, "proactive_state.json"),
            global_cooldown_seconds=0,
            same_app_cooldown_seconds=0,
            attempt_cooldown_seconds=0,
            daily_limit=min(5, max(1, int(self.settings.get("proactive_daily_limit", 5)))),
        )
        self.last_context_signature = None
        self.last_context_since = time.time()
        self.last_nudged_signature = None
        # One title-context nudge per distinct app/title during this app session.
        # The signature is a digest, so raw window titles are not retained here.
        self._seen_title_contexts = set()
        self._delivered_title_contexts = set()
        # Window check-in tracking: signature -> timing record (no raw titles kept).
        self._window_track = {}
        self._last_window_nudge_ts = 0.0
        self._last_title_nudge_text = ""
        self._pause_hint_shown_session = False
        self.closed_task_reminder = ""
        self.last_reply = ""
        self.last_interaction_ts = time.time()
        self._last_entry_activity_ts = 0.0
        self.last_turn_completed_ts = self.last_interaction_ts
        self.session_started_at = self.last_interaction_ts
        self._phase2_previous_session = float(self.personal_companion.setting("initiative_last_session_ts", "0") or 0)
        self._phase2_session_return = self.last_interaction_ts - self._phase2_previous_session >= 8*3600 if self._phase2_previous_session else False
        self.personal_companion.set_setting("initiative_last_session_ts", str(self.last_interaction_ts))
        self._phase2_retention_day = ""
        self._phase2_wind_down_day = ""
        self._phase2_session_return_consumed = False
        self._phase2_nudge_queued = False
        self._phase2_rhythm_offer_queued = False
        self._consent_event_queued = False
        self.last_reminder_check = 0
        self.last_category = None
        self.category_since = time.time()
        self.proactive_worker_running = False
        self._memory_review_running = False
        self._memory_review_status = "Waiting for new chat to review in the background."
        self._memory_review_retry_after = 0.0
        self._memory_review_last_summary = str(
            self.personal_companion.setting("conversation_review_last_summary", "") or "")
        self._memory_review_cancel_event = threading.Event()
        self.context_engine = ContextEngine()
        self._proactive_cancel_event = threading.Event()
        self._streaming_reply_active = False
        self.core.desktop_context_provider = self._desktop_context_for_chat
        self.timer_job = None
        self.timer_running = False
        self.timer_remaining = int(self.settings.get("pomodoro_minutes", 25))*60
        self.root = tk.Tk()
        # Compose the LLM-first agent once all desktop callbacks and state providers exist.
        # Every callable exposed to the model is explicitly registered and schema-validated.
        self.core.tool_registry = build_builtin_tool_registry(
            self.core, app=self, capability_registry=self.capability_registry
        )
        if self.settings.get("tool_calling_enabled", True):
            self.core.tool_orchestrator = ToolOrchestrator(
                self.core.llm, self.core.tool_registry, monitor=self.intelligence_monitor,
                max_rounds=int(self.settings.get("tool_max_rounds", 2)),
                max_calls_per_turn=int(self.settings.get("tool_max_calls_per_turn", 4)),
                max_routed_tools=int(self.settings.get("tool_retrieval_max_tools", 4)),
                retrieval_mode=self.settings.get("tool_retrieval_mode", "bm25"),
                routing_config_path=os.path.join(ROOT, "config", "intent_dictionary.json"),
            )
        self.root.title("Study Buddy")
        self.root.geometry("820x650")
        self.root.minsize(640, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._apply_autostart(bool(self.settings.get("auto_start_enabled", True)))
        self._start_background_agent()
        self._build_ui()
        self.root.after(100, self._poll_events)
        # Start the opt-in relationship flow at every launch; this replaces the old
        # requirement to find and click the My World onboarding button.
        self.root.after(900, lambda: self._relationship_welcome(new_session=True))
        self.root.after(40, self._animate_face)
        self.root.after(60, self._animate_activity)
        if self.awareness_enabled:
            self._start_awareness()
        self.root.after(5000, self._scheduler_tick)
        self.root.after(15000, self._periodic_ollama_check)
        self._status("Ready. Checking local Ollama…")
        threading.Thread(target=self._check_ollama, daemon=True).start()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(12,10)); top.pack(fill="x")
        ttk.Label(top, text="Study Buddy", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Button(top, text="Floating face", command=self.toggle_face).pack(side="right", padx=3)
        ttk.Button(top, text="Settings", command=self.open_settings).pack(side="right", padx=3)
        ttk.Button(top, text="Backup", command=self.backup_restore).pack(side="right", padx=3)
        ttk.Button(top, text="Tasks", command=self.open_tasks).pack(side="right", padx=3)
        ttk.Button(top, text="My World", command=self.open_my_world).pack(side="right", padx=3)
        ttk.Button(top, text="Memory", command=self.open_memory).pack(side="right", padx=3)
        ttk.Button(top, text="Tools", command=self.open_tools).pack(side="right", padx=3)
        ttk.Button(top, text="Capabilities", command=self.open_intelligence_center).pack(side="right", padx=3)
        ttk.Button(top, text="Camera preview", command=self.open_camera).pack(side="right", padx=3)
        main = ttk.Frame(self.root, padding=(12,0,12,8)); main.pack(fill="both", expand=True)
        self.chat = tk.Text(main, wrap="word", state="disabled", font=("Segoe UI", 10), padx=10, pady=10,
                            background="#101827", foreground="#e7edf5", insertbackground="white", relief="flat")
        scroll=ttk.Scrollbar(main, command=self.chat.yview); self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("you", foreground="#7dd3fc", font=("Segoe UI",10,"bold"))
        self.chat.tag_configure("buddy", foreground="#a7f3d0", font=("Segoe UI",10,"bold"))
        self.chat.tag_configure("meta", foreground="#9ca3af", font=("Segoe UI",9,"italic"))
        hello = (f"Hi! I'm {self.core.buddy_name}. I'm here. I run locally through Ollama.\n\n"
                 "Camera privacy: camera access is off unless you enable it in Settings. You can say 'look at me' or 'turn on camera' to request access. "
                 "I may ask permission in a relevant conversation, but I will never activate the camera without your clear yes. "
                 "If you allow it, optional facial-feature analysis runs locally when its model is installed; raw camera images or video are not sent to Llama or saved. "
                 "You can say 'stop camera' at any time. Facial signals are only rough clues, not proof of how you feel.\n\n"
                 "You can ask in your own words to save a note, add/show/complete tasks, set reminders, "
                 "start a custom-length focus timer or Pomodoro, and list pending reminders. For example: "
                 "'Keep a note that my exam is Friday', 'Add finishing my assignment to my tasks', "
                 "'Nudge me in 20 minutes to drink water', or 'Start a 45-minute focus timer'. "
                 "If I don't understand a request, try 'What built-in commands can you do?' and I'll show examples.")
        self._append("Buddy", hello, "buddy")
        activity_frame = ttk.Frame(self.root, padding=(12, 0, 12, 4))
        activity_frame.pack(fill="x")
        self.activity_canvas = tk.Canvas(activity_frame, height=34, width=300,
                                        background="#101827", highlightthickness=0, relief="flat")
        self.activity_canvas.pack(side="left", fill="x", expand=True)
        self.activity_canvas.create_text(10, 17, text="READY", anchor="w", fill="#8293a8",
                                         font=("Segoe UI", 8, "bold"), tags=("activity_label",))
        self.proactive_availability_switch = tk.Canvas(
            activity_frame, width=208, height=34, background="#101827",
            highlightthickness=0, cursor="hand2", takefocus=True)
        self.proactive_availability_switch.pack(side="right", padx=(8, 0))
        self.proactive_availability_switch.bind("<Button-1>", self._toggle_proactive_availability)
        self.proactive_availability_switch.bind("<space>", self._toggle_proactive_availability)
        self.proactive_availability_switch.bind("<Return>", self._toggle_proactive_availability)
        self._draw_proactive_availability_switch()
        # Reply-length slider (controls max tokens per reply; Auto = adaptive modes)
        token_frame=ttk.Frame(self.root, padding=(12,0,12,2)); token_frame.pack(fill="x")
        ttk.Label(token_frame, text="Reply length").pack(side="left")
        _saved_tokens=0
        try: _saved_tokens=int(self.settings.get("reply_token_limit") or 0)
        except (TypeError, ValueError): _saved_tokens=0
        self.token_auto_var=tk.BooleanVar(value=_saved_tokens<=0)
        self.token_value_var=tk.IntVar(value=max(32,min(1024,_saved_tokens)) if _saved_tokens>0 else 110)
        self.token_label_var=tk.StringVar()
        self.token_scale=ttk.Scale(token_frame, from_=32, to=1024, orient="horizontal", command=self._on_token_slider)
        self.token_scale.set(self.token_value_var.get())
        self.token_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.token_scale.bind("<ButtonRelease-1>", lambda e: self._save_token_setting())
        self.token_scale.bind("<KeyRelease>", lambda e: self._save_token_setting())
        ttk.Label(token_frame, textvariable=self.token_label_var, width=14).pack(side="left")
        ttk.Checkbutton(token_frame, text="Auto", variable=self.token_auto_var, command=self._on_token_auto).pack(side="left", padx=(6,0))
        ttk.Button(token_frame, text="Auto-tune", command=self.auto_tune_hardware).pack(side="left", padx=(8,0))
        self._refresh_token_widgets()
        self.hardware_label_var=tk.StringVar(value=self._hardware_summary())
        ttk.Label(self.root, textvariable=self.hardware_label_var, foreground="#8293a8", font=("Segoe UI",8), padding=(12,0,12,2)).pack(fill="x")
        bottom=ttk.Frame(self.root, padding=(12,0,12,8)); bottom.pack(fill="x")
        self.entry=tk.Text(bottom, height=3, wrap="word", font=("Segoe UI",10)); self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Control-Return>", self._send_event)
        self.entry.bind("<KeyPress>", self._mark_entry_activity, add="+")
        controls=ttk.Frame(bottom); controls.pack(side="left", padx=(8,0), fill="y")
        self.send_btn=ttk.Button(controls,text="Send",command=self.send); self.send_btn.pack(fill="x",pady=2)
        ttk.Button(controls,text="🎙 Voice",command=self.voice_input).pack(fill="x",pady=2)
        ttk.Button(controls,text="Speak reply",command=self.speak_last).pack(fill="x",pady=2)
        self.status=tk.StringVar(value="Starting…")
        ttk.Label(self.root,textvariable=self.status,anchor="w",padding=(12,5)).pack(fill="x")
        self.entry.focus_set()

    def _hardware_summary(self):
        profile=getattr(self.core,"hardware",None)
        if not profile:
            return "Hardware auto-tuning is off or unavailable."
        try:
            import hardware_profile
            return "Detected: "+hardware_profile.describe(profile, getattr(self.core.llm,"options",None))
        except Exception:
            return "Hardware detected."

    def auto_tune_hardware(self):
        """One-time benchmark of thread counts on the real model; result is saved and reused."""
        profile=getattr(self.core,"hardware",None)
        if not profile:
            self._status("Auto-tune needs hardware auto-tuning enabled (settings.json: auto_hardware_tuning)."); return
        if self.busy:
            self._status("Wait for the current reply to finish, then run Auto-tune."); return
        if not messagebox.askyesno("Auto-tune",
                "Auto-tune tests a few CPU thread counts with your local model (about 1-2 minutes).\n"
                "Chat is unavailable while it runs. Continue?", parent=self.root):
            return
        self.busy=True
        self.send_btn.configure(state="disabled", text="Tuning…")
        config_dir=os.path.join(ROOT,"config")
        def progress(message): self.root.after(0, lambda m=message: self._status(m))
        def worker():
            try:
                import hardware_profile
                record=hardware_profile.calibrate(self.core.llm, profile, config_dir, progress=progress)
                self.core.llm.options["num_thread"]=int(record["num_thread"])
                self.root.after(0, lambda: self.hardware_label_var.set(self._hardware_summary()))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._status(f"Auto-tune failed: {e}"))
            finally:
                def restore():
                    self.busy=False
                    self.send_btn.configure(state="normal", text="Send")
                self.root.after(0, restore)
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_token_widgets(self):
        auto=self.token_auto_var.get()
        try: self.token_scale.state(["disabled"] if auto else ["!disabled"])
        except Exception: pass
        self.token_label_var.set("Auto (adaptive)" if auto else f"{self.token_value_var.get()} tokens")

    def _on_token_slider(self, value):
        try: self.token_value_var.set(int(float(value)))
        except (TypeError, ValueError): return
        self._refresh_token_widgets()
        self._apply_token_setting()  # live: takes effect on the next reply

    def _on_token_auto(self):
        self._refresh_token_widgets()
        self._apply_token_setting()
        self._save_token_setting()

    def _apply_token_setting(self):
        value=0 if self.token_auto_var.get() else int(self.token_value_var.get())
        self.settings["reply_token_limit"]=value
        try: self.core.settings["reply_token_limit"]=value
        except Exception: pass

    def _save_token_setting(self):
        self._apply_token_setting()
        path=os.path.join(ROOT,"config","settings.json"); temporary=path+".tmp"
        try:
            with open(temporary,"w",encoding="utf-8") as stream:
                json.dump(self.settings,stream,indent=2); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,path)
        except OSError as exc:
            self._status(f"Could not save reply length: {exc}")

    def _mark_entry_activity(self, event=None):
        # Transient timestamp only: no typed text or key data is persisted.
        self._last_entry_activity_ts=time.time()
        self.last_interaction_ts=self._last_entry_activity_ts
        if self._memory_review_running:
            self._memory_review_cancel_event.set()

    def _append(self, who, text, tag=None):
        self.chat.configure(state="normal")
        self.chat.insert("end", who + ": ", tag or ("you" if who=="You" else "buddy"))
        self.chat.insert("end", text.strip()+"\n\n")
        self.chat.configure(state="disabled"); self.chat.see("end")

    def _relationship_welcome(self, new_session=False):
        if self.closing or self.busy or not self.settings.get("proactive_enabled", True):
            return
        if time.time() - float(self._last_entry_activity_ts or 0) < 5:
            return
        if self._is_quiet(datetime.datetime.now().strftime("%H:%M")):
            return
        try:
            prompt = self.core.start_relationship_checkin(new_session=new_session)
            if not prompt:
                return
            self._show_relationship_welcome(prompt)
        except Exception as exc:
            self._status("Getting-to-know-you prompt skipped: " + str(exc)[:120])

    def _show_relationship_welcome(self, prompt):
        if not prompt:
            return
        self._append("Buddy", prompt, "buddy")
        self.core.history.append({"role": "assistant", "content": prompt})
        self.memory.add_turn("assistant", prompt)
        self.last_reply = prompt
        self.face_mode = "curious"
        self.face_expression_until = time.perf_counter() + 2.2
        self._status("Buddy started a conversation")
        if self.voice_enabled:
            threading.Thread(target=self._speak, args=(prompt,), daemon=True).start()
        if self.root.state() == "withdrawn" and self.tray_icon:
            try: self.tray_icon.notify(prompt[:180], "Study Buddy")
            except Exception: pass

    def _stream_token_to_chat(self, token):
        """Render provisional model chunks in the existing assistant reply block."""
        if not token:
            return
        self.chat.configure(state="normal")
        if not self._streaming_reply_active:
            self.chat.mark_set("stream_block_start", "end-1c")
            self.chat.mark_gravity("stream_block_start", "left")
            self.chat.insert("end", "Buddy: ", "buddy")
            self.chat.mark_set("stream_body_start", "end-1c")
            self.chat.mark_gravity("stream_body_start", "left")
            self.chat.mark_set("stream_body_end", "end-1c")
            self.chat.mark_gravity("stream_body_end", "right")
            self._streaming_reply_active = True
        self.chat.insert("stream_body_end", token)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _finish_streaming_reply(self, final_text, remove=False):
        """Replace provisional tokens with the safety-checked final reply."""
        if not self._streaming_reply_active:
            return False
        self.chat.configure(state="normal")
        start = self.chat.index("stream_block_start")
        end = self.chat.index("stream_body_end")
        self.chat.delete(start, end)
        if not remove:
            self.chat.mark_set("stream_commit", start)
            self.chat.insert("stream_commit", "Buddy: ", "buddy")
            self.chat.insert("stream_commit", (final_text or "").strip() + "\n\n")
        for mark in ("stream_block_start", "stream_body_start", "stream_body_end", "stream_commit"):
            try:
                self.chat.mark_unset(mark)
            except Exception:
                pass
        self.chat.see("end")
        self.chat.configure(state="disabled")
        self._streaming_reply_active = False
        return True

    def _status(self, text): self.status.set(text)

    def _draw_proactive_availability_switch(self):
        canvas = getattr(self, "proactive_availability_switch", None)
        if canvas is None:
            return
        paused = bool(self.proactive_pause_flow.is_paused(time.time()))
        enabled = bool(self.settings.get("proactive_enabled", True)) and not paused
        canvas.delete("all")
        canvas.create_text(5, 17, text="Luna availability", anchor="w",
                           fill="#e2e8f0", font=("Segoe UI", 9, "bold"))
        left, top, right, bottom = 140, 5, 202, 29
        color = "#22c55e" if enabled else "#f59e0b" if paused else "#e11d48"
        canvas.create_oval(left, top, left + 24, bottom, fill=color, outline=color)
        canvas.create_oval(right - 24, top, right, bottom, fill=color, outline=color)
        canvas.create_rectangle(left + 12, top, right - 12, bottom, fill=color, outline=color)
        if enabled:
            canvas.create_text(left + 17, 17, text="ON", fill="#052e16",
                               font=("Segoe UI", 8, "bold"))
            knob_x = right - 13
        else:
            canvas.create_text(right - (22 if paused else 19), 17,
                               text="PAUSED" if paused else "OFF",
                               fill="#422006" if paused else "#fff1f2",
                               font=("Segoe UI", 6 if paused else 7, "bold"))
            knob_x = left + 13
        canvas.create_oval(knob_x - 9, 8, knob_x + 9, 26,
                           fill="#f8fafc", outline="#f8fafc")
        canvas.configure(
            cursor="hand2",
            takefocus=True,
        )
        canvas.bind("<Enter>", lambda _event: canvas.configure(highlightthickness=1, highlightbackground="#7dd3fc"))
        canvas.bind("<Leave>", lambda _event: canvas.configure(highlightthickness=0))

    def _apply_proactive_availability(self, enabled, *, persist=True, announce=True):
        enabled = bool(enabled)
        previous = bool(self.settings.get("proactive_enabled", True))
        self.settings["proactive_enabled"] = enabled
        if persist:
            path = os.path.join(ROOT, "config", "settings.json")
            temporary = path + ".tmp"
            try:
                with open(temporary, "w", encoding="utf-8") as stream:
                    json.dump(self.settings, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            except OSError as exc:
                self.settings["proactive_enabled"] = previous
                self._draw_proactive_availability_switch()
                messagebox.showerror("Luna availability", f"I couldn't save that setting:\n{exc}", parent=self.root)
                return False
        self.core.settings["proactive_enabled"] = enabled
        self.proactive_pause_flow.paused_until = 0.0
        self.proactive_pause_flow.awaiting_duration = False
        self.proactive_pause_flow.awaiting_until = 0.0
        if not enabled:
            self._proactive_cancel_event.set()
            self._phase2_nudge_queued = False
            self._phase2_rhythm_offer_queued = False
            builder = getattr(self.core, "relationship_builder", None)
            if builder:
                builder.cancel()
            if not self.busy:
                self.core.pending_proactive_context = None
                self.core.pending_proactive_kind = None
                self.core.pending_proactive_window_title = None
                self.core.pending_proactive_app_name = None
                self.core.pending_proactive_app_category = None
                self.core.pending_proactive_observed_at = None
        self.last_interaction_ts = time.time()
        self.last_turn_completed_ts = self.last_interaction_ts
        self._last_entry_activity_ts = self.last_interaction_ts
        self._draw_proactive_availability_switch()
        label = "on" if enabled else "off"
        if announce:
            detail = (" Window-title check-ins are available too." if enabled and self.awareness_enabled
                      else " Turn on Desktop Awareness in Settings if you also want window-title check-ins." if enabled
                      else " You can turn me back on here anytime.")
            self._append("Buddy", f"Proactive check-ins are {label}.{detail}", "buddy")
        self._status(f"Proactive check-ins {label}.")
        return True

    def _toggle_proactive_availability(self, _event=None):
        currently_available = (bool(self.settings.get("proactive_enabled", True))
                               and not self.proactive_pause_flow.is_paused(time.time()))
        enabled = not currently_available
        self._apply_proactive_availability(enabled)
        return "break"

    def _send_event(self, _event=None): self.send(); return "break"
    def send(self):
        text=self.entry.get("1.0","end").strip()
        if not text or self.busy: return
        if self._memory_review_running:
            self._memory_review_cancel_event.set()
        # Interactive chat has priority over a low-priority proactive generation.
        # The proactive Ollama stream is cancelled and its provisional result dropped.
        if self.proactive_worker_running:
            self._proactive_cancel_event.set()
        self.entry.delete("1.0","end"); self._append("You",text,"you")
        self.last_interaction_ts = time.time()
        pause_notice = self._update_proactive_boundary_from_user(text)
        if self._pause_reply_only:
            self._finish_pause_flow_turn(text, pause_notice)
            return
        if self._handle_camera_chat_command(text):
            return
        self.busy=True; self.send_btn.configure(state="disabled", text="Thinking…"); self.face_mode="thinking"; self.activity_mode="thinking"
        thinking_status = "Thinking…"
        if pause_notice:
            thinking_status += " · check-in status updated"
        self._status(thinking_status)
        threading.Thread(target=self._reply_worker,args=(text,pause_notice),daemon=True).start()

    def _reply_worker(self, text, pause_notice=None):
        start = time.time()
        try:
            stream_state = {"text": "", "safety_blocked": False, "safety_candidate": ""}

            def on_token(chunk):
                if not chunk:
                    return True
                # Check the whole provisional output before making each new chunk
                # visible. If a safety rule matches, cancel generation and let
                # ChatCore replace the provisional answer with its fixed fallback.
                candidate = stream_state["text"] + str(chunk or "")
                safe, _reason = self.core.safety.check_output(candidate)
                if not safe:
                    stream_state["safety_blocked"] = True
                    stream_state["safety_candidate"] = candidate
                    on_token.safety_blocked = True
                    on_token.safety_candidate = candidate
                    return False
                stream_state["text"] = candidate
                self.events.put(("reply_token", chunk))
                return True

            on_token.safety_blocked = False
            on_token.safety_candidate = ""
            with self.intelligence_monitor.track(
                "chat_request", "Chat request", "ChatCore", "Message routed; private text omitted"
            ):
                result = self.core.handle_message(text, on_token=on_token)
            if pause_notice:
                reply = str(result.get("reply") or "").strip()
                result["reply"] = (reply + "\n\n" if reply else "") + pause_notice
            self.events.put(("reply", result, time.time() - start))
        except Exception as exc:
            # Keep a model/database exception from silently killing the worker thread.
            self.events.put(("reply_error", str(exc)))

    def _poll_events(self):
        try:
            while True:
                item=self.events.get_nowait(); kind=item[0]
                if kind == "reply_token":
                    self._stream_token_to_chat(item[1])
                    self._status("Responding…")
                elif kind == "reply":
                    _, result, elapsed = item
                    self.last_turn_completed_ts = time.time()
                    self.busy = False
                    self.send_btn.configure(state="normal", text="Send")
                    self.activity_mode = "idle"
                    if not self._finish_streaming_reply(result.get("reply", "")):
                        self._append("Buddy", result.get("reply", ""), "buddy")
                    self.last_reply = result.get("reply", "")
                    if self._reply_requests_camera_permission(self.last_reply) and not self.camera_declined_this_chat:
                        self.camera_consent_pending = True
                        self._status("Camera permission requested in chat. Reply yes or no; camera remains off until yes.")
                    flagged = result.get("flag") in ("high", "medium")
                    self.face_mode = "concerned" if flagged else "happy"
                    self.face_expression_until = time.perf_counter() + 2.4
                    if not self.camera_consent_pending:
                        self._status(f"{result.get('flag', 'ok')} · {elapsed:.1f}s · local model")
                    if self.voice_enabled and self.last_reply:
                        threading.Thread(target=self._speak, args=(self.last_reply,), daemon=True).start()
                elif kind == "reply_error":
                    self._finish_streaming_reply("", remove=True)
                    self.busy = False
                    self.send_btn.configure(state="normal", text="Send")
                    self.activity_mode = "idle"
                    self.face_mode = "concerned"
                    self.face_expression_until = time.perf_counter() + 3.0
                    self._append("Study Buddy error", item[1], "meta")
                    self._status("Reply failed. Check the error above.")
                elif kind == "rhythm_offer":
                    self._phase2_rhythm_offer_queued=False
                    router=getattr(self.core,"intent_router",None)
                    if (not self.settings.get("proactive_enabled", True)
                            or self.busy or self.proactive_worker_running or (router and router.has_pending)
                            or (self.core.relationship_builder and self.core.relationship_builder.phase)):
                        continue
                    if str(self.personal_companion.setting("tier_rhythm","0")).lower() not in ("0","false","off","no"):
                        continue
                    count=int(self.personal_companion.setting("rhythm_offer_count","0") or 0)
                    if count>=2 or str(self.personal_companion.setting("rhythm_offer_declined","0")).lower() not in ("0","false","off","no"):
                        continue
                    prompt="Would you like me to learn your usual study times? I store only coarse day, hour, and app-category counts on this PC; I never save window titles. Say yes, no, or skip."
                    self._append("Buddy",prompt,"buddy")
                    self.core.history.append({"role":"assistant","content":prompt})
                    self.memory.add_turn("assistant",prompt)
                    if router: router.start_rhythm_consent()
                    self.personal_companion.set_setting("rhythm_offer_count",str(count+1))
                    self.personal_companion.set_setting("rhythm_offer_last_ts",str(time.time()))
                elif kind == "phase2_nudge":
                    _, decision = item
                    self._phase2_nudge_queued=False
                    router = getattr(self.core, "intent_router", None)
                    # Recheck delivery gates on the UI thread; a user turn wins over a queued nudge.
                    if (not self.settings.get("proactive_enabled", True)
                            or self.busy or self.proactive_worker_running or (router and router.has_pending)
                            or (self.core.relationship_builder and self.core.relationship_builder.phase)):
                        continue
                    if not decision.text:
                        continue
                    is_title_checkin = decision.trigger == "title_clue"
                    self._append("Buddy", decision.text, "buddy")
                    if is_title_checkin:
                        # Window titles are volatile context: show the prompt and
                        # pass it to exactly one reply without saving it as chat.
                        self.core.pending_proactive_context = decision.text
                        self.core.pending_proactive_kind = "title_availability"
                        self.core.pending_proactive_window_title = str(decision.meta.get("window_title") or "")
                        self.core.pending_proactive_app_name = str(decision.meta.get("app_name") or "unknown")
                        self.core.pending_proactive_app_category = str(decision.meta.get("app_category") or "Other")
                        self.core.pending_proactive_observed_at = time.time()
                    else:
                        self.core.history.append({"role":"assistant","content":decision.text})
                        self.memory.add_turn("assistant", decision.text)
                        if decision.trigger in ("session_return", "usual_study_start"):
                            self.core.pending_proactive_context = decision.text
                            self.core.pending_proactive_kind = "general_availability"
                    self.core.initiative_policy.record_shown(decision)
                    self.core.last_initiative_nudge_id=decision.nudge_id
                    self.core.last_initiative_nudge_text=decision.text
                    if is_title_checkin:
                        limiter_app, limiter_category = self._window_limiter_key(
                            decision.meta.get("app_name"), decision.meta.get("app_category"),
                            decision.meta.get("window_title"))
                    else:
                        limiter_app, limiter_category = "studybuddy:"+decision.trigger, "Initiative"
                    self.proactive_limiter.record_attempt(limiter_app, limiter_category)
                    self.proactive_limiter.record_sent(limiter_app, limiter_category, decision.text)
                    self.personal_companion.set_setting("initiative_last_nudge_ts", str(time.time()))
                    if decision.trigger != "session_return": self._phase2_session_return_consumed=True
                    if decision.trigger == "open_loop_due" and router:
                        loop_id=decision.meta.get("loop_id")
                        title=decision.meta.get("title", "your deadline")
                        suggested=f"Help me plan revision for my {title}."
                        router.start_nudge_checkin(nudge_id=decision.nudge_id,trigger=decision.trigger,loop_id=loop_id,suggested_message=suggested)
                    elif decision.trigger == "session_return": self._phase2_session_return_consumed=True
                    elif decision.trigger == "wind_down": self._phase2_wind_down_day=datetime.date.today().isoformat()
                    self.last_reply=decision.text
                    self._status(f"Initiative check-in · {decision.trigger}")
                    if decision.trigger == "title_clue":
                        # The companion may be behind the app the student is using.
                        # Use the already-enabled local voice/tray channel so the
                        # check-in is actually noticeable outside the chat window.
                        if self.voice_enabled:
                            spoken = "I noticed another window and was curious about it."
                            threading.Thread(target=self._speak, args=(spoken,), daemon=True).start()
                        if self.root.state() == "withdrawn" and self.tray_icon:
                            try: self.tray_icon.notify(f"{self.core.buddy_name or 'Your study buddy'} is checking whether you are free for a quick chat.", "Study Buddy")
                            except Exception: pass
                elif kind == "proactive_skipped":
                    # No visible message is better than an irrelevant or intrusive fallback.
                    self.proactive_worker_running = False
                    if not self.settings.get("proactive_enabled", True):
                        continue
                    if not self.busy:
                        self.send_btn.configure(state="normal", text="Send")
                        self.activity_mode = "idle"
                        self._status("Check-in skipped — no suitable message.")
                elif kind == "proactive_reply":
                    # New events carry app/category so rate limits remain stable even
                    # when a browser changes its title for every page/video.
                    _, text, *context_parts = item
                    app_name = context_parts[0] if len(context_parts) > 0 else "unknown"
                    category = context_parts[1] if len(context_parts) > 1 else "Other"
                    decision = context_parts[2] if len(context_parts) > 2 else None
                    self.proactive_worker_running = False
                    # A just-arrived user request wins; never inject a proactive
                    # message into the middle of the interactive response.
                    if (self.busy or self._proactive_cancel_event.is_set()
                            or not self.settings.get("proactive_enabled", True)):
                        continue
                    is_title_checkin = bool(decision and getattr(decision, "trigger", None) == "title_clue")
                    if is_title_checkin and not self._title_decision_is_fresh(decision):
                        # Keep this title marked as seen for the rest of this app
                        # session; otherwise a quick alt-tab back to a stale context
                        # can produce the same missed check-in over and over.
                        self._status("The window changed before I could check in, so I skipped that guess.")
                        continue
                    if is_title_checkin:
                        # Repeats are now scheduled by time (see _window_checkin_mode); only block an identical sentence.
                        if text.strip().casefold() == str(self._last_title_nudge_text or "").strip().casefold():
                            self._status("Repeated window check-in suppressed.")
                            continue
                        self._last_title_nudge_text = text
                    if not is_title_checkin and self.proactive_limiter.is_duplicate(text):
                        if not self.busy:
                            self._status("Repeated check-in suppressed.")
                        continue
                    if is_title_checkin:
                        if not self._pause_hint_shown_session:
                            text = text.rstrip() + " If you'd like me to leave you to focus, just say 'pause'."
                            self._pause_hint_shown_session = True
                        builder = self.core.relationship_builder
                        if builder and builder.phase in ("availability", "topic_permission", "conversation"):
                            # A new stable foreground window takes priority over an
                            # unfinished personal chat and replaces its pending question.
                            builder.cancel()
                        # Keep the exact title available for one grounded reply, but
                        # never add this volatile OS context to reusable chat history.
                        self.core.pending_proactive_kind = "title_availability"
                        self.core.pending_proactive_window_title = str((decision.meta or {}).get("window_title") or "")
                        self.core.pending_proactive_app_name = str((decision.meta or {}).get("app_name") or app_name or "unknown")
                        self.core.pending_proactive_app_category = str((decision.meta or {}).get("app_category") or category or "Other")
                        self.core.pending_proactive_observed_at = time.time()
                    else:
                        self.core.pending_proactive_kind = None
                        self.core.pending_proactive_window_title = None
                        self.core.pending_proactive_app_name = None
                        self.core.pending_proactive_app_category = None
                        self.core.pending_proactive_observed_at = None
                    # Keep the exact visible message as an ephemeral hand-off for
                    # one reply. The title-only generator never receives reusable history.
                    self.core.pending_proactive_context = text
                    self.send_btn.configure(state="normal", text="Send")
                    self.activity_mode = "idle"
                    self._append("Buddy", text, "buddy")
                    if not is_title_checkin:
                        self.core.history.append({"role":"assistant","content":text})
                        self.memory.add_turn("assistant",text)
                    else:
                        # In-memory only (never saved to the database): the next reply can now see what was asked.
                        self.core.history.append({"role":"assistant","content":text})
                        recent = list(getattr(self.core, "recent_title_nudges", None) or [])
                        self.core.recent_title_nudges = (recent + [text])[-3:]
                    self.core.last_initiative_nudge_id = getattr(decision,"nudge_id",None)
                    self.core.last_initiative_nudge_text = text
                    if is_title_checkin:
                        limiter_app, limiter_category = self._window_limiter_key(
                            (decision.meta or {}).get("app_name", app_name),
                            (decision.meta or {}).get("app_category", category),
                            (decision.meta or {}).get("window_title", ""))
                    else:
                        limiter_app, limiter_category = app_name, category
                    self.proactive_limiter.record_sent(limiter_app, limiter_category, text)
                    if decision and getattr(self.core, "initiative_policy", None):
                        self.core.initiative_policy.record_shown(decision)
                        self.personal_companion.set_setting("initiative_last_nudge_ts", str(time.time()))
                        if decision.trigger == "session_return": self._phase2_session_return_consumed = True
                        if decision.trigger != "session_return": self._phase2_session_return_consumed = True
                        if decision.trigger == "wind_down": self._phase2_wind_down_day = datetime.date.today().isoformat()
                    self.last_reply = text
                    self.face_mode = "curious"
                    self.face_expression_until = time.perf_counter() + 2.2
                    self._status("Buddy started a conversation")
                    if self.voice_enabled and text:
                        threading.Thread(target=self._speak, args=(text,), daemon=True).start()
                    if self.root.state() == "withdrawn" and self.tray_icon:
                        try: self.tray_icon.notify(text[:180], "Study Buddy")
                        except Exception: pass
                elif kind == "set_chat_timer":
                    _, seconds, label = item
                    timer_event = self.intelligence_monitor.start(
                        "focus_timer", "Start focus timer", "Timer", "Requested duration recorded; label omitted"
                    )
                    self.timer_running = False
                    if self.timer_job is not None:
                        try: self.root.after_cancel(self.timer_job)
                        except Exception: pass
                        self.timer_job = None
                    self.timer_remaining = max(1, int(seconds))
                    self.timer_label_text = label
                    self.timer_running = True
                    self._timer_tick()
                    self.intelligence_monitor.finish(timer_event, "success")
                    self._status(f"{label} started from chat · {self._format_time(seconds)}")
                elif kind == "consent_candidates":
                    _, queued = item
                    self._consent_event_queued = False
                    router = getattr(self.core, "intent_router", None)
                    # Re-check every gate on the UI thread; a new user turn wins over the queued event.
                    try:
                        from companion.consent_dialog import render_batch, should_deliver_consent
                        now_delivery=time.time(); today_delivery=datetime.date.today().isoformat()
                        last_prompt_delivery=float(self.personal_companion.setting("consent_last_prompt_ts", "0") or 0)
                        prompt_day_delivery=self.personal_companion.setting("consent_prompt_day", "")
                        prompt_count_delivery=int(self.personal_companion.setting("consent_prompt_count", "0") or 0) if prompt_day_delivery==today_delivery else 0
                        pause_delivery=float(self.personal_companion.setting("consent_pause_until", "0") or 0)
                        tier_delivery=str(self.personal_companion.setting("tier_facts", "1")).lower() not in ("0", "false", "off", "no")
                        allowed=should_deliver_consent(
                            now=now_delivery,session_started_at=self.session_started_at,
                            last_interaction_ts=max(self.last_interaction_ts,self.last_turn_completed_ts),last_prompt_ts=last_prompt_delivery,
                            prompt_count_today=prompt_count_delivery,pause_until=pause_delivery,
                            idle_seconds=getattr(self.core,"companion_config",{}).get("consent_idle_seconds",120),
                            cooldown_seconds=getattr(self.core,"companion_config",{}).get("consent_prompt_cooldown_seconds",1800),
                            daily_cap=getattr(self.core,"companion_config",{}).get("consent_daily_cap",3),
                            facts_enabled=tier_delivery,proactive_enabled=self.settings.get("proactive_enabled",True),
                            busy=self.busy,worker_busy=self.proactive_worker_running,
                            dialog_pending=bool(router is None or router.has_pending or
                                                (self.core.relationship_builder and self.core.relationship_builder.phase)),
                            quiet=self._is_quiet(datetime.datetime.now().strftime("%H:%M")))
                        if not allowed: continue
                        fresh = {x["id"]: x for x in self.personal_companion.queued_candidates(limit=5)}
                        candidates = [fresh[x.get("id")] for x in queued if x.get("id") in fresh
                                      and (not fresh[x.get("id")].get("last_prompted_ts") or now_delivery-float(fresh[x.get("id")]["last_prompted_ts"]) >= 1800)][:3]
                        if not candidates: continue
                        prompt = render_batch(candidates)
                        # Make the prompt visible and part of history before installing its dialog state.
                        self._append(self.core.buddy_name, prompt, "buddy")
                        self.core.history.append({"role": "assistant", "content": prompt})
                        self.memory.add_turn("assistant", prompt)
                        router.start_consent_fact(candidates)
                        now_prompt = time.time(); today = datetime.date.today().isoformat()
                        self.personal_companion.mark_candidates_prompted([x["id"] for x in candidates], now=now_prompt)
                        self.personal_companion.set_setting("consent_last_prompt_ts", str(now_prompt))
                        old_day = self.personal_companion.setting("consent_prompt_day", "")
                        old_count = int(self.personal_companion.setting("consent_prompt_count", "0") or 0) if old_day == today else 0
                        self.personal_companion.set_setting("consent_prompt_day", today)
                        self.personal_companion.set_setting("consent_prompt_count", str(old_count+1))
                        self.last_reply = prompt
                        self._status("Memory consent question · reply yes, no, or skip")
                    except Exception:
                        # No candidate status or prompt timestamp is changed if delivery fails.
                        pass
                elif kind == "companion_followup":
                    _, followup_id, _text = item
                    # Claim/ack delivery: never mark a prompt delivered until it is
                    # actually visible and its deterministic dialog state is installed.
                    if (self.busy or self.proactive_worker_running or self.core.intent_router is None
                            or self.core.intent_router.has_pending
                            or (self.core.relationship_builder and self.core.relationship_builder.phase)):
                        self.personal_companion.release_followup(followup_id)
                        continue
                    try:
                        text = self.core.start_relationship_checkin()
                        if not text:
                            self.personal_companion.acknowledge_followup(followup_id)
                            continue
                        self.core.history.append({"role": "assistant", "content": text})
                        if self.memory:
                            self.memory.add_turn("assistant", text)
                        self._append(self.core.buddy_name, text, "buddy")
                        self.personal_companion.acknowledge_followup(followup_id)
                        self.last_reply = text
                        self._status("Getting-to-know-you prompt · answer, skip, or chat normally")
                    except Exception:
                        self.personal_companion.release_followup(followup_id)
                elif kind == "reminder_due":
                    _, text = item
                    self._append("Buddy", "Quick reminder: " + text, "buddy")
                    self.face_mode = "curious"
                    self.face_expression_until = time.perf_counter() + 2.0
                    if self.root.state() == "withdrawn" and self.tray_icon:
                        try: self.tray_icon.notify(str(text)[:180], "Study Buddy reminder")
                        except Exception: pass
                    if self.voice_enabled:
                        threading.Thread(target=self._speak, args=("Quick reminder. " + text,), daemon=True).start()
                elif kind == "face_speaking":
                    self.face_speaking = bool(item[1])
                    if self.face_speaking:
                        self.activity_mode = "speaking"
                    elif self.activity_mode == "speaking":
                        self.activity_mode = "idle"
                    if self.face_speaking:
                        self.face_mode = "speaking"
                    elif self.face_mode == "speaking":
                        self.face_mode = "neutral"
                        self.face_expression_until = 0.0
                elif kind == "voice_started":
                    self.activity_mode = "listening"
                    self.voice_levels = [0.0] * len(self.voice_levels)
                    self.voice_frequency = 0
                elif kind == "voice_level":
                    _, level, frequency = item
                    self.voice_levels.append(max(0.0, min(1.0, float(level))))
                    self.voice_levels = self.voice_levels[-28:]
                    self.voice_frequency = int(frequency or 0)
                elif kind == "voice_text":
                    _,text=item
                    self.activity_mode = "idle"
                    if text: self.entry.delete("1.0","end"); self.entry.insert("1.0",text); self._status("Speech recognized. Press Send to submit.")
                    else: self._status("No speech detected. Try again closer to the microphone.")
                elif kind=="error":
                    self.activity_mode = "idle"
                    messagebox.showerror("Study Buddy",item[1]); self._status("Voice feature unavailable")
                elif kind == "memory_review_progress":
                    self._memory_review_status = str(item[1])
                    if not self.busy:
                        self._status("My World memory review · " + self._memory_review_status)
                elif kind=="status": self._status(item[1])
                elif kind=="nudge": self.root.after(0,lambda msg=item[1]:messagebox.showinfo("Study Buddy",msg))
        except queue.Empty: pass
        if not self.closing: self.root.after(100,self._poll_events)

    def _check_ollama(self):
        self.ollama_available = bool(self.core.llm.is_alive())
        if self.ollama_available:
            self.events.put(("status", "Ollama is available"))
        else:
            self.events.put(("status", "Ollama not detected. Start Ollama first."))

    def _periodic_ollama_check(self):
        """Refresh the local AI health state without blocking the Tk event loop."""
        if self.closing:
            return
        if not self._ollama_probe_running:
            self._ollama_probe_running = True
            def probe():
                try:
                    self.ollama_available = bool(self.core.llm.is_alive())
                finally:
                    self._ollama_probe_running = False
            threading.Thread(target=probe, daemon=True).start()
        if not self.closing:
            self.root.after(15000, self._periodic_ollama_check)

    def _attach_intelligence_hooks(self):
        """Connect the current ChatCore/Memory instances to observability and self-awareness."""
        self.core.capability_context_provider = self._capability_context
        self.core.capability_list_provider = self._capability_list
        self.intelligence_monitor.instrument(
            self.core.llm, "chat", "local_ai", "Ollama chat inference", "Ollama",
            "Legacy local model request; prompt contents omitted",
        )
        self.intelligence_monitor.instrument(
            self.core.llm, "chat_completion", "local_ai", "Ollama tool-aware inference", "Ollama",
            "Structured local model request; prompts and tool arguments omitted",
        )
        memory_tools = {
            "add_task": ("tasks", "Add task", "Task saved to local memory"),
            "list_tasks": ("tasks", "List tasks", "Task list queried"),
            "complete_task": ("tasks", "Complete task", "Task completion requested"),
            "delete_task": ("tasks", "Delete task", "Task deletion requested"),
            "add_reminder": ("reminders", "Add reminder", "Reminder saved; text omitted"),
            "list_reminders": ("reminders", "List reminders", "Reminder list queried"),
            "claim_due_reminders": ("reminders", "Fetch due reminders", "Due reminder text omitted"),
            "add_note": ("memory", "Save memory note", "Note content omitted"),
            "add_fact": ("memory", "Save memory fact", "Fact content omitted"),
            "list_facts": ("memory", "Retrieve memory facts", "Fact content omitted"),
            "search_notes": ("memory", "Search memory notes", "Search query and results omitted"),
        }
        for method, (tool_id, label, summary) in memory_tools.items():
            self.intelligence_monitor.instrument(
                self.memory, method, tool_id, label, "Memory", summary
            )
        self.intelligence_monitor.instrument(
            self, "open_camera", "camera_preview", "Camera launch request", "Camera",
            "Consent-gated preview request; current preview state is checked separately",
        )
        if hasattr(self, "lifecycle"):
            self.intelligence_monitor.instrument(
                self.lifecycle, "consolidate_daily", "memory", "Consolidate memory", "Lifecycle",
                "Local memory maintenance; conversation contents are not copied to telemetry",
            )

    def _capability_context(self):
        """Authoritative tool/capability state supplied to Milo on each model turn."""
        return build_capability_context(self.capability_registry)

    def _capability_list(self):
        """Human-readable capability inventory used for deterministic self-description."""
        labels = {
            "active": "active", "available": "ready", "disabled": "disabled",
            "unavailable": "unavailable", "checking": "checking", "degraded": "limited",
            "error": "error",
        }
        lines = []
        for item in self.capability_registry.snapshot():
            lines.append(f"• {item['name']} — {labels.get(item['status'], item['status'])}: {item['detail']}")
        lines.append("I cannot inspect screen pixels, browser page contents, or raw camera images. Camera expression estimates, when available, are computed locally and are only uncertain visual cues.")
        return "\n".join(lines)

    def open_intelligence_center(self):
        existing = getattr(self, "_intelligence_dashboard", None)
        if existing is not None:
            try:
                if existing.window.winfo_exists():
                    existing.window.deiconify()
                    existing.window.lift()
                    existing.refresh()
                    return
            except Exception:
                pass
        self._intelligence_dashboard = IntelligenceDashboard(
            self.root, self.capability_registry, self.intelligence_monitor, self.memory,
            model_name=self.settings.get("model_chat", "llama3.2:3b"),
            intent_dictionary_path=os.path.join(ROOT, "config", "intent_dictionary.json"),
            core=self.core, settings=self.settings,
        )

    def toggle_face(self):
        if self.face_window and self.face_window.winfo_exists():
            self.face_window.destroy()
            self.face_window = None
            self.face_canvas = None
            return
        win = tk.Toplevel(self.root)
        self.face_window = win
        win.title("Study Buddy Face")
        win.geometry("280x310+900+120")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg="#0b1220")
        win.overrideredirect(True)
        bar = tk.Frame(win, bg="#182438", height=28)
        bar.pack(fill="x")
        tk.Label(bar, text="STUDY BUDDY", bg="#182438", fg="#d8f4ff",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=9)
        tk.Button(bar, text="×", command=self._close_face, bg="#29374b", fg="white",
                  activebackground="#475569", activeforeground="white",
                  relief="flat", bd=0, width=3).pack(side="right")
        bar.bind("<ButtonPress-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        self.face_canvas = tk.Canvas(win, width=280, height=282, bg="#0b1220", highlightthickness=0)
        self.face_canvas.pack(fill="both", expand=True)
        self._draw_face()

    def _close_face(self):
        if self.face_window:
            self.face_window.destroy()
        self.face_window = None
        self.face_canvas = None

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.face_window.winfo_x()
        self._drag_y = e.y_root - self.face_window.winfo_y()

    def _drag_move(self, e):
        self.face_window.geometry(f"+{e.x_root-self._drag_x}+{e.y_root-self._drag_y}")

    def _animate_face(self):
        if self.face_canvas and self.face_canvas.winfo_exists():
            now = time.perf_counter()
            # Expressions settle back to a calm neutral face after a short reaction.
            if not self.face_speaking and self.face_expression_until and now >= self.face_expression_until:
                self.face_mode = "neutral"
                self.face_expression_until = 0.0
            self._draw_face()
        if not self.closing:
            self.root.after(80 if (self.face_canvas and self.face_canvas.winfo_exists()) else 500, self._animate_face)  # ~12 FPS; near-idle when the face window is closed

    def _draw_face(self):
        c = self.face_canvas
        if not c:
            return
        c.delete("all")
        t = time.perf_counter() - self.face_started_at
        mode = self.face_mode
        # Subtle breathing/floating is always on, including neutral/idle.
        bob = math.sin(t * 1.45) * 2.0
        cx, cy = 140, 128 + bob
        blink_phase = t % 4.7
        blinking = blink_phase > 4.48
        blink_amount = max(0.08, abs((blink_phase - 4.595) / 0.115)) if blinking else 1.0
        eye_h = 25 * min(1.0, blink_amount)
        gaze_x = math.sin(t * 0.72) * 3.0
        gaze_y = math.sin(t * 0.51) * 1.4
        if mode == "thinking":
            gaze_x += math.sin(t * 3.2) * 4
            gaze_y -= 2
        elif mode == "curious":
            gaze_x += 2
        # Outer glow rings, shell and faceplate.
        c.create_oval(cx-108, cy-108, cx+108, cy+108, fill="#101e32", outline="#1f4562", width=2)
        c.create_oval(cx-98, cy-98, cx+98, cy+98, fill="#1a2a40", outline="#7dd3fc", width=3)
        c.create_arc(cx-91, cy-91, cx+91, cy+91, start=205, extent=130,
                     style="arc", outline="#315a78", width=2)
        c.create_oval(cx-82, cy-78, cx+82, cy+82, fill="#23364d", outline="#35536d", width=1)
        # Small status lights add detail without making the face busy.
        for dx, dy in [(-72,-64), (72,-64), (-78,0), (78,0)]:
            c.create_oval(cx+dx-2, cy+dy-2, cx+dx+2, cy+dy+2, fill="#67e8f9", outline="")

        # Brows change shape with the emotional state.
        brow_color = "#a5f3fc"
        if mode in ("concerned", "sad"):
            c.create_line(cx-65,cy-39,cx-37,cy-31,fill="#fda4af",width=4,smooth=True,capstyle="round")
            c.create_line(cx+37,cy-31,cx+65,cy-39,fill="#fda4af",width=4,smooth=True,capstyle="round")
        elif mode in ("happy", "excited"):
            c.create_arc(cx-68,cy-48,cx-27,cy-25,start=15,extent=150,style="arc",outline=brow_color,width=3)
            c.create_arc(cx+27,cy-48,cx+68,cy-25,start=15,extent=150,style="arc",outline=brow_color,width=3)
        elif mode == "thinking":
            c.create_line(cx-67,cy-40,cx-36,cy-46,fill="#a5f3fc",width=3,smooth=True)
            c.create_line(cx+36,cy-46,cx+67,cy-40,fill="#a5f3fc",width=3,smooth=True)
        elif mode == "curious":
            c.create_line(cx-66,cy-43,cx-36,cy-37,fill=brow_color,width=3,smooth=True)
            c.create_line(cx+36,cy-49,cx+66,cy-39,fill=brow_color,width=3,smooth=True)
        else:
            c.create_line(cx-65,cy-39,cx-37,cy-41,fill=brow_color,width=3,smooth=True)
            c.create_line(cx+37,cy-41,cx+65,cy-39,fill=brow_color,width=3,smooth=True)

        # Large eyes with sclera, iris, pupils and moving highlights.
        for ex in (cx-46, cx+46):
            ey = cy-17 + gaze_y
            c.create_oval(ex-25, ey-16, ex+25, ey+16, fill="#d9f8ff", outline="#7dd3fc", width=2)
            if eye_h < 24:
                c.create_rectangle(ex-27, ey-17, ex+27, ey-17+max(1, 25-eye_h), fill="#23364d", outline="")
            iris_x = ex + gaze_x
            c.create_oval(iris_x-11, ey-12, iris_x+11, ey+12, fill="#22b8d5", outline="#0e7490", width=1)
            c.create_oval(iris_x-6, ey-8, iris_x+6, ey+8, fill="#082f49", outline="")
            c.create_oval(iris_x-4, ey-8, iris_x, ey-4, fill="white", outline="")
            c.create_oval(iris_x+3, ey+3, iris_x+6, ey+6, fill="#a5f3fc", outline="")
            # Eyelid comes down during blinks; a narrow line remains at full blink.
            if eye_h < 24:
                c.create_rectangle(ex-26, ey-18, ex+26, ey-18+(24-eye_h), fill="#23364d", outline="")

        # Cheek glow varies by expression.
        cheek = "#38506b" if mode not in ("happy", "excited") else "#6a526b"
        c.create_oval(cx-76,cy+18,cx-57,cy+28,fill=cheek,outline="")
        c.create_oval(cx+57,cy+18,cx+76,cy+28,fill=cheek,outline="")
        mouth_y = cy + 43
        mouth_color = "#a5f3fc"
        if mode in ("concerned", "sad"):
            c.create_arc(cx-29,mouth_y-2,cx+29,mouth_y+23,start=25,extent=130,style="arc",outline="#fda4af",width=4)
        elif mode == "thinking":
            c.create_line(cx-19,mouth_y+7,cx+17,mouth_y+3,fill=mouth_color,width=4,capstyle="round")
            c.create_oval(cx+19,mouth_y+1,cx+23,mouth_y+5,fill=mouth_color,outline="")
        elif mode == "curious":
            c.create_oval(cx-7,mouth_y-3,cx+9,mouth_y+15,outline=mouth_color,width=3)
        elif mode in ("happy", "excited"):
            c.create_arc(cx-34,mouth_y-13,cx+34,mouth_y+27,start=205,extent=130,style="arc",outline=mouth_color,width=4)
            if mode == "excited":
                c.create_arc(cx-22,mouth_y-3,cx+22,mouth_y+20,start=200,extent=140,style="arc",outline="#67e8f9",width=2)
        elif mode == "sleepy":
            c.create_line(cx-18,mouth_y+5,cx+18,mouth_y+5,fill=mouth_color,width=3,capstyle="round")
        elif mode == "speaking" or self.face_speaking:
            # Mouth opens/closes rhythmically while pyttsx3 is actually speaking.
            amp = abs(math.sin(t * 13.0))
            open_h = 4 + amp * (14 if int(t*13) % 4 else 19)
            c.create_oval(cx-15,mouth_y-open_h/2,cx+15,mouth_y+open_h/2,
                          fill="#071827",outline=mouth_color,width=3)
            if open_h > 11:
                c.create_arc(cx-9,mouth_y-2,cx+9,mouth_y+open_h/2-1,start=190,extent=160,
                             style="arc",outline="#fb7185",width=2)
        else:
            # Calm neutral smile; subtle curve movement keeps the idle face alive.
            smile = 8 + (math.sin(t*1.2)+1)*2
            c.create_arc(cx-28,mouth_y-smile,cx+28,mouth_y+20,start=205,extent=130,
                         style="arc",outline=mouth_color,width=3)
        # Tiny chin/voice indicator and expression label.
        c.create_oval(cx-4,cy+82,cx+4,cy+86,fill="#46627c",outline="")
        label = "TALKING" if self.face_speaking else mode.upper()
        c.create_text(cx, 265, text=label, fill="#9fb4c8", font=("Segoe UI", 8, "bold"))

    def _animate_activity(self):
        """Draw a live microphone waveform and a lightweight thinking animation."""
        if not hasattr(self, "activity_canvas") or not self.activity_canvas.winfo_exists():
            return
        canvas = self.activity_canvas
        canvas.delete("wave")
        width = max(240, canvas.winfo_width())
        height = 34
        mode = self.activity_mode
        if mode == "listening":
            label = f"LISTENING  ·  {self.voice_frequency} Hz" if self.voice_frequency else "LISTENING  ·  SPEAK NOW"
            color = "#38bdf8"
            levels = self.voice_levels[-28:]
        elif mode == "thinking":
            label = "THINKING  ·  LOCAL MODEL"
            color = "#a78bfa"
            phase = time.perf_counter() * 5.5
            levels = [0.12 + 0.72 * abs(math.sin(phase + i * 0.47)) for i in range(28)]
        elif mode == "speaking":
            label = "SPEAKING"
            color = "#34d399"
            phase = time.perf_counter() * 7
            levels = [0.10 + 0.65 * abs(math.sin(phase + i * 0.58)) for i in range(28)]
        else:
            label = "READY"
            color = "#64748b"
            levels = [0.035] * 28
        canvas.create_text(10, 7, text=label, anchor="w", fill=color,
                           font=("Segoe UI", 8, "bold"), tags=("wave",))
        left = min(205, max(130, width * 0.43))
        right = width - 12
        count = len(levels)
        step = max(2.0, (right - left) / max(1, count))
        baseline = 22
        for i, level in enumerate(levels):
            x = left + i * step
            amp = 2 + max(0.0, min(1.0, level)) * 10
            canvas.create_line(x, baseline - amp, x, baseline + amp,
                               fill=color, width=2, capstyle="round", tags=("wave",))
        if mode == "thinking":
            # Moving dots provide an obvious loading cue even if bars are subtle.
            dot = int(time.perf_counter() * 3) % 3
            canvas.create_text(width - 10, 7, text="." * (dot + 1), anchor="e",
                               fill=color, font=("Segoe UI", 10, "bold"), tags=("wave",))
        # Redraw rarely when idle so the UI thread stays free while the model is generating.
        self.root.after(500 if mode not in ("listening", "thinking", "speaking") else 100, self._animate_activity)

    def voice_input(self):
        if self.busy: return
        self._status("Listening for up to 8 seconds…")
        threading.Thread(target=self._voice_worker,daemon=True).start()
    def _voice_worker(self):
        self.voice_listening = True
        monitor_event = self.intelligence_monitor.start(
            "voice_input", "Voice transcription", "Voice", "Audio content is not logged"
        )
        voice_ok = False
        voice_error = ""
        try:
            import sounddevice as sd
            import numpy as np
            from faster_whisper import WhisperModel
            self.events.put(("voice_started",))
            chunks = []
            started = time.monotonic()

            def audio_callback(indata, frames, time_info, status):
                chunk = indata[:, 0].copy()
                chunks.append(chunk)
                rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
                frequency = 0
                if len(chunk) > 32 and rms > 0.002:
                    spectrum = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
                    frequencies = np.fft.rfftfreq(len(chunk), d=1.0 / 16000)
                    valid = np.where((frequencies >= 70) & (frequencies <= 3500))[0]
                    if len(valid):
                        frequency = int(frequencies[valid[np.argmax(spectrum[valid])]])
                self.events.put(("voice_level", min(1.0, rms * 12.0), frequency))

            with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                                blocksize=1024, callback=audio_callback):
                while time.monotonic() - started < 8.0:
                    time.sleep(0.04)
            if not chunks:
                self.events.put(("voice_text", ""))
                return
            audio = np.concatenate(chunks)
            with self._whisper_lock:
                if self._whisper_model is None:
                    self.events.put(("status", "Loading speech model for first use…"))
                    self._whisper_model = WhisperModel(self.settings.get("speech_model", "tiny"),
                                                       device="cpu", compute_type="int8")
                whisper = self._whisper_model
            segments, _ = whisper.transcribe(audio, language="en", vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            self.events.put(("voice_text", text))
            voice_ok = bool(text)
            if not voice_ok:
                voice_error = "No speech was detected"
        except Exception as e:
            voice_error = f"{type(e).__name__}: {e}"
            self.events.put(("error", "Voice input needs a working microphone and optional dependencies. Run install_voice.bat and check Windows microphone permission. Details: " + str(e)))
        finally:
            self.voice_listening = False
            self.intelligence_monitor.finish(monitor_event, "success" if voice_ok else "failed", voice_error)

    def _speak(self, text):
        self.events.put(("face_speaking", True))
        event_id = self.intelligence_monitor.start(
            "voice_output", "Text-to-speech", "Voice", "Speech text omitted from telemetry"
        )
        speech_ok = False
        speech_error = ""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            speech_ok = True
        except Exception as exc:
            speech_error = f"{type(exc).__name__}: {exc}"
            self.events.put(("status", "Text-to-speech unavailable: " + str(exc)))
        finally:
            self.intelligence_monitor.finish(event_id, "success" if speech_ok else "failed", speech_error)
            self.events.put(("face_speaking", False))

    def speak_last(self):
        text=getattr(self,"last_reply","")
        if text: threading.Thread(target=self._speak,args=(text,),daemon=True).start()
        else: self._status("No reply to speak yet")

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Study Buddy settings and personality")
        win.geometry("700x820")
        win.transient(self.root)
        # Scrollable settings/personality panel so every control remains reachable
        # on smaller screens and at high Windows display scaling.
        container = ttk.Frame(win)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        frame = ttk.Frame(canvas, padding=14)
        frame_window = canvas.create_window((0, 0), window=frame, anchor="nw")

        def _settings_frame_configured(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _settings_canvas_configured(event):
            canvas.itemconfigure(frame_window, width=event.width)

        frame.bind("<Configure>", _settings_frame_configured)
        canvas.bind("<Configure>", _settings_canvas_configured)

        def _settings_mousewheel(event):
            # Only scroll this panel while the pointer is over this settings window.
            try:
                if event.widget.winfo_toplevel() is not win:
                    return
            except tk.TclError:
                return
            # Windows uses delta multiples of 120; Linux wheel uses buttons 4/5.
            if getattr(event, "delta", 0):
                canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")

        def _unbind_settings_wheel(event=None):
            if event is not None and getattr(event, "widget", None) is not win:
                return
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                canvas.unbind_all(sequence)

        canvas.bind_all("<MouseWheel>", _settings_mousewheel)
        canvas.bind_all("<Button-4>", _settings_mousewheel)
        canvas.bind_all("<Button-5>", _settings_mousewheel)
        win.bind("<Destroy>", _unbind_settings_wheel, add="+")

        ttk.Label(frame, text="Local model (must already exist in Ollama)").pack(anchor="w")
        model = tk.StringVar(value=self.settings.get("model_chat", "llama3.2:3b"))
        ttk.Entry(frame, textvariable=model).pack(fill="x", pady=(2, 8))

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Keep AI model loaded in RAM", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Longer keeps chats quicker after idle, but uses more RAM. Unlimited keeps it loaded until Ollama exits or unloads it.", wraplength=620).pack(anchor="w", pady=(2, 5))
        keep_alive_mode = tk.StringVar(value=self.settings.get("model_keep_alive_mode", "30 minutes"))
        keep_alive_values = ("30 minutes", "1 hour", "Custom", "Unlimited")
        keep_alive_row = ttk.Frame(frame); keep_alive_row.pack(fill="x", pady=(0, 4))
        ttk.Label(keep_alive_row, text="Keep loaded for").pack(side="left")
        keep_alive_combo = ttk.Combobox(keep_alive_row, textvariable=keep_alive_mode, values=keep_alive_values, state="readonly", width=16)
        keep_alive_combo.pack(side="left", padx=10)
        custom_minutes = tk.StringVar(value=str(self.settings.get("model_keep_alive_custom_minutes", 30)))
        ttk.Label(keep_alive_row, text="Custom minutes:").pack(side="left", padx=(8, 3))
        custom_minutes_entry = ttk.Entry(keep_alive_row, textvariable=custom_minutes, width=8)
        custom_minutes_entry.pack(side="left")
        def update_custom_state(_event=None):
            state = "normal" if keep_alive_mode.get() == "Custom" else "disabled"
            custom_minutes_entry.configure(state=state)
        keep_alive_combo.bind("<<ComboboxSelected>>", update_custom_state)
        update_custom_state()

        voice = tk.BooleanVar(value=self.voice_enabled)
        awareness = tk.BooleanVar(value=self.awareness_enabled)
        proactive = tk.BooleanVar(value=self.settings.get("proactive_enabled", True))
        ttk.Checkbutton(frame, text="Speak replies automatically (English TTS)", variable=voice).pack(anchor="w", pady=2)
        ttk.Checkbutton(frame, text="Enable local desktop awareness (app, idle time and privacy-filtered window title)", variable=awareness).pack(anchor="w", pady=2)
        ttk.Checkbutton(frame, text="Allow Luna to start proactive check-ins (also controlled by the chat switch)", variable=proactive).pack(anchor="w", pady=2)
        ttk.Label(frame, text="Window check-in timing", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 0))
        timing_vars = {}
        for _key, _label, _default, _low, _high in (
                ("window_checkin_new_seconds", "Comment on a NEW window after it stays open (seconds)", 15, 5, 3600),
                ("window_checkin_same_minutes", "Check again while I stay on the SAME window, every (minutes)", 10, 1, 240),
                ("window_checkin_return_minutes", "Check again when I RETURN to a window, after (minutes)", 5, 1, 240),
                ("window_checkin_min_gap_seconds", "Minimum gap between any two window check-ins (seconds)", 90, 0, 3600),
                ("window_checkin_max_followups", "Max follow-ups per window (0 = unlimited)", 0, 0, 100)):
            _row = ttk.Frame(frame); _row.pack(fill="x", pady=1)
            ttk.Label(_row, text=_label).pack(side="left")
            _var = tk.StringVar(value=str(self.settings.get(_key, _default)))
            ttk.Spinbox(_row, from_=_low, to=_high, width=7, textvariable=_var).pack(side="right")
            timing_vars[_key] = (_var, _low, _high)
        llm_text = tk.BooleanVar(value=bool(self.settings.get("window_checkin_llm_text", True)))
        ttk.Checkbutton(frame, text="Let the AI write each window check-in in its own words (not a fixed template)",
                        variable=llm_text).pack(anchor="w", pady=2)
        autostart = tk.BooleanVar(value=self.settings.get("auto_start_enabled", True))
        background_agent = tk.BooleanVar(value=self.settings.get("background_agent_enabled", True))
        ttk.Checkbutton(frame, text="Start Study Buddy when I sign in to Windows", variable=autostart).pack(anchor="w", pady=2)
        ttk.Checkbutton(frame, text="Keep reminders active after closing the window (background agent)", variable=background_agent).pack(anchor="w", pady=2)
        late_night = tk.BooleanVar(value=self.settings.get("late_night_nudge_enabled", False))
        ttk.Checkbutton(frame, text="Allow one optional late-night wind-down notification", variable=late_night).pack(anchor="w", pady=2)
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Initiative and learning", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tier_loops = tk.BooleanVar(value=str(self.personal_companion.setting("tier_loops", "1")).lower() not in ("0", "false", "off", "no"))
        tier_rhythm = tk.BooleanVar(value=str(self.personal_companion.setting("tier_rhythm", "0")).lower() not in ("0", "false", "off", "no"))
        ttk.Checkbutton(frame, text="Remind me about deadlines I confirmed", variable=tier_loops).pack(anchor="w", pady=2)
        ttk.Checkbutton(frame, text="Learn my usual study times (stored on this PC, no window titles)", variable=tier_rhythm).pack(anchor="w", pady=2)
        rhythm_profile=self.core.rhythm_tracker.profile()
        if rhythm_profile.confidence>0 and rhythm_profile.weekday_start is not None and rhythm_profile.weekday_end is not None:
            suggestion=f"Suggested quiet hours from weekday patterns: {rhythm_profile.weekday_end:02d}:00–{rhythm_profile.weekday_start:02d}:00 (suggestion only; not applied)."
        else:
            suggestion="Quiet-hours suggestions will appear after at least five distinct active days and enough title-free samples. Nothing is applied automatically."
        ttk.Label(frame,text=suggestion,wraplength=620).pack(anchor="w",pady=(1,5))
        daily_cap = tk.StringVar(value=str(max(0,min(5,int(self.settings.get("initiative_daily_cap",3))))) )
        cap_row=ttk.Frame(frame); cap_row.pack(fill="x",pady=3)
        ttk.Label(cap_row,text="Daily initiative nudge cap (0–5)").pack(side="left")
        ttk.Spinbox(cap_row,from_=0,to=5,textvariable=daily_cap,width=5).pack(side="left",padx=8)
        trigger_vars={}
        for trigger,label in (("open_loop_due","Deadline nudges"),("usual_study_start","Usual study-time nudges"),("session_return","Session-return check-ins"),("wind_down","Wind-down notes"),("title_clue","Window-title check-ins")):
            trigger_vars[trigger]=tk.BooleanVar(value=str(self.personal_companion.setting("trigger_enabled_"+trigger,"1")).lower() not in ("0","false","off","no"))
            ttk.Checkbutton(frame,text=label,variable=trigger_vars[trigger]).pack(anchor="w",pady=1)
        def delete_patterns():
            from companion.privacy import PrivacyService
            count=PrivacyService(self.memory).delete_rhythm()
            messagebox.showinfo("Patterns deleted",f"Deleted {count} learned pattern bucket(s).",parent=win)
        def delete_nudges():
            from companion.privacy import PrivacyService
            counts=PrivacyService(self.memory,self.proactive_limiter).delete_nudge_history()
            messagebox.showinfo("Nudge history deleted",f"Deleted {counts['companion_events']} event(s) and {counts['trigger_stats']} trigger statistic(s).",parent=win)
        delete_row=ttk.Frame(frame); delete_row.pack(fill="x",pady=3)
        ttk.Button(delete_row,text="Delete learned patterns",command=delete_patterns).pack(side="left",padx=(0,6))
        ttk.Button(delete_row,text="Delete nudge history",command=delete_nudges).pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Camera privacy and access", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Camera starts only after explicit consent. Frames stay local; only text summaries of facial movement may be sent to your local Llama model. No images/video are saved or sent to the model.", wraplength=620).pack(anchor="w", pady=(2, 5))
        webcam_enabled = tk.BooleanVar(value=bool(self.settings.get("webcam_enabled", False)))
        ttk.Checkbutton(frame, text="Allow camera requests from chat (still requires consent each time)", variable=webcam_enabled).pack(anchor="w", pady=2)
        camera_cooldown = tk.StringVar(value=str(self.settings.get("camera_min_interval_minutes", 5)))
        camera_rate = tk.StringVar(value=str(self.settings.get("camera_frame_interval_ms", 200)))
        camera_row = ttk.Frame(frame); camera_row.pack(fill="x", pady=3)
        ttk.Label(camera_row, text="Minimum time between camera starts (minutes)").pack(side="left")
        ttk.Combobox(camera_row, textvariable=camera_cooldown, values=("0", "1", "5", "15", "30"), state="readonly", width=6).pack(side="left", padx=8)
        ttk.Label(camera_row, text="Preview refresh (ms)").pack(side="left", padx=(12, 4))
        ttk.Combobox(camera_row, textvariable=camera_rate, values=("100", "200", "500"), state="readonly", width=6).pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Personal memory consent", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        facts_tier = tk.BooleanVar(value=str(self.personal_companion.setting("tier_facts", "1")).lower() not in ("0", "false", "off", "no"))
        ttk.Checkbutton(frame, text="Let Study Buddy remember details I confirm and use them in chat", variable=facts_tier).pack(anchor="w", pady=2)
        ttk.Label(frame, text="Unconfirmed candidates are never used in replies. You can review them in My World.", wraplength=620).pack(anchor="w", pady=(0,5))

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Memory data protection", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Optional Windows EFS encryption protects the profile folder at rest on supported Windows editions and NTFS drives. Export an encrypted Study Buddy backup first and keep your Windows account/certificate safe.", wraplength=620).pack(anchor="w", pady=(2, 5))
        efs_row = ttk.Frame(frame); efs_row.pack(fill="x", pady=(0, 6))
        ttk.Button(efs_row, text="Encrypt profile folder with Windows EFS…", command=self.encrypt_profile_folder).pack(side="left")
        ttk.Button(efs_row, text="Check memory.db encryption", command=self.check_memory_encryption).pack(side="left", padx=8)

        quiet = ttk.Frame(frame); quiet.pack(fill="x", pady=6)
        ttk.Label(quiet, text="Quiet hours (24-hour clock)").pack(side="left")
        q1 = tk.StringVar(value=self.settings.get("quiet_start", "23:00"))
        q2 = tk.StringVar(value=self.settings.get("quiet_end", "07:00"))
        ttk.Entry(quiet, textvariable=q1, width=8).pack(side="left", padx=(12, 4))
        ttk.Label(quiet, text="to").pack(side="left")
        ttk.Entry(quiet, textvariable=q2, width=8).pack(side="left", padx=4)
        late_row = ttk.Frame(frame); late_row.pack(fill="x", pady=4)
        ttk.Label(late_row, text="Late-night notification time").pack(side="left")
        late_time = tk.StringVar(value=self.settings.get("late_night_nudge_time", "22:30"))
        ttk.Entry(late_row, textvariable=late_time, width=8).pack(side="left", padx=10)
        strict_row = ttk.Frame(frame); strict_row.pack(fill="x", pady=4)
        ttk.Label(strict_row, text="Disagreement strength").pack(side="left")
        strictness = tk.StringVar(value=self.settings.get("strictness", "medium"))
        ttk.Combobox(strict_row, textvariable=strictness, values=("low", "medium", "high"), state="readonly", width=10).pack(side="left", padx=10)

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="Personality (editable; fixed safety rules stay separate)", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        char = dict(self.core.character)
        traits = tk.StringVar(value=", ".join(char.get("traits", [])))
        values = tk.StringVar(value=", ".join(char.get("values", [])))
        likes = tk.StringVar(value=", ".join(char.get("likes", [])))
        dislikes = tk.StringVar(value=", ".join(char.get("dislikes", [])))
        ttk.Label(frame, text="Traits (comma-separated)").pack(anchor="w")
        ttk.Entry(frame, textvariable=traits).pack(fill="x", pady=(1, 5))
        ttk.Label(frame, text="Values (comma-separated)").pack(anchor="w")
        ttk.Entry(frame, textvariable=values).pack(fill="x", pady=(1, 5))
        ttk.Label(frame, text="Likes (comma-separated)").pack(anchor="w")
        ttk.Entry(frame, textvariable=likes).pack(fill="x", pady=(1, 5))
        ttk.Label(frame, text="Dislikes (comma-separated)").pack(anchor="w")
        ttk.Entry(frame, textvariable=dislikes).pack(fill="x", pady=(1, 5))
        ttk.Label(frame, text="Speech style").pack(anchor="w")
        style = tk.Text(frame, height=2, wrap="word"); style.pack(fill="x", pady=(1, 5)); style.insert("1.0", char.get("speech_style", ""))
        ttk.Label(frame, text="How I disagree").pack(anchor="w")
        disagreement = tk.Text(frame, height=2, wrap="word"); disagreement.pack(fill="x", pady=(1, 5)); disagreement.insert("1.0", char.get("disagreement", ""))
        ttk.Label(frame, text="Always-follow rules (one per line; safety rules cannot be removed here)").pack(anchor="w")
        never = tk.Text(frame, height=3, wrap="word"); never.pack(fill="x", pady=(1, 5)); never.insert("1.0", "\n".join(char.get("never_change_rules", [])))

        def save():
            try:
                datetime.datetime.strptime(q1.get().strip(), "%H:%M")
                datetime.datetime.strptime(q2.get().strip(), "%H:%M")
                datetime.datetime.strptime(late_time.get().strip(), "%H:%M")
            except ValueError:
                messagebox.showerror("Settings", "Times must use HH:MM, for example 23:00.", parent=win); return
            try:
                custom_keep_minutes = int(custom_minutes.get().strip() or "30")
                if custom_keep_minutes < 1 or custom_keep_minutes > 10080:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Settings", "Custom model RAM time must be a whole number from 1 to 10080 minutes.", parent=win); return
            try:
                camera_cooldown_value = int(camera_cooldown.get())
                camera_rate_value = int(camera_rate.get())
                if camera_cooldown_value not in (0, 1, 5, 15, 30) or camera_rate_value not in (100, 200, 500):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Settings", "Choose a listed camera cooldown and refresh interval.", parent=win); return
            old_proactive_enabled = bool(self.settings.get("proactive_enabled", True))
            old_facts_tier = str(self.personal_companion.setting("tier_facts", "1")).lower() not in ("0", "false", "off", "no")
            if old_facts_tier != bool(facts_tier.get()):
                self.personal_companion.set_facts_tier(bool(facts_tier.get()))
            for tier_key,enabled in (("tier_loops",tier_loops.get()),("tier_rhythm",tier_rhythm.get())):
                old=str(self.personal_companion.setting(tier_key,"0" if tier_key=="tier_rhythm" else "1")).lower() not in ("0","false","off","no")
                if old != bool(enabled):
                    self.personal_companion.set_setting(tier_key,"1" if enabled else "0")
                    self.personal_companion.add_consent_ledger(tier_key,bool(enabled),tier_key+"_toggle")
            for trigger,enabled_var in trigger_vars.items():
                old=str(self.personal_companion.setting("trigger_enabled_"+trigger,"1")).lower() not in ("0","false","off","no")
                if old != bool(enabled_var.get()):
                    self.personal_companion.set_setting("trigger_enabled_"+trigger,"1" if enabled_var.get() else "0")
                    self.personal_companion.add_consent_ledger("initiative:"+trigger,bool(enabled_var.get()),"trigger_toggle")
            timing_values = {}
            try:
                for _key, (_var, _low, _high) in timing_vars.items():
                    _number = int(float(_var.get()))
                    if not _low <= _number <= _high: raise ValueError
                    timing_values[_key] = _number
            except ValueError:
                messagebox.showerror("Settings", "Window check-in timings must be whole numbers inside the allowed ranges.", parent=win); return
            self.settings.update(timing_values, window_checkin_llm_text=bool(llm_text.get()))
            try: cap_value=max(0,min(5,int(daily_cap.get())))
            except ValueError:
                messagebox.showerror("Settings","Daily nudge cap must be a number from 0 to 5.",parent=win); return
            self.settings.update(initiative_daily_cap=cap_value)
            self.settings.update(model_chat=model.get().strip() or "llama3.2:3b", voice_enabled=voice.get(),
                                 webcam_enabled=webcam_enabled.get(),
                                 camera_min_interval_minutes=camera_cooldown_value,
                                 camera_frame_interval_ms=camera_rate_value,
                                 model_keep_alive_mode=keep_alive_mode.get(),
                                 model_keep_alive_custom_minutes=custom_keep_minutes,
                                 awareness_enabled=awareness.get(), proactive_enabled=proactive.get(),
                                 auto_start_enabled=autostart.get(), background_agent_enabled=background_agent.get(),
                                 late_night_nudge_enabled=late_night.get(), late_night_nudge_time=late_time.get().strip(),
                                 quiet_start=q1.get().strip(), quiet_end=q2.get().strip(), strictness=strictness.get())
            self.settings.setdefault("ollama_options", {})["num_gpu"] = 0
            self.core.settings.update(self.settings)
            # Apply the keep-alive choice immediately to future Ollama requests.
            from llm import resolve_keep_alive
            self.core.llm.keep_alive = resolve_keep_alive(self.settings)
            char["traits"] = [x.strip() for x in traits.get().split(",") if x.strip()]
            char["values"] = [x.strip() for x in values.get().split(",") if x.strip()]
            char["likes"] = [x.strip() for x in likes.get().split(",") if x.strip()]
            char["dislikes"] = [x.strip() for x in dislikes.get().split(",") if x.strip()]
            char["speech_style"] = style.get("1.0", "end").strip()
            char["disagreement"] = disagreement.get("1.0", "end").strip()
            char["never_change_rules"] = [x.strip() for x in never.get("1.0", "end").splitlines() if x.strip()]
            with open(os.path.join(ROOT, "config", "settings.json"), "w", encoding="utf-8") as f: json.dump(self.settings, f, indent=2)
            with open(os.path.join(ROOT, "config", "character.json"), "w", encoding="utf-8") as f: json.dump(char, f, indent=2)
            if old_proactive_enabled != bool(proactive.get()):
                self._apply_proactive_availability(proactive.get(), persist=False, announce=False)
            else:
                self._draw_proactive_availability_switch()
            self.core.character = char
            self.voice_enabled = voice.get()
            self.camera_policy.enabled = webcam_enabled.get()
            self.camera_policy.minimum_interval_minutes = camera_cooldown_value
            was = self.awareness_enabled; self.awareness_enabled = awareness.get()
            if self.awareness_enabled and not was: self._start_awareness()
            self._apply_autostart(autostart.get())
            self._status("Settings saved. RAM keep-alive changes apply to the next model request; model name changes apply after restart.")
            win.destroy()
        lifecycle_row = ttk.Frame(frame)
        lifecycle_row.pack(fill="x", pady=(2, 8))
        ttk.Button(lifecycle_row, text="Consolidate chats now", command=self._run_consolidation_now).pack(side="left", padx=(0, 6))
        ttk.Button(lifecycle_row, text="Rollback latest personality change", command=self._rollback_personality).pack(side="left")
        ttk.Button(frame, text="Save settings", command=save).pack(anchor="e", pady=8)

    def _visual_context(self):
        if not self.camera_active or not self.expression_analyzer:
            return ""
        return self.expression_analyzer.get_recent_summary(max_age_seconds=12)

    @staticmethod
    def _reply_requests_camera_permission(reply):
        low = (reply or "").lower()
        asks = ("may i turn on your camera", "may i turn on the camera", "can i turn on your camera", "may i use the camera",
                "can i use the camera", "would you like me to turn on the camera",
                "can i turn on the camera", "may i look at you through the camera",
                "would it be okay if i turn on the camera", "would it be okay to turn on your camera",
                "is it okay if i turn on the camera", "can i switch on the camera")
        return "?" in low and any(phrase in low for phrase in asks)

    @staticmethod
    def _is_explicit_camera_consent(text):
        """Recognize an unambiguous user grant, not a question or vague acknowledgement."""
        low = " ".join((text or "").strip().lower().split())
        grants = (
            "i give you permission to use my webcam", "i give you permission to see me using webcam",
            "i am giving you permission to see me using webcam", "i'm giving you permission to see me using webcam",
            "i give permission to turn on the camera", "i permit you to turn on the camera",
            "you have my permission to turn on the camera", "i consent to turning on the camera",
            "i consent to you using my webcam", "you can use my webcam now",
            "i allow you to use my webcam", "i allow you to turn on the camera",
        )
        return low in grants or any(phrase in low for phrase in grants)

    @staticmethod
    def _is_camera_status_question(text):
        low = " ".join((text or "").strip().lower().split())
        return any(phrase in low for phrase in (
            "can you see me", "are you seeing me", "can you view me", "can you see my face",
            "can you access my webcam", "are you using my webcam", "is my camera on",
            "is the camera on", "is webcam on", "can you see me now", "are you looking at me",
        ))

    @staticmethod
    def _is_expression_question(text):
        low = " ".join((text or "").strip().lower().split())
        return any(phrase in low for phrase in (
            "can you read my facial expression", "can you read my expression",
            "read my expression", "read my facial expression", "what does my expression look like",
            "describe my expression", "what is my face showing", "what expression am i making",
            "judge my facial expression", "judge my expression", "check my expression",
            "my facial expression", "my expression", "facial expression",
        ))

    @staticmethod
    def _is_mood_question(text):
        low = " ".join((text or "").strip().lower().split())
        return any(phrase in low for phrase in (
            "can you explain my mood", "what is my mood", "guess my mood", "read my mood",
            "how do i look emotionally", "what am i feeling", "what emotion am i showing",
            "what mood am i in", "can you tell my mood", "am i happy", "am i sad",
            "am i angry", "am i surprised", "do i look happy", "do i look sad",
            "do i look angry", "happy or sad", "sad or happy", "what emotion do i look",
        ))

    @staticmethod
    def _is_camera_followup(text):
        low = " ".join((text or "").strip().lower().strip("?.! ").split())
        return low in {"now", "and now", "what about now", "how about now", "now then", "what about now then"}

    @staticmethod
    def _summarize_visual_observations(visual):
        """Convert raw local feature text into a short, non-repetitive user-facing summary."""
        if not visual:
            return "I don't have a fresh facial-feature sample yet. Give it a moment and ask again."
        if "no face detected" in visual.lower():
            return "The camera is on, but the latest sample didn't detect a face, so I won't guess your expression."
        if "offline fer+ cnn prediction smoothed across" in visual.lower():
            marker = ": "
            idx = visual.find(marker)
            prediction = visual[idx + len(marker):].strip() if idx >= 0 else visual.strip()
            return ("Recent local model estimate: " + prediction +
                    " The model classifies visible facial patterns; it cannot know your actual feelings.")
        wanted = ("smile-related movement", "brow-lowering movement", "eye-blink closure", "jaw-open movement")
        found = []
        low = visual.lower()
        for label in wanted:
            marker = label + ":"
            idx = low.find(marker)
            if idx >= 0:
                value = visual[idx + len(marker):].strip().split(";")[0].split(".")[0].strip()
                found.append(f"{label}: {value}")
        pattern_marker = "estimated visible expression pattern:"
        pattern = ""
        pattern_idx = low.find(pattern_marker)
        if pattern_idx >= 0:
            pattern = visual[pattern_idx + len(pattern_marker):].strip().split(".")[0].strip()
        if not found and not pattern:
            return "The camera is on, but I couldn't get a readable recent feature summary."
        response = "Recent local facial-movement cues: " + "; ".join(found) if found else "Recent local facial analysis"
        if pattern:
            response += ". Rough visible-expression estimate: " + pattern
        return response + ". These are approximate visual cues, not proof of your mood or intent."

    def _handle_camera_chat_command(self, text):
        low = " ".join(text.strip().lower().split())
        stop_terms = ("stop camera", "turn camera off", "camera off", "close camera", "stop looking at me")
        if any(term in low for term in stop_terms):
            self.camera_consent_pending = False
            if self.camera_active:
                # Close the preview window via the stored close callback.
                if callable(getattr(self, "_camera_stop_callback", None)):
                    self._camera_stop_callback()
                self._append("Buddy", "Okay, stopping the camera now.", "buddy")
            else:
                self._append("Buddy", "The camera is already off.", "buddy")
            return True
        yes = low in {"yes", "yes please", "yes, go ahead", "yes go ahead", "yes turn it on", "yeah", "yep", "sure", "sure, you can", "okay", "ok", "go ahead", "allow", "i agree"}
        explicit_consent = self._is_explicit_camera_consent(low)
        no = low in {"no", "no thanks", "nope", "don't", "do not", "not now", "cancel"}

        # A desktop-awareness question (e.g. "what am I doing?") is not the same
        # as a request to inspect screen pixels. Route desktop-context questions to
        # get_desktop_context; keep the hard boundary for actual screen/page requests.
        screen_question = any(term in low for term in (
            "screen", "display", "browser page", "webpage", "web page", "screenshot", "screen pixels"
        )) and any(term in low for term in ("see", "view", "look", "show", "read", "what", "inspect"))
        if screen_question:
            if getattr(self, "awareness_enabled", False):
                # The title is a clue, not page content. Use the same privacy-filtered
                # summary exposed to the background context engine.
                try:
                    snapshot = self.context_engine.observe(self._idle_seconds()).safe_summary()
                except Exception:
                    snapshot = {}
                title = str(snapshot.get("window_title") or "").strip()
                title_note = (" The current window title is " + repr(title[:120]) + "; that's only a clue, not page content."
                              if title and title != "[title hidden for privacy]" else "")
                detail = (" Desktop Awareness may provide the foreground app/category and a sanitized active-window title, "
                          "but it does not provide screen pixels or browser page contents." + title_note)
            else:
                detail = " Desktop Awareness is disabled, so I do not even receive the coarse foreground-app context."
            camera_note = (" The camera preview is a separate feature and does not give me screen access."
                           if self.camera_active else "")
            self._append("Buddy", "I cannot view or read your screen, browser page, or display." + detail + camera_note, "buddy")
            return True

        if self.camera_consent_pending and (yes or explicit_consent):
            self.camera_consent_pending = False
            self.last_camera_topic_ts = time.time()
            if not self.settings.get("webcam_enabled", False):
                self._append("Buddy", "Camera requests are disabled in Settings. Enable them there first; camera is still off.", "buddy")
            else:
                self._append("Buddy", "Thanks for allowing it. I'll open the camera now; you can stop it any time.", "buddy")
                self.open_camera(consent_already_given=True)
            return True
        if self.camera_consent_pending and no:
            self.camera_consent_pending = False
            self.last_camera_topic_ts = time.time()
            self.camera_declined_this_chat = True
            self.core.camera_declined_this_chat = True
            self._append("Buddy", "No problem. I won't turn on the camera.", "buddy")
            return True
        if any(term in low for term in stop_terms):
            return True
        if explicit_consent and not self.camera_active:
            if not self.settings.get("webcam_enabled", False):
                self._append("Buddy", "Thanks for giving permission. Camera requests are disabled in Settings, so the camera remains off until you enable them.", "buddy")
            else:
                self._append("Buddy", "Thanks for the clear permission. I'll open the camera now; you can say 'stop camera' at any time.", "buddy")
                self.open_camera(consent_already_given=True)
            return True
        camera_followup = (self.camera_active and time.time() - self.last_camera_topic_ts < 120
                           and self._is_camera_followup(low))
        expression_or_mood_question = (
            self._is_expression_question(low) or self._is_mood_question(low)
        )
        visual_inspection_request = any(phrase in low for phrase in (
            "see me", "look at me", "look at my face", "see my face",
            "check my expression", "check my face", "look at my expression",
        ))
        general_vision_question = any(phrase in low for phrase in (
            "what can you see", "what do you see", "what are you seeing",
            "can you see", "describe what you see", "what can you observe",
            "how do i look", "how am i look like", "how am i looking", "what do i look like",
            "describe my appearance", "can you describe me", "am i smiling",
        ))
        if (self._is_camera_status_question(low) or camera_followup or expression_or_mood_question
                or (self.camera_active and visual_inspection_request) or general_vision_question):
            # Answer from the actual camera/analysis state. A small model must not
            # override permission, preview, or analysis availability with a guess.
            self.last_camera_topic_ts = time.time()
            if not self.camera_active:
                if self.camera_consent_pending:
                    self._append("Buddy", "I haven't started the camera yet; I'm waiting for your clear yes. I cannot see your screen or images. Reply 'yes' to allow the camera, or 'no' to keep it off.", "buddy")
                else:
                    desktop_note = (" Desktop Awareness may provide only a coarse active-app category; it does not show me screen pixels or page contents."
                                    if getattr(self, "awareness_enabled", False) else " Desktop Awareness is also disabled.")
                    self._append("Buddy", "I can't directly see your screen, browser pages, or images. The camera is currently off, so I also have no live facial cues." + desktop_note + " Say 'look at me' if you want to request camera access.", "buddy")
            elif self._is_camera_status_question(low) or camera_followup or general_vision_question:
                visual = self._visual_context()
                if visual:
                    self._append("Buddy", "The camera preview is active. I don't send or interpret the raw image; the app classifies a face crop locally with an offline FER+ CNN. " + self._summarize_visual_observations(visual), "buddy")
                elif self.expression_analyzer and not self.expression_analyzer.available:
                    self._append("Buddy", "The camera preview is active, but local facial-feature analysis isn't available. No camera image is sent to Llama.", "buddy")
                else:
                    self._append("Buddy", "The camera preview is active, but I don't have a fresh facial-feature sample yet. Give it a moment and ask again.", "buddy")
            else:
                visual = self._visual_context()
                if visual:
                    self._append("Buddy", self._summarize_visual_observations(visual) + " I can't reliably infer your mood from these cues alone—how are you feeling?", "buddy")
                elif self.expression_analyzer and not self.expression_analyzer.available:
                    self._append("Buddy", "The camera is active, but facial-feature analysis isn't available. I can't describe your expression from the camera image because the image isn't sent to Llama.", "buddy")
                else:
                    self._append("Buddy", "The camera is active, but I don't have a fresh facial-feature sample yet. Give it a moment and ask again.", "buddy")
            return True
        request_terms = (
            "look at me", "see me", "i want you to see me", "can you look at me",
            "look at my face", "check my face", "check my expression", "read my expression",
            "check if i look", "tell me how i look", "what do i look like", "watch me",
            "turn on camera", "turn the camera on", "camera on", "open camera",
            "use my camera", "look through camera", "can you see my face",
        )
        if any(term in low for term in request_terms):
            self.last_camera_topic_ts = time.time()
            self.camera_declined_this_chat = False
            self.core.camera_declined_this_chat = False
            if not self.settings.get("webcam_enabled", False):
                self._append("Buddy", "Camera access is disabled. Enable 'Allow camera requests from chat' in Settings first. The camera remains off.", "buddy")
            elif self.camera_active:
                self._append("Buddy", "The camera preview is already active. I can classify a visible-expression pattern locally, but it does not prove your mood.", "buddy")
            else:
                self.camera_consent_pending = True
                self._append("Buddy", "I'd like to see you for a moment so I can check the live facial-expression cues. May I turn on your camera? I'll only analyse the frames locally, won't save photos or video, and you can stop it whenever you like. Just say yes or no.", "buddy")
            return True
        return False

    def open_camera(self, consent_already_given=False):
        allowed, reason = self.camera_policy.can_start()
        if not allowed:
            messagebox.showinfo("Camera unavailable", reason, parent=self.root)
            return
        if not self.settings.get("webcam_enabled", False):
            messagebox.showinfo("Camera disabled", "Enable manual webcam preview in Settings first.", parent=self.root)
            return
        self.camera_policy.mark_started()
        if self.expression_analyzer is None:
            model_path = os.path.join(ROOT, "models", "emotion-ferplus-8.onnx")
            self.expression_analyzer = CameraExpressionAnalyzer(model_path)
            if not self.expression_analyzer.available:
                self._append("Buddy", "Camera preview can still work, but offline facial-feature analysis is unavailable: " + self.expression_analyzer.last_error + " Run install_camera.bat once while online to install ONNX Runtime and download the model asset.", "meta")
        window = open_camera_preview(
            self.root, self.settings, on_state_change=self._camera_state_changed,
            consent_already_given=consent_already_given,
            on_frame=(self.expression_analyzer.process_bgr_frame if self.expression_analyzer and self.expression_analyzer.available else None),
            on_overlay=(self.expression_analyzer.get_live_overlay if self.expression_analyzer and self.expression_analyzer.available else None),
        )
        if window is not None:
            self._camera_stop_callback = getattr(window, "_studybuddy_stop", None)
        if window is None:
            # A cancelled prompt or failed camera open should not consume the cooldown.
            self.camera_policy.last_started_at = 0.0

    def _camera_state_changed(self, active):
        self.camera_active = bool(active)
        dashboard = getattr(self, "_intelligence_dashboard", None)
        if dashboard is not None:
            try:
                dashboard.refresh()
            except Exception:
                pass
        if self.camera_active:
            self._status("CAMERA ACTIVE — live preview only; not recording")
        elif not self.busy:
            self._status("Camera stopped. No video was saved.")

    def encrypt_profile_folder(self):
        profile = self.settings.get("profile", "hamim")
        folder = os.path.join(ROOT, "users", profile)
        if not messagebox.askyesno(
            "Windows EFS encryption",
            "Before continuing, create an encrypted Study Buddy backup and keep your Windows account/certificate safe. "
            "If the EFS certificate is lost, encrypted files may be unrecoverable.\n\nEncrypt the entire profile folder now?",
            parent=self.root,
        ):
            return
        result = encrypt_folder(folder)
        if result.get("ok"):
            messagebox.showinfo("Memory protection", result.get("message", "Encryption command completed."), parent=self.root)
        else:
            messagebox.showerror("Memory protection unavailable", result.get("message", "Encryption failed."), parent=self.root)

    def check_memory_encryption(self):
        profile = self.settings.get("profile", "hamim")
        db_path = os.path.join(ROOT, "users", profile, "memory.db")
        result = check_efs_file(db_path)
        title = "Encrypted" if result.get("encrypted") else "Encryption status"
        messagebox.showinfo(title, result.get("message", "No status returned."), parent=self.root)

    def open_my_world(self):
        """Explore/edit a consent-first local personal knowledge graph."""
        win = tk.Toplevel(self.root); win.title("My World — Personal Knowledge Map")
        window_w=min(1320,max(1050,win.winfo_screenwidth()-80))
        window_h=min(820,max(650,win.winfo_screenheight()-100))
        win.geometry(f"{window_w}x{window_h}"); win.minsize(min(1050,window_w),min(650,window_h))
        outer = ttk.Frame(win, padding=10); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="My World", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(outer, text="A personal map of what you enjoy, what matters to you, your goals, important people and experiences, and the kind of support you prefer. It stores what you choose to share, not a psychological profile; you can change or forget any detail at any time.", wraplength=1180).pack(anchor="w", pady=(2,8))
        review_status = tk.StringVar(value="Checking background chat review…")
        ttk.Label(outer, textvariable=review_status, wraplength=1180, justify="left",
                  foreground="#256a8a").pack(anchor="w", pady=(0,8))
        body = ttk.Frame(outer); body.pack(fill="both", expand=True)
        graph = ttk.Frame(body); graph.pack(side="left", fill="both", expand=True, padx=(0,10))
        canvas = tk.Canvas(graph, background="#101827", highlightthickness=0, width=820)
        yscroll = ttk.Scrollbar(graph, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=yscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        side = ttk.Frame(body, width=330); side.pack(side="right", fill="y")
        ttk.Label(side, text="Memories", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        listing = tk.Listbox(side, height=14, width=43); listing.pack(fill="both", expand=True, pady=5)
        selected = {}
        last_map_signature = None
        def refresh_review_status():
            try:
                if not win.winfo_exists(): return
                state = self._background_memory_review_status()
                summary = self._memory_review_last_summary or "No background chat review has finished yet."
                review_status.set(
                    "Background chat review: " + state + "\n"
                    "Last pass: " + summary + "\n"
                    "After 90 seconds without chat or model activity, one local pass reviews up to 60 retained messages together. New typing or a new chat turn pauses the review; PC inactivity is not required. "
                    "Only clear, one-idea details with direct evidence in your own words are added; filler, availability, and run-on chat is skipped. The reviewer does not diagnose or assign personality traits.")
                win.after(1000, refresh_review_status)
            except tk.TclError:
                return
        def refresh():
            nonlocal last_map_signature
            nodes = self.personal_companion.list_nodes()
            edges = self.personal_companion.list_edges()
            last_map_signature = tuple((n.get("id"),n.get("label"),n.get("kind"),n.get("status"),n.get("updated_ts")) for n in nodes)
            listing.delete(0, "end"); canvas.delete("all"); selected.clear()
            w=max(canvas.winfo_width(),760)
            confirmed=[n for n in nodes if n['status']=='confirmed']
            candidates=[n for n in nodes if n['status']=='candidate']
            display=confirmed+candidates
            groups=(
                ("Everyday context", {"profile","area","study_work","education","occupation","routine","schedule","habit","location","place"}, "#38bdf8"),
                ("Interests & enjoyment", {"interest","hobby"}, "#fbbf24"),
                ("Goals & progress", {"goal","plan","aim","project","deadline","achievement"}, "#a78bfa"),
                ("Values & meaning", {"value","meaning"}, "#f472b6"),
                ("People & belonging", {"people","relationship","family","friend"}, "#fb7185"),
                ("Good moments", {"good_moment"}, "#4ade80"),
                ("Difficult moments", {"hard_moment","challenge","setback"}, "#f97372"),
                ("Support & communication", {"preference","style","support_style"}, "#34d399"),
                ("Life moments", {"episode","moment","event"}, "#c084fc"),
            )
            grouped={title:[] for title,_,_ in groups}
            other=[]
            for n in display:
                kind=str(n.get("kind") or "").casefold()
                target=next((title for title,kinds,_ in groups if kind in kinds),None)
                if target:
                    grouped[target].append(n)
                else:
                    other.append(n)
            if other:
                groups=groups+(("Other details",set(),"#94a3b8"),)
                grouped["Other details"]=other

            source_labels={"conversation":"shared in conversation","onboarding":"getting-to-know-you chat"}
            for i,n in enumerate(display):
                source_label = str(n.get("source") or "manual")
                expiry = n.get("expires_ts")
                expiry_label = (" · expires " + datetime.datetime.fromtimestamp(float(expiry)).strftime("%Y-%m-%d")) if expiry else ""
                status_label = str(n.get("status") or "candidate")
                marker = "✓" if status_label == "confirmed" else ("×" if status_label in ("rejected", "expired") else "?")
                listing.insert("end", f"{marker} {n['label']} [{status_label}; {source_labels.get(source_label, source_label)}{expiry_label}]")
                selected[i]=n
            col_gap=28
            card_w=max(330,(w-54-col_gap)//2)
            x_positions=(24, 24+card_w+col_gap)
            root_x=w/2
            canvas.create_oval(root_x-84,22,root_x+84,78,fill="#172554",outline="#60a5fa",width=2)
            canvas.create_text(root_x,42,text="ME",fill="#bfdbfe",font=("Segoe UI",10,"bold"))
            canvas.create_text(root_x,61,text="my world",fill="white",font=("Segoe UI",10))
            row_y=118
            node_positions={}
            card_positions=[]
            def wrapped_lines(value, width=58):
                import textwrap
                return textwrap.wrap(str(value),width=width,break_long_words=True,break_on_hyphens=False) or [""]

            for pair in range(0,len(groups),2):
                row=groups[pair:pair+2]
                heights=[]
                for title,kinds,color in row:
                    items=grouped.get(title,[])
                    height=66 if not items else 66+sum(max(1,len(wrapped_lines(n.get("label",""))))*15+10 for n in items)
                    heights.append(max(122,height))
                row_h=max(heights)
                for offset,(title,kinds,color) in enumerate(row):
                    x=x_positions[offset]
                    items=grouped.get(title,[])
                    card_positions.append((root_x,row_y,x+card_w/2,row_y,color))
                    # Draw category cards first; labels and status indicators remain
                    # inside the card instead of floating around a crowded circle.
                    canvas.create_rectangle(x,row_y,x+card_w,row_y+row_h,fill="#172235",outline=color,width=2)
                    canvas.create_text(x+18,row_y+20,text=title,anchor="w",fill=color,font=("Segoe UI",11,"bold"))
                    canvas.create_text(x+card_w-16,row_y+20,text=f"{len(items)} detail{'s' if len(items)!=1 else ''}",anchor="e",fill="#94a3b8",font=("Segoe UI",9))
                    y=row_y+46
                    if not items:
                        empty_hint = ("Only clear, meaningful experiences appear here after a quiet chat review."
                                      if title in ("Good moments", "Difficult moments", "Life moments")
                                      else "This can grow naturally from what you choose to share.")
                        canvas.create_text(x+18,y+9,text=empty_hint,anchor="w",fill="#8291a6",font=("Segoe UI",9,"italic"),width=card_w-36)
                        continue
                    for n in items:
                        status_label=str(n.get("status") or "candidate")
                        dot="#4ade80" if status_label=="confirmed" else ("#fbbf24" if status_label=="candidate" else "#64748b")
                        canvas.create_oval(x+17,y+3,x+25,y+11,fill=dot,outline="")
                        label=str(n.get("label") or "")
                        text_id=canvas.create_text(x+34,y,text=label,anchor="nw",fill="#e5edf7",font=("Segoe UI",9),width=card_w-54)
                        bbox=canvas.bbox(text_id)
                        text_h=max(16,(bbox[3]-bbox[1]) if bbox else 16)
                        node_positions[n['id']]=(x+card_w/2,y+text_h/2)
                        y += text_h+10
                row_y += row_h+22

            # A quiet root branch gives the page a map structure; category cards
            # carry the detail so long labels stay readable at any window size.
            trunk_y=94
            canvas.create_line(root_x,78,root_x,trunk_y,fill="#45617e",width=2)
            if card_positions:
                card_centers=[item[2] for item in card_positions]
                canvas.create_line(min(card_centers),trunk_y,max(card_centers),trunk_y,fill="#45617e",width=2)
                for _,card_y,cx,_unused,color in card_positions:
                    canvas.create_line(cx,trunk_y,cx,card_y,fill=color,width=1.5)

            for e in edges:
                if e['source_id'] in node_positions and e['target_id'] in node_positions:
                    x1,y1=node_positions[e['source_id']]; x2,y2=node_positions[e['target_id']]
                    if abs(x1-x2)>card_w*.7:
                        canvas.create_line(x1,y1,x2,y2,fill="#52657a",width=1,dash=(3,4),smooth=True)
            canvas.configure(scrollregion=(0,0,w,max(row_y+10,canvas.winfo_height(),600)))
        canvas.bind("<Configure>", lambda _event: refresh())
        def selected_node():
            cur=listing.curselection()
            return selected.get(cur[0]) if cur else None
        def edit_memory():
            n=selected_node()
            if not n:
                messagebox.showinfo("Edit memory", "Select a My World detail first.", parent=win)
                return
            editor=tk.Toplevel(win); editor.title("Edit My World detail"); editor.geometry("560x370")
            editor.transient(win); editor.grab_set()
            frame=ttk.Frame(editor,padding=12); frame.pack(fill="both",expand=True)
            ttk.Label(frame,text="Memory detail").pack(anchor="w")
            label_var=tk.StringVar(value=str(n.get("label") or ""))
            label_entry=ttk.Entry(frame,textvariable=label_var); label_entry.pack(fill="x",pady=(3,10))
            kind_names={
                "Everyday context":"profile", "Study / work":"study_work", "Study area":"area",
                "Interest":"interest", "Hobby":"hobby", "Goal / plan":"goal", "Achievement":"achievement",
                "Value / meaning":"value", "People / relationships":"people", "Routine":"routine",
                "Location":"location", "Communication preference":"preference", "Support style":"support_style",
                "Good moment":"good_moment", "Difficult moment":"hard_moment", "Life moment":"episode",
            }
            current_kind=str(n.get("kind") or "profile")
            current_name=next((name for name,value in kind_names.items() if value==current_kind),None)
            if current_name is None:
                current_name=f"Keep current type ({current_kind})"
                kind_names[current_name]=current_kind
            ttk.Label(frame,text="My World section").pack(anchor="w")
            kind_var=tk.StringVar(value=current_name)
            kind_box=ttk.Combobox(frame,textvariable=kind_var,values=tuple(kind_names),state="readonly")
            kind_box.pack(fill="x",pady=(3,10))
            ttk.Label(frame,text="Original evidence (read-only)").pack(anchor="w")
            evidence=tk.Text(frame,height=4,wrap="word"); evidence.pack(fill="both",expand=True,pady=(3,8))
            evidence.insert("1.0",str(n.get("evidence") or "No evidence text is stored."))
            evidence.configure(state="disabled")
            buttons=ttk.Frame(frame); buttons.pack(fill="x")
            def save_edit():
                try:
                    self.personal_companion.update_fact(n["id"],label_var.get(),kind_names.get(kind_var.get(),current_kind))
                except ValueError as exc:
                    messagebox.showerror("Edit memory",str(exc),parent=editor); return
                refresh(); editor.destroy()
            ttk.Button(buttons,text="Cancel",command=editor.destroy).pack(side="right")
            ttk.Button(buttons,text="Save changes",command=save_edit).pack(side="right",padx=(0,6))
            label_entry.focus_set(); label_entry.selection_range(0,"end")
        def add_memory():
            label=simpledialog.askstring("Add memory", "What should your study buddy remember?", parent=win)
            if not label: return
            kind=simpledialog.askstring("Memory type", "Type (profile, study_work, area, interest, value, goal, achievement, people, routine, location, preference, support_style, good_moment, hard_moment, episode):", initialvalue="interest", parent=win) or "interest"
            if not messagebox.askyesno("Confirm memory", f"Save this as a confirmed personal memory?\n\n{label}", parent=win):
                self.personal_companion.add_fact(label,kind,evidence="Manually entered; not confirmed",confirmed=False,consented=False)
            else:
                self.personal_companion.add_fact(label,kind,evidence="Explicitly provided and confirmed by user",confirmed=True,consented=True)
                self.core._bridge_confirmed_name(label)
            refresh()
        def confirm_memory():
            n=selected_node()
            if n and messagebox.askyesno("Confirm", f"Confirm this memory?\n{n['label']}", parent=win):
                if self.personal_companion.confirm_fact(n['id']): self.core._bridge_confirmed_name(n['label'])
                refresh()
        def delete_memory():
            n=selected_node()
            if n and messagebox.askyesno("Delete memory", f"Delete this memory?\n{n['label']}", parent=win): self.personal_companion.forget_fact(n['id']); refresh()
        def start_chat():
            if self.busy: messagebox.showinfo("Chat busy", "Wait for the current reply, then try again.", parent=win); return
            if self.core.intent_router and self.core.intent_router.has_pending:
                messagebox.showinfo("Dialog active", "Finish or skip the current chat dialog first.", parent=win); return
            prompt = self.core.start_relationship_checkin(restart=True)
            if not prompt:
                messagebox.showinfo("Getting to know you", "No invitation is ready right now. Personal memory may be off, or your last invitation is still in its cooldown.", parent=win)
                return
            self.core.history.append({"role": "assistant", "content": prompt})
            self.memory.add_turn("assistant", prompt)
            self._append(self.core.buddy_name, prompt, "buddy")
        def schedule_chat():
            minutes=simpledialog.askinteger("Schedule a check-in", "How many minutes from now should your study buddy ask again? (1–10080)", minvalue=1, maxvalue=10080, parent=win)
            if minutes is None: return
            fid=self.personal_companion.schedule_followup(time.time()+minutes*60, kind="onboarding", payload={"consent_requested":True})
            messagebox.showinfo("Check-in scheduled", f"Your study buddy will ask once in about {minutes} minute(s), when the app is available and outside quiet hours.", parent=win)
        ttk.Button(side,text="Start getting-to-know-you chat",command=start_chat).pack(fill="x",pady=3)
        ttk.Button(side,text="I'm busy — ask later…",command=schedule_chat).pack(fill="x",pady=3)
        ttk.Button(side,text="Add memory",command=add_memory).pack(fill="x",pady=3)
        ttk.Button(side,text="Edit selected",command=edit_memory).pack(fill="x",pady=3)
        ttk.Button(side,text="Confirm selected",command=confirm_memory).pack(fill="x",pady=3)
        ttk.Button(side,text="Delete selected",command=delete_memory).pack(fill="x",pady=3)
        ttk.Separator(side).pack(fill="x",pady=6)
        ttk.Label(side,text="Patterns",font=("Segoe UI",10,"bold")).pack(anchor="w")
        pattern_text=tk.StringVar(value=self.core.rhythm_tracker.describe())
        ttk.Label(side,textvariable=pattern_text,wraplength=270,justify="left").pack(anchor="w",pady=3)
        def reset_patterns():
            from companion.privacy import PrivacyService
            PrivacyService(self.memory).delete_rhythm(); pattern_text.set(self.core.rhythm_tracker.describe())
        ttk.Button(side,text="Reset patterns",command=reset_patterns).pack(fill="x",pady=2)
        ttk.Label(side,text="Nudge log (last 20)",font=("Segoe UI",10,"bold")).pack(anchor="w",pady=(6,0))
        nudge_list=tk.Listbox(side,height=5,width=38); nudge_list.pack(fill="x",pady=3)
        def refresh_nudge_log():
            nudge_list.delete(0,"end")
            for event in self.core.feedback_logger.recent(20):
                stamp=datetime.datetime.fromtimestamp(float(event['ts'])).strftime("%m-%d %H:%M")
                nudge_list.insert("end",f"{stamp} · {event['trigger']}: {str(event['reason'] or '')[:70]}")
        def refresh_map_if_changed():
            try:
                if not win.winfo_exists(): return
                nodes=self.personal_companion.list_nodes()
                signature=tuple((n.get("id"),n.get("label"),n.get("kind"),n.get("status"),n.get("updated_ts")) for n in nodes)
                if signature != last_map_signature:
                    refresh()
                win.after(10000,refresh_map_if_changed)
            except tk.TclError:
                return
        def why_last():
            messagebox.showinfo("Why did you message me?",self.core.initiative_policy.explain_last(),parent=win)
        ttk.Button(side,text="Why did you message me?",command=why_last).pack(fill="x",pady=2)
        ttk.Button(side,text="Refresh map / patterns / log",command=lambda:(refresh(),pattern_text.set(self.core.rhythm_tracker.describe()),refresh_nudge_log())).pack(fill="x",pady=3)
        ttk.Label(side,text="Green marks confirmed details; amber marks an unconfirmed candidate. Select a detail to edit, confirm, or delete it. Window titles are never saved.",wraplength=280).pack(anchor="w",pady=6)
        listing.bind("<Double-Button-1>", lambda _e: edit_memory())
        refresh(); refresh_nudge_log(); refresh_review_status(); win.after(10000,refresh_map_if_changed)

    def open_memory(self):
        win = tk.Toplevel(self.root); win.title("Memory manager"); win.geometry("760x520")
        book = ttk.Notebook(win); book.pack(fill="both", expand=True, padx=10, pady=10)

        # Facts tab
        facts_tab = ttk.Frame(book, padding=8); book.add(facts_tab, text="Facts")
        facts_list = tk.Listbox(facts_tab, height=14); facts_list.pack(fill="both", expand=True)
        fact_ids = []
        def refresh_facts():
            facts_list.delete(0,"end"); fact_ids.clear()
            for fact in self.memory.list_facts(200):
                facts_list.insert("end", fact); fact_ids.append(fact)
        def add_fact():
            val = simpledialog.askstring("Add memory", "What should I remember?", parent=win)
            if val and self.memory.add_fact(val): refresh_facts()
        def delete_fact():
            sel = facts_list.curselection()
            if not sel: return
            value = fact_ids[sel[0]]
            if messagebox.askyesno("Delete memory", "Delete this fact and matching conversation/companion memory? Tasks and reminders are not included.", parent=win):
                from companion.privacy import PrivacyService
                privacy = PrivacyService(self.memory)
                preview = privacy.preview_forget(value[:80])
                task_count = preview.get("tasks", 0)
                reminder_count = preview.get("reminders", 0)
                include_tasks = False
                if task_count or reminder_count:
                    detail = (f"This topic also matches {task_count} task(s) and {reminder_count} reminder(s).\n\n"
                              "Delete those tasks/reminders too? This cannot be undone.")
                    include_tasks = messagebox.askyesno("Also delete tasks/reminders?", detail, parent=win)
                self.memory.forget(value[:80], include_tasks=include_tasks)
                refresh_facts(); self.core.reload_history()
        fact_buttons = ttk.Frame(facts_tab); fact_buttons.pack(fill="x", pady=6)
        ttk.Button(fact_buttons, text="Add fact", command=add_fact).pack(side="left")
        ttk.Button(fact_buttons, text="Delete selected fact", command=delete_fact).pack(side="left", padx=6)
        refresh_facts()

        # Recent raw chat tab
        turns_tab = ttk.Frame(book, padding=8); book.add(turns_tab, text="Recent chat")
        turns_list = tk.Listbox(turns_tab, height=14); turns_list.pack(fill="both", expand=True)
        turn_ids = []
        def refresh_turns():
            turns_list.delete(0,"end"); turn_ids.clear()
            for turn in self.memory.list_turns(200):
                stamp = datetime.datetime.fromtimestamp(turn["ts"]).strftime("%m-%d %H:%M")
                text = turn["text"].replace("\n", " ")
                if len(text) > 180: text = text[:177] + "..."
                turns_list.insert("end", f"[{stamp}] {turn['role']}: {text}"); turn_ids.append(turn["id"])
        def delete_turn():
            sel = turns_list.curselection()
            if sel and messagebox.askyesno("Delete chat turn", "Delete selected saved turn?", parent=win):
                self.memory.delete_turn(turn_ids[sel[0]]); self.core.reload_history(); refresh_turns()
        row = ttk.Frame(turns_tab); row.pack(fill="x", pady=6)
        ttk.Button(row, text="Delete selected turn", command=delete_turn).pack(side="left")
        ttk.Button(row, text="Clear all raw chat", command=lambda: self._clear_chat(win, refresh_turns)).pack(side="left", padx=6)
        refresh_turns()

        # Summaries tab
        notes_tab = ttk.Frame(book, padding=8); book.add(notes_tab, text="Summaries")
        notes_list = tk.Listbox(notes_tab, height=14); notes_list.pack(fill="both", expand=True)
        note_ids = []
        def refresh_notes():
            notes_list.delete(0,"end"); note_ids.clear()
            for note in self.memory.list_notes(200):
                text = note["text"].replace("\n", " ")
                if len(text) > 180: text = text[:177] + "..."
                notes_list.insert("end", f"{note['level']} · {note['period']}: {text}"); note_ids.append(note["id"])
        def delete_note():
            sel = notes_list.curselection()
            if sel and messagebox.askyesno("Delete summary", "Delete selected summary?", parent=win):
                self.memory.delete_note(note_ids[sel[0]]); refresh_notes()
        ttk.Button(notes_tab, text="Delete selected summary", command=delete_note).pack(anchor="w", pady=6)
        refresh_notes()

        # Trusted people tab
        trusted_tab = ttk.Frame(book, padding=8); book.add(trusted_tab, text="Trusted people")
        trusted_list = tk.Listbox(trusted_tab, height=14); trusted_list.pack(fill="both", expand=True)
        def refresh_trusted():
            trusted_list.delete(0,"end")
            for person in self.memory.trusted_names(): trusted_list.insert("end", person)
        def add_trusted():
            name = simpledialog.askstring("Trusted person", "Who is someone you trust?", parent=win)
            if name: self.memory.add_trusted(name); refresh_trusted()
        def delete_trusted():
            sel = trusted_list.curselection()
            if sel and messagebox.askyesno("Remove trusted person", "Remove selected person?", parent=win):
                self.memory.delete_trusted(trusted_list.get(sel[0])); refresh_trusted()
        row = ttk.Frame(trusted_tab); row.pack(fill="x", pady=6)
        ttk.Button(row, text="Add person", command=add_trusted).pack(side="left")
        ttk.Button(row, text="Remove selected", command=delete_trusted).pack(side="left", padx=6)
        refresh_trusted()

    def _clear_chat(self, win, refresh=None):
        if messagebox.askyesno("Clear chat", "Delete saved raw chat turns? Facts and notes remain.", parent=win):
            self.core.clear_chat()
            if refresh: refresh()
            messagebox.showinfo("Memory", "Chat history cleared.", parent=win)

    def open_tools(self):
        win = tk.Toplevel(self.root); win.title("Focus tools"); win.geometry("380x310")
        ttk.Label(win, text="Pomodoro timer", font=("Segoe UI", 13, "bold")).pack(pady=12)
        self.timer_label = ttk.Label(win, text=self._format_time(self.timer_remaining), font=("Consolas", 24)); self.timer_label.pack(pady=5)
        row = ttk.Frame(win); row.pack(pady=8)
        ttk.Button(row, text="Start / pause", command=self.toggle_timer).pack(side="left", padx=4)
        ttk.Button(row, text="Reset", command=self.reset_timer).pack(side="left", padx=4)
        ttk.Separator(win).pack(fill="x", padx=14, pady=8)
        ttk.Label(win, text="One-time reminder (saved to memory)").pack(pady=(4, 2))
        task = tk.StringVar(); ttk.Entry(win, textvariable=task).pack(fill="x", padx=15)
        row2 = ttk.Frame(win); row2.pack(pady=8)
        ttk.Button(row2, text="In 30 minutes", command=lambda: self.schedule_reminder(task.get(), 30)).pack(side="left", padx=4)
        ttk.Button(row2, text="In 1 hour", command=lambda: self.schedule_reminder(task.get(), 60)).pack(side="left", padx=4)

    def _format_time(self, n): return f"{max(0,n)//60:02d}:{max(0,n)%60:02d}"
    def toggle_timer(self):
        self.timer_running = not self.timer_running
        if self.timer_running:
            self._timer_tick()
        elif self.timer_job is not None:
            try: self.root.after_cancel(self.timer_job)
            except Exception: pass
            self.timer_job = None
    def reset_timer(self):
        self.timer_running = False
        if self.timer_job is not None:
            try: self.root.after_cancel(self.timer_job)
            except Exception: pass
            self.timer_job = None
        self.timer_remaining = int(self.settings.get("pomodoro_minutes",25))*60
        if hasattr(self,"timer_label"): self.timer_label.configure(text=self._format_time(self.timer_remaining))
    def _timer_tick(self):
        self.timer_job = None
        if not self.timer_running: return
        self.timer_remaining -= 1
        if hasattr(self,"timer_label") and self.timer_label.winfo_exists(): self.timer_label.configure(text=self._format_time(self.timer_remaining))
        if self.timer_remaining <= 0:
            self.timer_running = False
            # The matching reminder is stored persistently in SQLite and delivered by
            # the reminder scheduler/background agent; avoid showing a duplicate alert here.
            self.timer_remaining = 0
            self._status("Timer finished. Your saved reminder will notify you.")
        else: self.timer_job = self.root.after(1000, self._timer_tick)

    def schedule_reminder(self, text, minutes=30):
        if not text.strip(): messagebox.showinfo("Reminder", "Type a task first."); return
        self.memory.add_reminder(text.strip(), time.time() + int(minutes)*60)
        messagebox.showinfo("Reminder set", f"I'll remind you in {minutes} minutes while Study Buddy is running.")

    def open_tasks(self):
        win = tk.Toplevel(self.root); win.title("Tasks and deadlines"); win.geometry("650x500")
        top = ttk.Frame(win, padding=10); top.pack(fill="x")
        title = tk.StringVar(); due = tk.StringVar(); details = tk.StringVar()
        ttk.Label(top, text="Task").grid(row=0,column=0,sticky="w")
        ttk.Entry(top, textvariable=title, width=38).grid(row=0,column=1,sticky="ew",padx=5)
        ttk.Label(top, text="Due date (optional: YYYY-MM-DD HH:MM)").grid(row=1,column=0,sticky="w",pady=5)
        ttk.Entry(top, textvariable=due, width=24).grid(row=1,column=1,sticky="w",padx=5)
        ttk.Label(top, text="Details (optional)").grid(row=2,column=0,sticky="w")
        ttk.Entry(top, textvariable=details).grid(row=2,column=1,sticky="ew",padx=5)
        top.columnconfigure(1, weight=1)
        lst = tk.Listbox(win, height=14); lst.pack(fill="both", expand=True, padx=10, pady=4)
        task_ids = []
        def refresh():
            lst.delete(0,"end"); task_ids.clear()
            for t in self.memory.list_tasks(include_done=True):
                due_label = datetime.datetime.fromtimestamp(t["due_ts"]).strftime("%Y-%m-%d %H:%M") if t["due_ts"] else "no deadline"
                status = "✓" if t["status"] == "done" else "○"
                lst.insert("end", f"{status} {t['title']}  [{due_label}]")
                task_ids.append(t["id"])
        def add_task():
            due_ts = None
            if due.get().strip():
                try: due_ts = datetime.datetime.strptime(due.get().strip(), "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    messagebox.showerror("Task", "Use YYYY-MM-DD HH:MM for the due date.", parent=win); return
            if not self.memory.add_task(title.get(), details.get(), due_ts):
                messagebox.showinfo("Task", "Enter a task title first.", parent=win); return
            title.set(""); due.set(""); details.set(""); refresh()
        def complete():
            sel = lst.curselection()
            if sel and self.memory.complete_task(task_ids[sel[0]]): refresh()
        def delete():
            sel = lst.curselection()
            if sel and messagebox.askyesno("Delete task", "Delete selected task?", parent=win):
                self.memory.delete_task(task_ids[sel[0]]); refresh()
        buttons = ttk.Frame(win, padding=8); buttons.pack(fill="x")
        ttk.Button(buttons, text="Add task", command=add_task).pack(side="left")
        ttk.Button(buttons, text="Mark complete", command=complete).pack(side="left", padx=5)
        ttk.Button(buttons, text="Delete", command=delete).pack(side="left")
        refresh()

    def backup_restore(self):
        action = messagebox.askyesnocancel("Backup / restore", "Yes = create encrypted backup.\nNo = restore an encrypted backup.\nCancel = do nothing.", parent=self.root)
        if action is None: return
        profile = self.settings.get("profile", "hamim")
        if action:
            path = filedialog.asksaveasfilename(parent=self.root, title="Save encrypted backup", defaultextension=".sbbak", filetypes=[("Study Buddy encrypted backup", "*.sbbak")])
            if not path: return
            password = simpledialog.askstring("Backup password", "Choose a password (at least 8 characters):", show="*", parent=self.root)
            if not password: return
            try:
                with self.intelligence_monitor.track("backup", "Create encrypted backup", "Backup", "Backup path and password omitted"):
                    create_backup(ROOT, profile, path, password)
                messagebox.showinfo("Backup", "Encrypted backup saved. Keep the password safe; it cannot be recovered.", parent=self.root)
            except Exception as exc: messagebox.showerror("Backup failed", str(exc), parent=self.root)
        else:
            path = filedialog.askopenfilename(parent=self.root, title="Choose encrypted backup", filetypes=[("Study Buddy encrypted backup", "*.sbbak")])
            if not path: return
            password = simpledialog.askstring("Restore password", "Enter the backup password:", show="*", parent=self.root)
            if not password: return
            if not messagebox.askyesno("Restore backup", "This replaces current memory and settings. A backup of the current state is recommended first. Continue?", parent=self.root): return
            try:
                self.memory.close()
                with self.intelligence_monitor.track("backup", "Restore encrypted backup", "Backup", "Backup path and password omitted"):
                    restore_backup(ROOT, path, password, profile)
                self.settings = read_settings()
                self.memory = Memory(os.path.join(ROOT, "users", profile, "memory.db"))
                self.core = ChatCore(ROOT, memory=self.memory)
                self._attach_intelligence_hooks()
                self.voice_enabled = bool(self.settings.get("voice_enabled", False))
                self.awareness_enabled = bool(self.settings.get("awareness_enabled", False))
                self._status("Restore complete. Restart Study Buddy to fully reload all settings.")
                messagebox.showinfo("Restore", "Memory and settings restored. Restart Study Buddy now.", parent=self.root)
            except Exception as exc:
                try: self.memory = Memory(os.path.join(ROOT, "users", profile, "memory.db"))
                except Exception: pass
                messagebox.showerror("Restore failed", str(exc), parent=self.root)

    def _start_awareness(self):
        if getattr(self,"_awareness_started",False):return
        self._awareness_started=True
        threading.Thread(target=self._awareness_loop,daemon=True).start()
    def _awareness_loop(self):
        while not self.closing:
            try:
                category = self._foreground_category()
                if category != self.last_category:
                    self.last_category = category
                    self.category_since = time.time()
            except Exception:
                pass
            time.sleep(15)

    def _update_proactive_boundary_from_user(self, text):
        """Handle pause commands and concise busy replies before normal chat."""
        boundary = classify_user_boundary(text)
        pending_kind = getattr(self.core, "pending_proactive_kind", None)
        builder = getattr(self.core, "relationship_builder", None)
        now = time.time()
        busy_with_request = bool(re.search(
            r"(?i)\b(?:help|explain|solve|show|write|review|plan|search|find|tell me|can you|could you|please)\b",
            str(text or "")))
        busy_reply = bool(
            pending_kind in ("title_availability", "general_availability")
            and ((builder and builder._busy(text)) or boundary == "busy")
            and not busy_with_request
        )
        outcome = self.proactive_pause_flow.user_turn(
            text, now=now, pause_request=(boundary == "pause"), busy_reply=busy_reply)
        self._pause_reply_only = bool(outcome.get("direct"))
        notice = outcome.get("reply") if self._pause_reply_only else outcome.get("notice")
        if self._pause_reply_only:
            # A deterministic pause reply closes the pending proactive invitation;
            # the next user message must not be interpreted as its answer.
            self.core.pending_proactive_context = None
            self.core.pending_proactive_kind = None
            self.core.pending_proactive_window_title = None
            self.core.pending_proactive_app_name = None
            self.core.pending_proactive_app_category = None
            self.core.pending_proactive_observed_at = None
        if self._pause_reply_only:
            self._status("Proactive check-ins paused." if self.proactive_pause_flow.is_paused(now)
                         else "Waiting for your pause choice; check-ins remain active.")
        elif notice:
            self._status("Proactive check-ins resumed." if "active again" in notice.casefold()
                         else "Check-in status updated.")
        self._draw_proactive_availability_switch()
        if mentions_closed_task(text):
            self.closed_task_reminder = (text or "").strip()[:240]
        return notice

    def _finish_pause_flow_turn(self, user_text, reply):
        """Display a deterministic pause choice/acknowledgement without an LLM call."""
        reply = str(reply or "").strip()
        self.core._remember(user_text, reply)
        self.last_reply = reply
        self.last_turn_completed_ts = time.time()
        self.busy = False
        self.activity_mode = "idle"
        self.face_mode = "happy"
        self.face_expression_until = time.perf_counter() + 1.5
        self._append("Buddy", reply, "buddy")
        if self.proactive_pause_flow.is_paused(time.time()):
            self._status("Proactive check-ins paused; they resume when you message or restart the app.")
        elif self.proactive_pause_flow.awaiting_duration:
            self._status("Check-ins remain active until you choose a pause duration.")
        else:
            self._status("Proactive check-ins are active.")

    def _num_setting(self, key, default, low, high):
        """Read a numeric setting safely, clamped to [low, high]."""
        try: value = float(self.settings.get(key, default))
        except (TypeError, ValueError): value = float(default)
        return max(low, min(high, value))

    def _window_checkin_mode(self, signature, eligible, now):
        """Decide whether a window check-in is due: 'new', 'still_here', 'returned' or None.

        new        - first time this window context is seen, after window_checkin_new_seconds
        still_here - same window kept open; repeats every window_checkin_same_minutes
        returned   - the user left and came back; due window_checkin_return_minutes after the last check-in
        All of them respect window_checkin_min_gap_seconds between any two window check-ins.
        """
        track = self._window_track.get(signature)
        previous_seen = None
        if track:
            previous_seen = float(track.get("seen_ts", now))
            track["seen_ts"] = now
            if now - previous_seen > 60:      # not in this window for a while -> it is a return
                track["returned"] = True
        if not eligible:
            return None
        if now - float(self._last_window_nudge_ts or 0) < self._num_setting("window_checkin_min_gap_seconds", 90, 0, 3600):
            return None
        if track is None:
            return "new"
        since_last = now - float(track.get("last_ts", 0))
        if track.get("returned"):
            return "returned" if since_last >= self._num_setting("window_checkin_return_minutes", 5, 1, 240) * 60 else None
        limit = int(self._num_setting("window_checkin_max_followups", 0, 0, 100))
        if limit and int(track.get("followups", 0)) >= limit:
            return None
        return "still_here" if since_last >= self._num_setting("window_checkin_same_minutes", 10, 1, 240) * 60 else None

    def _window_limiter_key(self, app_name, category, title=""):
        """Rate-limit a title context without saving the raw title in limiter state."""
        key = str(app_name or "unknown")
        title = " ".join(str(title or "").split()).casefold()
        if title:
            key += "|window:" + self.proactive_limiter._digest(title)
        return key, str(category or "Other")

    def _title_context_signature(self, app_name, category, title=""):
        """Digest a stable topic identity so changing browser suffixes do not re-prompt."""
        normalized = " ".join(str(title or "").casefold().split())
        # Browser/media providers and years often change around an otherwise
        # identical title as a tab or playback state updates.
        normalized = re.sub(
            r"\s*(?:[-–—|•·]\s*)(?:youtube|netflix|spotify|twitch|vimeo|disney\+|prime video|"
            r"google chrome|chrome|microsoft edge|edge|firefox|brave)\b.*$", "", normalized, flags=re.I)
        normalized = re.sub(r"\b(?:19|20)\d{2}\b", " ", normalized)
        normalized = re.sub(
            r"\b(?:official\s+)?(?:trailer|teaser|preview|clip|full\s+movie|movie\s+clip)\b.*$",
            " ", normalized, flags=re.I)
        normalized = re.sub(r"\b(?:1080p|720p|2160p|4k|hd|official)\b", " ", normalized)
        normalized = " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))
        if not normalized:
            normalized = " ".join(str(title or "").casefold().split())
        app_topic_key = (str(app_name or "unknown").casefold().strip()
                         + "|topic:" + self.proactive_limiter._digest(normalized))
        return self.proactive_limiter._app_key(app_topic_key, category)

    def _title_decision_is_fresh(self, decision):
        """Recheck the foreground window before delivering an asynchronous guess."""
        if not decision or getattr(decision, "trigger", None) != "title_clue":
            return True
        if (not self.settings.get("proactive_enabled", True)
                or not self.awareness_enabled or not self.settings.get("awareness_enabled", True)
                or self.closing or self.busy or self._proactive_cancel_event.is_set()
                or time.time() - float(self._last_entry_activity_ts or 0) < 10
                or self.proactive_pause_flow.is_paused(time.time())
                or self._is_quiet(datetime.datetime.now().strftime("%H:%M"))):
            return False
        meta = dict(getattr(decision, "meta", {}) or {})
        try:
            current = ContextEngine.capture(self._idle_seconds())
            safe = current.safe_summary()
        except Exception:
            return False
        if (safe.get("private_context") or safe.get("app_category") in ("Private", "Meeting")
                or float(safe.get("idle_seconds", 0)) >= 900):
            return False
        expected_title = " ".join(str(meta.get("window_title") or "").split()).casefold()
        current_title = " ".join(str(safe.get("window_title") or "").split()).casefold()
        expected_app = str(meta.get("app_name") or "").strip().casefold()
        current_app = str(safe.get("app_name") or "").strip().casefold()
        return bool(expected_title and expected_title == current_title
                    and expected_app and expected_app == current_app)

    def _scheduler_tick(self):
        if self.closing: return
        now = time.time()
        if self.proactive_pause_flow.expire_pending(now):
            self._status("Pause request expired; proactive check-ins remain active.")
        if self.proactive_pause_flow.paused_until and now >= self.proactive_pause_flow.paused_until:
            self.proactive_pause_flow.paused_until = 0.0
            self._draw_proactive_availability_switch()
            self._status("Your timed proactive pause ended; check-ins are active again.")
        # The background agent may evolve the character while the GUI is minimized.
        try:
            char_path = os.path.join(ROOT, "config", "character.json")
            mtime = os.path.getmtime(char_path)
            if mtime != getattr(self, "_character_mtime", None):
                with open(char_path, encoding="utf-8") as f:
                    latest_character = json.load(f)
                if latest_character != self.core.character:
                    self.core.character = latest_character
                self._character_mtime = mtime
        except Exception:
            pass
        self._maybe_start_background_memory_review(now)
        # Candidate-memory consent prompts are batched and delivered only after idle time.
        router = getattr(self.core, "intent_router", None)
        facts_enabled = str(self.personal_companion.setting("tier_facts", "1")).lower() not in ("0", "false", "off", "no")
        cooldown = max(1800, int(getattr(self.core, "companion_config", {}).get("consent_prompt_cooldown_seconds", 1800)))
        daily_cap = max(1, min(3, int(getattr(self.core, "companion_config", {}).get("consent_daily_cap", 3))))
        try:
            self.personal_companion.expire_candidates(now)
            last_prompt = float(self.personal_companion.setting("consent_last_prompt_ts", "0") or 0)
            today = datetime.date.today().isoformat()
            count_day = self.personal_companion.setting("consent_prompt_day", "")
            count_today = int(self.personal_companion.setting("consent_prompt_count", "0") or 0) if count_day == today else 0
            pause_until = float(self.personal_companion.setting("consent_pause_until", "0") or 0)
            from companion.consent_dialog import should_deliver_consent
            if (not self._consent_event_queued and should_deliver_consent(
                    now=now, session_started_at=self.session_started_at,
                    last_interaction_ts=max(self.last_interaction_ts, self.last_turn_completed_ts), last_prompt_ts=last_prompt,
                    prompt_count_today=count_today, pause_until=pause_until,
                    idle_seconds=getattr(self.core, "companion_config", {}).get("consent_idle_seconds", 120),
                    cooldown_seconds=cooldown, daily_cap=daily_cap,
                    facts_enabled=facts_enabled, proactive_enabled=self.settings.get("proactive_enabled", True),
                    busy=self.busy, worker_busy=self.proactive_worker_running,
                    dialog_pending=bool(router and router.has_pending or
                                        (self.core.relationship_builder and self.core.relationship_builder.phase)),
                    quiet=self._is_quiet(datetime.datetime.now().strftime("%H:%M")))):
                queued = self.personal_companion.queued_candidates(limit=3)
                queued = [x for x in queued if not x.get("last_prompted_ts") or now-float(x["last_prompted_ts"]) >= cooldown]
                if queued:
                    self._consent_event_queued = True
                    self.events.put(("consent_candidates", queued))
        except Exception:
            self._consent_event_queued = False

        # Deliver consent-based getting-to-know-you follow-ups only when the user is
        # available, outside quiet hours, and not actively chatting. Delivery is one-shot
        # and persisted in the same per-user SQLite database.
        if (not self.busy and not self.proactive_worker_running
                and not (router and router.has_pending)
                and not (self.core.relationship_builder and self.core.relationship_builder.phase)
                and not self._is_quiet(datetime.datetime.now().strftime("%H:%M"))
                and self.settings.get("proactive_enabled", True)):
            try:
                for followup in self.personal_companion.claim_due_followups(now, kinds=("onboarding",)):
                    self.events.put(("companion_followup", followup["id"], ""))
            except Exception:
                pass

        # Deliver persistent reminders even if the app was closed when they became due.
        if now - self.last_reminder_check >= 25:
            self.last_reminder_check = now
            try:
                for reminder in self.memory.claim_due_reminders(now):
                    self.events.put(("reminder_due", reminder["text"]))
                for task in self.memory.list_tasks():
                    due = task.get("due_ts")
                    if due and 0 <= due - now <= 15*60:
                        key = f"task_due_{task['id']}"
                        last = getattr(self, "_task_due_nudges", set())
                        if key not in last:
                            last.add(key); self._task_due_nudges = last
                            self.events.put(("reminder_due", f"Your task '{task['title']}' is due soon."))
            except Exception as exc:
                self.events.put(("status", "Reminder check issue: " + str(exc)))

        # Phase 2: one policy evaluates every initiative trigger. Explicit reminders
        # above remain unchanged and are not counted as initiative nudges.
        policy = getattr(self.core, "initiative_policy", None)
        if policy and self.settings.get("proactive_enabled", True):
            try:
                if self.awareness_enabled:
                    snapshot = self.context_engine.observe(self._idle_seconds())
                    safe = snapshot.safe_summary()
                    category = str(safe.get("app_category") or "Other")
                    # Sampling stores only weekday/hour/coarse category, never titles or app names.
                    self.core.rhythm_tracker.sample(now, active=(snapshot.idle_seconds < 900 and category not in ("Private", "Meeting")), app_category=category)
                else:
                    snapshot = None; safe = {}; category = "Other"
                local_now = datetime.datetime.fromtimestamp(now)
                active_for_feedback = bool(self.awareness_enabled and snapshot and (snapshot.idle_seconds < 300 or self.context_engine.is_studybuddy_window(snapshot)))
                self.core.feedback_logger.evaluate_pending(now, active=active_for_feedback)
                quiet_configured = self.settings.get("quiet_start", "23:00") != self.settings.get("quiet_end", "07:00")
                quiet = self._is_quiet(local_now.strftime("%H:%M")) if quiet_configured else False
                paused_until = float(self.proactive_pause_flow.paused_until)
                if snapshot:
                    signature=(safe.get("app_name","unknown"),safe.get("app_category","Other"),safe.get("window_title",""))
                    if signature != self.last_context_signature:
                        self.last_context_signature=signature
                        self.last_context_since=now
                    stable_for=now-float(self.last_context_since or now)
                else:
                    stable_for=0.0
                app_lower = str(safe.get("app_name") or "").lower()
                title_lower = str(safe.get("window_title") or "").lower()
                own_window = bool(snapshot and self.context_engine.is_studybuddy_window(snapshot)) or (app_lower in ("python", "pythonw") and "study buddy" in title_lower)
                usable_title = str(safe.get("window_title") or "").strip()
                window_context_signature = self._title_context_signature(
                    safe.get("app_name"), category, usable_title)
                router = getattr(self.core, "intent_router", None)
                builder = self.core.relationship_builder
                relationship_deferred = bool(builder and builder.deferred_this_session)
                title_clue = bool(snapshot and usable_title and usable_title != "[title hidden for privacy]"
                    and not safe.get("private_context") and category not in ("Private", "Meeting")
                    # Deferring getting-to-know-you questions must not silence
                    # independent, title-grounded curiosity about a new window.
                    # Explicit proactive pauses are enforced separately below.
                    and not own_window
                    and ContextEngine.has_specific_title_clue(safe)
                    and stable_for >= self._num_setting("window_checkin_new_seconds",
                                                        self.settings.get("proactive_switch_stability_seconds", 15), 5, 3600))
                window_mode = self._window_checkin_mode(window_context_signature, title_clue, now) if snapshot else None
                title_clue = window_mode is not None
                title_can_replace_idle_invite = bool(
                    title_clue and builder and builder.phase in ("availability", "topic_permission", "conversation"))
                relationship_dialog_pending = bool(
                    builder and builder.phase and not title_can_replace_idle_invite)
                session_return = bool(self._phase2_session_return and not self._phase2_session_return_consumed and not relationship_deferred)
                late_time = str(self.settings.get("late_night_nudge_time", "22:30"))
                wind_down = bool(self.settings.get("late_night_nudge_enabled", False) and local_now.strftime("%H:%M") == late_time and self._phase2_wind_down_day != local_now.date().isoformat())
                router = getattr(self.core, "intent_router", None)
                self.proactive_limiter.refresh_day()
                cap_setting=max(0,min(5,int(self.settings.get("initiative_daily_cap", self.core.companion_config.get("daily_cap_default", 3)))))
                self.proactive_limiter.daily_limit=cap_setting
                # Offer opt-in rhythm learning after activity on at least three distinct days.
                router = getattr(self.core, "intent_router", None)
                rhythm_enabled = str(self.personal_companion.setting("tier_rhythm", "0")).lower() not in ("0","false","off","no")
                offer_count = int(self.personal_companion.setting("rhythm_offer_count", "0") or 0)
                declined = str(self.personal_companion.setting("rhythm_offer_declined", "0")).lower() not in ("0","false","off","no")
                last_offer = float(self.personal_companion.setting("rhythm_offer_last_ts", "0") or 0)
                with self.memory._db_lock:
                    active_days = int(self.memory.db.execute("SELECT COUNT(DISTINCT date(ts,'unixepoch','localtime')) FROM turns").fetchone()[0] or 0)
                offer_cooldown_ok = (offer_count == 0 or now-last_offer >= 30*86400)
                offer_quiet = quiet if quiet_configured else policy._quiet(now, {"quiet":None})
                if (not self._phase2_rhythm_offer_queued and not rhythm_enabled and not declined and offer_count < 2
                        and active_days >= 3 and offer_cooldown_ok and now-self.session_started_at >= 60
                        and now-max(self.last_interaction_ts,self.last_turn_completed_ts) >= 120
                        and not self.busy and not self.proactive_worker_running and not (router and router.has_pending)
                        and not (self.core.relationship_builder and self.core.relationship_builder.phase)
                        and not offer_quiet
                        and category not in ("Private","Meeting") and not own_window):
                    self._phase2_rhythm_offer_queued=True
                    self.events.put(("rhythm_offer",))
                state = {
                    "limiter_daily_count": int(self.proactive_limiter.state.get("daily_attempts",0)),
                    "limiter_last_sent_ts": float(self.proactive_limiter.state.get("last_sent_at",0)),
                    "proactive_enabled": bool(self.settings.get("proactive_enabled", True)),
                    "active": bool(not self.awareness_enabled or (snapshot and snapshot.idle_seconds < 900)),
                    "busy": bool(self.busy or self._phase2_nudge_queued or self._phase2_rhythm_offer_queued), "worker_busy": bool(self.proactive_worker_running),
                    "dialog_pending": bool(router and router.has_pending or relationship_dialog_pending
                                            or self.proactive_pause_flow.awaiting_duration),
                    "typing": bool(now-float(self._last_entry_activity_ts or 0)<10),
                    "paused": now < paused_until, "pause_until": paused_until,
                    "last_interaction_ts": max(self.last_interaction_ts, self.last_turn_completed_ts),
                    "idle_seconds": float(snapshot.idle_seconds if snapshot else 0),
                    "last_nudge_ts": max(float(self.personal_companion.setting("initiative_last_nudge_ts", "0") or 0), float(self.proactive_limiter.state.get("last_sent_at",0))),
                    "daily_cap": int(self.settings.get("initiative_daily_cap", self.core.companion_config.get("daily_cap_default", 3))),
                    "session_return": session_return, "wind_down": wind_down,
                    "title_clue_enabled": bool(self.awareness_enabled and title_clue),
                }
                ctx = {"app_category": category, "app_name": safe.get("app_name", "unknown"),
                       "window_title": safe.get("window_title", ""),
                       "private": bool(safe.get("private_context")), "meeting": category == "Meeting",
                       "relationship_deferred": relationship_deferred,
                       "own_window": own_window, "has_window_context": title_clue,
                       "has_specific_title_clue": title_clue, "quiet": quiet, "quiet_explicit": quiet_configured}
                decision = policy.evaluate(now=now, ctx=ctx, state=state)
                # Retention is a single cheap delete once per local day.
                today = local_now.date().isoformat()
                if self._phase2_retention_day != today:
                    self.core.feedback_logger.prune(now)
                    self._phase2_retention_day = today
                if decision:
                    if decision.needs_llm:
                        # Title check-ins are event-triggered: once per distinct
                        # stable app/title context in this app session.
                        if decision.trigger == "title_clue" and window_mode:
                            track = self._window_track.setdefault(window_context_signature,
                                {"first_ts": now, "last_ts": now, "seen_ts": now, "count": 0, "followups": 0})
                            minutes_since = int(max(0, now - float(track.get("last_ts", now))) // 60)
                            track["count"] = int(track.get("count", 0)) + 1
                            if window_mode == "still_here":
                                track["followups"] = int(track.get("followups", 0)) + 1
                            track["last_ts"] = now
                            track["returned"] = False
                            self._last_window_nudge_ts = now
                            self._seen_title_contexts.add(window_context_signature)
                            mode_info = {"mode": window_mode, "minutes_here": int(stable_for // 60),
                                         "minutes_since_last": minutes_since, "checkin_count": track["count"],
                                         "free_text": bool(self.settings.get("window_checkin_llm_text", True))}
                            decision.meta["window_mode"] = window_mode
                            self.last_nudge = now
                            self._proactive_cancel_event.clear()
                            self.proactive_worker_running = True
                            self._status("Checking the current window title quietly…")
                            context = ("PROACTIVE WINDOW MODE: " + json.dumps(mode_info, ensure_ascii=True) + ". "
                                "PROACTIVE DESKTOP CONTEXT (limited, local signal only): "
                                + json.dumps(safe, ensure_ascii=True)
                                + ". Use ONLY the current sanitized app name and window title as evidence. "
                                "Do not retrieve or reuse any old assistant message, prior guess, task, reminder or chat. "
                                "If the title does not identify a specific topic, skip the check-in.")
                            threading.Thread(target=self._proactive_worker,
                                args=(context, str(safe.get("app_name") or "unknown"), category, decision), daemon=True).start()
                    elif decision.text and not self._phase2_nudge_queued:
                        # Deterministic template: no model call or background thread.
                        self._phase2_nudge_queued=True
                        self.events.put(("phase2_nudge", decision))
                if session_return and decision and decision.trigger == "session_return":
                    self._phase2_session_return_consumed = True
                if wind_down and decision and decision.trigger == "wind_down":
                    self._phase2_wind_down_day = local_now.date().isoformat()
            except Exception as exc:
                self.events.put(("status", "Initiative policy skipped this tick: " + str(exc)[:120]))
        self.root.after(5000, self._scheduler_tick)

    def _background_memory_review_status(self):
        """Explain whether background curation is running, waiting, or caught up."""
        if self._memory_review_running:
            return self._memory_review_status
        if self.personal_companion.setting("relationship_memory_consent", "") != "yes":
            return "Off until you opt in to personal memory."
        if str(self.personal_companion.setting("relationship_opt_out", "0")).lower() in ("1", "true", "yes", "on"):
            return "Paused because personal memory is opted out."
        if str(self.personal_companion.setting("tier_facts", "1")).lower() in ("0", "false", "off", "no"):
            return "Paused because personal memory is disabled in Settings."
        try:
            cursor_version = self.personal_companion.setting("conversation_review_cursor_version", "")
            cursor = 0 if cursor_version != "2" else int(self.personal_companion.setting("conversation_review_last_turn_id", "0") or 0)
            remaining = self.memory.count_turns_after(cursor)
            if remaining <= 0:
                return "Caught up. New chat is reviewed after a 90-second pause in chat and model activity."
            retry_in = max(0, int(self._memory_review_retry_after - time.time()))
            if retry_in:
                return f"{remaining} retained message(s) queued; the next local review pass starts after a short cooldown ({retry_in}s)."
            last_activity = max(float(self.last_interaction_ts or 0), float(self.last_turn_completed_ts or 0),
                                float(self._last_entry_activity_ts or 0))
            quiet_for = max(0.0, time.time() - last_activity)
            if quiet_for < MEMORY_REVIEW_QUIET_SECONDS:
                return (f"{remaining} retained message(s) queued. Review starts after {MEMORY_REVIEW_QUIET_SECONDS} seconds without "
                        "chat or model activity; PC inactivity is not required.")
            return f"{remaining} retained message(s) queued for the local background review."
        except Exception:
            return "Waiting for the local background reviewer."

    def _maybe_start_background_memory_review(self, now=None):
        """Review a quiet chat batch only when interactive/model work has paused."""
        now = time.time() if now is None else float(now)
        if self._memory_review_running or now < self._memory_review_retry_after:
            return
        if self.busy or self.proactive_worker_running:
            return
        last_activity = max(float(self.last_interaction_ts or 0), float(self.last_turn_completed_ts or 0),
                            float(self._last_entry_activity_ts or 0))
        if now - last_activity < MEMORY_REVIEW_QUIET_SECONDS:
            return
        if self.personal_companion.setting("relationship_memory_consent", "") != "yes":
            return
        if str(self.personal_companion.setting("relationship_opt_out", "0")).lower() in ("1", "true", "yes", "on"):
            return
        if str(self.personal_companion.setting("tier_facts", "1")).lower() in ("0", "false", "off", "no"):
            return
        try:
            cursor_version = self.personal_companion.setting("conversation_review_cursor_version", "")
            cursor = 0 if cursor_version != "2" else max(
                0, int(self.personal_companion.setting("conversation_review_last_turn_id", "0") or 0))
            batch = self.memory.turns_after(cursor, limit=60)
            if not batch:
                return
            self.personal_companion.set_setting("conversation_review_last_attempt_ts", str(now))
            self._memory_review_cancel_event.clear()
            self._memory_review_status = f"Reviewing {len(batch)} retained chat messages after a quiet pause…"
            self._memory_review_running = True
            threading.Thread(target=self._background_memory_review_worker, daemon=True).start()
        except Exception:
            self._memory_review_running = False

    def _background_memory_review_worker(self):
        def progress(message):
            self.events.put(("memory_review_progress", str(message)))

        try:
            result = self.lifecycle.review_recent_conversation(
                self.personal_companion, limit_turns=60,
                cancel_event=self._memory_review_cancel_event,
                progress_callback=progress)
            status = result.get("status", "processed")
            created = int(result.get("created", 0) or 0)
            processed = int(result.get("processed_turns", 0) or 0)
            if status == "cancelled":
                if self.closing:
                    summary = "Review was stopped while the app was closing; this batch can be retried next launch."
                    progress("Review stopped during shutdown. No turns from this batch were marked reviewed.")
                else:
                    summary = "Review paused when chat resumed; it will continue after the next quiet pause."
                    progress("Chat resumed, so memory review paused. No turns from this batch were marked reviewed.")
                    self._memory_review_retry_after = 0.0
            elif status == "current":
                summary = "All retained chat was already reviewed."
                progress("All retained chat turns have been reviewed.")
            elif status == "skipped":
                summary = str(result.get("reason") or "Review skipped.")
                progress(summary)
            elif created:
                labels = {
                    "study_work": "Everyday context", "area": "Everyday context", "routine": "Everyday context",
                    "location": "Everyday context", "goal": "Goals & progress", "achievement": "Goals & progress",
                    "people": "People & belonging", "interest": "Interests & enjoyment",
                    "preference": "Support & communication", "support_style": "Support & communication",
                    "value": "Values & meaning", "meaning": "Values & meaning",
                    "good_moment": "Good moments", "hard_moment": "Difficult moments", "episode": "Life moments",
                }
                kinds = result.get("created_by_kind", {})
                categories = list(dict.fromkeys(labels.get(kind, "Other details") for kind in kinds))
                category_text = ", ".join(categories)
                summary = f"Reviewed {processed} messages · added {created} detail(s) to {category_text}."
                progress(f"Finished this pass: added {created} detail(s) to {category_text} after reviewing {processed} messages.")
            else:
                summary = f"Reviewed {processed} messages · nothing new was clear enough to save."
                progress(f"Finished this pass: reviewed {processed} messages; nothing new was clear enough to save.")
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            summary += " · " + stamp
            if status == "processed":
                cursor = max(0, int(self.personal_companion.setting("conversation_review_last_turn_id", "0") or 0))
                remaining = self.memory.count_turns_after(cursor)
                # A long backlog is drained gradually; never fire consecutive
                # local-model passes in the same quiet window.
                self._memory_review_retry_after = (
                    time.time() + MEMORY_REVIEW_QUIET_SECONDS if remaining > 0 else 0.0)
            self.personal_companion.set_setting("conversation_review_last_summary", summary)
            self.personal_companion.set_setting("conversation_review_last_completed_ts", str(time.time()))
            self._memory_review_last_summary = summary
        except Exception as exc:
            # Curation is optional; a failed or interrupted review never affects chat.
            summary = "Review could not finish; it will retry in the background."
            self._memory_review_last_summary = summary
            self._memory_review_retry_after = time.time() + 30.0
            try:
                self.personal_companion.set_setting("conversation_review_last_summary", summary)
                self.personal_companion.set_setting("conversation_review_last_error", str(exc)[:240])
            except Exception:
                pass
            progress(summary)
        finally:
            self._memory_review_running = False

    def _proactive_worker(self, context, app_name="unknown", category="Other", decision=None):
        try:
            text = self.core.generate_proactive_message(context, cancel_event=self._proactive_cancel_event)
            if self._proactive_cancel_event.is_set():
                self.events.put(("proactive_skipped", "User chat took priority."))
            elif text and text.strip():
                self.events.put(("proactive_reply", text, app_name, category, decision))
            else:
                self.events.put(("proactive_skipped", "The local model couldn't make a grounded activity guess from this window."))
        except Exception as exc:
            # Background failures must never reset or interrupt an active chat.
            self.events.put(("proactive_skipped", "Proactive guess skipped because local generation failed: " + str(exc)[:140]))

    def _desktop_context_for_chat(self):
        """Return current foreground metadata, or a clearly labelled recent external title.

        Windows gives the Study Buddy GUI as the foreground window while the user types
        here. To answer an on-demand tab question, use only the last safe external title
        observed by the awareness collector, and include its age so the answer never
        pretends a cached title is guaranteed current. Proactive generation does not use
        this cached fallback.
        """
        if not self.awareness_enabled or not self.settings.get("awareness_enabled", True):
            return "Desktop Awareness is disabled in Settings; no current app/title context is available."
        try:
            current = self.context_engine.observe(self._idle_seconds())
            if self.context_engine.is_studybuddy_window(current):
                recent_external = self.context_engine.last_external_summary(max_age_seconds=600)
                if recent_external:
                    return json.dumps(recent_external, ensure_ascii=True, separators=(",", ":"))
            summary = current.safe_summary()
            summary["observation_source"] = "current_foreground"
            summary["age_seconds"] = 0
            return json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
        except Exception:
            return "Desktop Awareness is enabled, but the current foreground-app snapshot is unavailable."

    def _idle_seconds(self):
        if os.name!="nt":return 0
        class LASTINPUTINFO(ctypes.Structure): _fields_=[("cbSize",ctypes.c_uint),("dwTime",ctypes.c_uint)]
        li=LASTINPUTINFO(); li.cbSize=ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)): return 0
        return max(0,(ctypes.windll.kernel32.GetTickCount()-li.dwTime)/1000)
    def _foreground_category(self):
        if os.name!="nt":return "Other"
        try:
            hwnd=ctypes.windll.user32.GetForegroundWindow(); pid=ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            import psutil
            name=psutil.Process(pid.value).name().lower()
            if any(x in name for x in ("chrome","msedge","firefox","brave","opera")):return "Browser"
            if any(x in name for x in ("code","pycharm","notepad++","devenv")):return "Coding"
            if any(x in name for x in ("word","powerpnt","excel","acrobat","notepad")):return "Study/Docs"
            if any(x in name for x in ("zoom","teams","meet")):return "Meeting"
            if any(x in name for x in ("vlc","spotify","wmplayer")):return "Video/Game"
            return "Other"
        except Exception:return "Other"
    def _is_quiet(self,now):
        try:
            start=datetime.datetime.strptime(self.settings.get("quiet_start","23:00"),"%H:%M").time()
            end=datetime.datetime.strptime(self.settings.get("quiet_end","07:00"),"%H:%M").time(); n=datetime.datetime.strptime(now,"%H:%M").time()
            return (n>=start or n<end) if start>end else (start<=n<end)
        except Exception:return False

    def _apply_autostart(self, enabled):
        """Create/remove per-user Startup entries for the GUI and background agent."""
        if os.name != "nt":
            return
        try:
            startup = os.path.join(os.environ.get("APPDATA", ""),
                "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
            if not startup: return
            os.makedirs(startup, exist_ok=True)
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw): pythonw = sys.executable
            entries = {
                "StudyBuddy.cmd": os.path.join(ROOT, "app", "desktop_app.py"),
                "StudyBuddyBackground.cmd": os.path.join(ROOT, "app", "background_agent.py"),
            }
            for filename, script in entries.items():
                entry = os.path.join(startup, filename)
                should_enable = enabled if filename == "StudyBuddy.cmd" else bool(self.settings.get("background_agent_enabled", True))
                if should_enable:
                    content = '@echo off\nstart "" "' + pythonw + '" "' + script + '"\n'
                    with open(entry, "w", encoding="utf-8") as f: f.write(content)
                elif os.path.exists(entry):
                    os.remove(entry)
        except Exception as exc:
            if hasattr(self, "events"):
                self.events.put(("status", "Could not update Windows startup setting: " + str(exc)))

    def _start_background_agent(self):
        """Start the lightweight reminder agent now; a mutex prevents duplicate instances."""
        if os.name != "nt" or not self.settings.get("background_agent_enabled", True):
            return
        try:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw): pythonw = sys.executable
            script = os.path.join(ROOT, "app", "background_agent.py")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([pythonw, script], cwd=ROOT, creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            if hasattr(self, "events"):
                self.events.put(("status", "Background reminders could not start: " + str(exc)))

    def _run_consolidation_now(self):
        if not messagebox.askyesno("Memory consolidation",
                "Summarize the newest chats, including today's chats, and delete raw turns only after each summary is saved? This can remove up to 3 days of raw chat.", parent=self.root):
            return
        self._status("Consolidating recent chats…")
        def worker():
            try:
                report = self.lifecycle.consolidate_daily(limit_days=3, include_today=True)
                days = report["days_summarized"]
                if days:
                    message = (f"Memory consolidation finished: {len(days)} day(s) summarized "
                               f"({', '.join(days)}); {report['turns_deleted']} raw turn(s) removed.")
                elif report["errors"]:
                    message = "Memory consolidation could not summarize chat: " + str(report["errors"][0].get("error", "Unknown error"))[:180]
                else:
                    message = ("No raw chat turns were eligible. If you have chatted today, "
                               "check that the updated chat_core.py is installed and that messages are being saved to this profile's memory.db.")
                self.events.put(("status", message))
            except Exception as exc:
                self.events.put(("status", "Memory consolidation failed: " + str(exc)[:180]))
        threading.Thread(target=worker, daemon=True).start()

    def _rollback_personality(self):
        if not messagebox.askyesno("Rollback personality", "Restore the previous accepted personality version?", parent=self.root):
            return
        try:
            result = self.lifecycle.rollback_latest()
            if not result.get("restored"):
                messagebox.showinfo("Personality", result.get("reason", "No rollback available."), parent=self.root)
                return
            with open(os.path.join(ROOT, "config", "character.json"), encoding="utf-8") as f:
                self.core.character = json.load(f)
            messagebox.showinfo("Personality", "Previous personality version restored.", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Personality", "Rollback failed: " + str(exc)[:180], parent=self.root)

    def hide_to_tray(self):
        # Tray is optional. If unavailable, minimize instead of unexpectedly exiting.
        try:
            import pystray
            from PIL import Image, ImageDraw
            if self.tray_icon is None:
                img=Image.new("RGB",(64,64),(17,24,39)); d=ImageDraw.Draw(img); d.ellipse((8,8,56,56),outline=(125,211,252),width=4); d.ellipse((20,24,27,32),fill=(125,211,252)); d.ellipse((37,24,44,32),fill=(125,211,252)); d.arc((20,30,44,47),0,180,fill=(167,243,208),width=3)
                menu=pystray.Menu(pystray.MenuItem("Open Study Buddy",lambda icon,item:self.root.after(0,self.show_window)),pystray.MenuItem("Quit",lambda icon,item:self.root.after(0,self.quit)))
                self.tray_icon=pystray.Icon("StudyBuddy",img,"Study Buddy",menu)
                self.tray_thread=threading.Thread(target=self.tray_icon.run,daemon=True); self.tray_thread.start()
            self.root.withdraw(); self._status("Running in system tray")
        except Exception:
            self.root.iconify(); self._status("Minimized (install optional tray dependencies for tray mode)")
    def show_window(self): self.root.deiconify(); self.root.lift()
    def quit(self):
        self.closing=True
        self._memory_review_cancel_event.set()
        self._proactive_cancel_event.set()
        try: self.personal_companion.set_setting("initiative_last_session_ts",str(time.time()))
        except Exception: pass
        try:
            if self.tray_icon:self.tray_icon.stop()
        except Exception:pass
        try:self.memory.close()
        except Exception:pass
        try:self.intelligence_monitor.close()
        except Exception:pass
        self.root.destroy()
    def run(self): self.root.mainloop()

if __name__=="__main__": StudyBuddyApp().run()
