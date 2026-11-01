"""Tkinter Intelligence Center UI; all values come from live app providers."""
from __future__ import annotations

import datetime
import tkinter as tk
from tkinter import ttk


_STATUS_COLOURS = {
    "active": "#16a34a", "available": "#2563eb", "disabled": "#64748b",
    "unavailable": "#dc2626", "checking": "#d97706", "degraded": "#d97706",
    "error": "#dc2626", "running": "#d97706", "success": "#16a34a",
    "failed": "#dc2626",
}


# One authoritative graph definition shared by drawing, click-details and tests.
# It deliberately contains exactly one primary LLM node.
ARCHITECTURE_NODES = {
    "chat": {"title": "USER CHAT", "subtitle": "Input / streaming reply", "capability": None,
             "description": "The desktop chat UI accepts the user's message and streams provisional model text as it arrives. Final output is safety-checked before being committed."},
    "intent": {"title": "INTENT + DIALOG", "subtitle": "Editable keyword dictionary", "capability": None,
                "description": "A lightweight, configurable dictionary detects likely task/reminder/timer intents. Ambiguous requests enter a stateful YES/NO/details flow; clear commands continue directly."},
    "llm": {"title": "PRIMARY LLM", "subtitle": "Ollama · one chat model", "capability": "local_ai",
            "description": "The only primary language model. It handles normal conversation and native tool calling. No second chat LLM or required embedding model is part of this architecture."},
    "tools": {"title": "TOOL EXECUTOR", "subtitle": "Validated Python handlers", "capability": "agent_tool_calling",
              "description": "Registered native tools are validated and executed by Python. The executor returns real results; it cannot claim an action succeeded without confirmation."},
    "store": {"title": "SQLITE STATE", "subtitle": "Durable data · explicit recall", "capability": "memory",
              "description": "SQLite stores conversation turns, profile facts, notes, tasks, completion state and reminders. Past chats and notes are not automatically injected into small talk; memory retrieval happens only for an explicit recall request or a user-selected memory workflow."},
    "desktop": {"title": "DESKTOP SIGNALS", "subtitle": "Fresh app / safe title", "capability": "desktop_awareness",
                 "description": "A lightweight collector supplies coarse foreground-application, idle-time and privacy-filtered window-title metadata. An explicit desktop question may use a recent in-memory external-window snapshot when Study Buddy itself is foreground. It never reads page contents or screen pixels."},
    "proactive": {"title": "PROACTIVE AGENT", "subtitle": "Low-priority · current title only", "capability": "proactive",
                  "description": "The background generator receives only a fresh, privacy-filtered current app/window title. It does not receive past chats, assistant guesses, reminders, task titles or saved notes. Generic titles yield no guess; unsupported title claims are discarded; it yields to interactive chat and obeys persistent cooldown/privacy checks."},
    "camera": {"title": "CAMERA FEATURE", "subtitle": "Explicit consent only", "capability": "camera_preview",
                "description": "Camera preview starts only after explicit permission. Optional expression features run locally when their model is installed; raw frames are not sent to the chat LLM."},
    "voice": {"title": "VOICE", "subtitle": "Optional speech I/O", "capability": "voice_input",
               "description": "Optional local speech input/output. Voice features are separate IO adapters and do not create a second reasoning model."},
}


class IntelligenceDashboard:
    def __init__(self, parent, registry, monitor, memory=None, model_name="llama3.2:3b", intent_dictionary_path=None,
                 core=None, settings=None):
        self.parent = parent
        self.registry = registry
        self.monitor = monitor
        self.memory = memory
        self.model_name = str(model_name or "llama3.2:3b")
        self.intent_dictionary_path = intent_dictionary_path
        self.core = core
        self.settings = settings or {}
        self.selected_node_id = "llm"
        self._node_rects = {}
        self.window = tk.Toplevel(parent)
        self.window.title("Study Buddy — Intelligence Center")
        self.window.geometry("1040x720")
        self.window.minsize(850, 590)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = None
        self._build()
        self.refresh()
        self._schedule_refresh()

    def _build(self):
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Intelligence Center", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(title_box, text="Live capabilities, tool execution and system awareness",
                  foreground="#64748b").pack(anchor="w")
        ttk.Button(header, text="Refresh now", command=self.refresh).pack(side="right", padx=(8, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.overview_tab = ttk.Frame(self.notebook, padding=10)
        self.capabilities_tab = ttk.Frame(self.notebook, padding=10)
        self.activity_tab = ttk.Frame(self.notebook, padding=10)
        self.memory_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.overview_tab, text="Overview & Graph")
        self.notebook.add(self.capabilities_tab, text="Capabilities")
        self.notebook.add(self.activity_tab, text="Tool Activity")
        self.notebook.add(self.memory_tab, text="Memory & Privacy")
        self._build_overview()
        self._build_capabilities()
        self._build_activity()
        self._build_memory_tab()

        self.footer_var = tk.StringVar(value="Telemetry is local. Tool arguments and private conversation text are not stored.")
        ttk.Label(outer, textvariable=self.footer_var, foreground="#64748b").pack(fill="x", pady=(8, 0))

    def _metric(self, parent, title, key):
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 8))
        value = tk.StringVar(value="—")
        ttk.Label(frame, textvariable=value, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        self.metric_vars[key] = value
        return frame

    def _build_overview(self):
        self.metric_vars = {}
        metrics = ttk.Frame(self.overview_tab)
        metrics.pack(fill="x", pady=(0, 10))
        for title, key in (("Capabilities", "capabilities"), ("Active", "active"),
                           ("Tool calls", "calls"), ("Failed calls", "failed"),
                           ("Avg LLM latency", "llm_latency")):
            self._metric(metrics, title, key).pack(side="left", fill="x", expand=True, padx=(0, 7))

        graph_frame = ttk.LabelFrame(self.overview_tab, text="Interactive runtime architecture · click any node", padding=8)
        graph_frame.pack(fill="both", expand=True)
        self.graph = tk.Canvas(graph_frame, height=390, background="#0b1220", highlightthickness=0)
        self.graph.pack(fill="both", expand=True)
        self.graph.bind("<Configure>", lambda _e: self._draw_graph())
        self.graph_caption = tk.StringVar(value="One primary LLM · editable intent dictionary · validated tools · SQLite · low-priority proactive path")
        ttk.Label(self.overview_tab, textvariable=self.graph_caption, foreground="#64748b",
                  wraplength=960, justify="left").pack(anchor="w", fill="x", pady=(7, 4))
        self.graph_detail_var = tk.StringVar(value="Click a node to inspect its role and live status.")
        detail = ttk.LabelFrame(self.overview_tab, text="Selected component", padding=(10, 6))
        detail.pack(fill="x")
        ttk.Label(detail, textvariable=self.graph_detail_var, wraplength=950, justify="left").pack(anchor="w", fill="x")

    def _build_capabilities(self):
        columns = ("category", "status", "description", "detail")
        self.cap_tree = ttk.Treeview(self.capabilities_tab, columns=columns, show="tree headings", height=17)
        self.cap_tree.heading("#0", text="Capability")
        self.cap_tree.column("#0", width=205, anchor="w")
        for col, width in (("category", 105), ("status", 105), ("description", 300), ("detail", 380)):
            self.cap_tree.heading(col, text=col.title())
            self.cap_tree.column(col, width=width, anchor="w")
        self.cap_tree.pack(fill="both", expand=True)
        self.cap_detail_var = tk.StringVar(value="Select a capability to inspect its current state.")
        ttk.Label(self.capabilities_tab, textvariable=self.cap_detail_var,
                  wraplength=900, foreground="#64748b").pack(fill="x", pady=(8, 0))
        self.cap_tree.bind("<<TreeviewSelect>>", self._show_capability_detail)

    def _build_activity(self):
        columns = ("time", "tool", "source", "status", "duration", "summary")
        self.event_tree = ttk.Treeview(self.activity_tab, columns=columns, show="headings", height=18)
        widths = {"time": 145, "tool": 190, "source": 115, "status": 90, "duration": 90, "summary": 340}
        for col in columns:
            self.event_tree.heading(col, text=col.title())
            self.event_tree.column(col, width=widths[col], anchor="w")
        yscroll = ttk.Scrollbar(self.activity_tab, orient="vertical", command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=yscroll.set)
        self.event_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

    def _build_memory_tab(self):
        ttk.Label(self.memory_tab, text="Local memory database", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.memory_tab,
                  text="Read-only diagnostic summary. This page displays counts only, never stored conversation or note text.",
                  foreground="#64748b", wraplength=900).pack(anchor="w", pady=(3, 12))
        self.memory_count_vars = {}
        grid = ttk.Frame(self.memory_tab)
        grid.pack(fill="x", anchor="n")
        for index, (key, label) in enumerate((
            ("profile", "Profile records"), ("turns", "Conversation turns"),
            ("facts", "Saved facts"), ("notes", "Memory notes"),
            ("tasks", "Tasks"), ("reminders", "Reminders"),
        )):
            frame = ttk.LabelFrame(grid, text=label, padding=12)
            frame.grid(row=index // 3, column=index % 3, sticky="nsew", padx=5, pady=5)
            value = tk.StringVar(value="—")
            ttk.Label(frame, textvariable=value, font=("Segoe UI", 18, "bold")).pack(anchor="w")
            self.memory_count_vars[key] = value
        for col in range(3):
            grid.columnconfigure(col, weight=1)
        ttk.Label(self.memory_tab,
                  text="For viewing or editing actual memories, use the existing Memory button in the main Study Buddy window.\n"
                       "Camera frames, audio recordings, raw prompts and tool arguments are not stored in the Intelligence Center.",
                  foreground="#64748b", justify="left", wraplength=900).pack(anchor="w", pady=(14, 0))

    def _show_capability_detail(self, _event=None):
        selection = self.cap_tree.selection()
        if not selection:
            return
        values = self.cap_tree.item(selection[0], "values")
        self.cap_detail_var.set(f"{values[0]} · {values[1]} — {values[2]}. {values[3]}")

    def _graph_node_status(self, node_id, by_id, running_tools):
        spec = ARCHITECTURE_NODES[node_id]
        cap_id = spec.get("capability")
        if node_id == "intent":
            return "active" if self.intent_dictionary_path and __import__("os").path.isfile(self.intent_dictionary_path) else "degraded"
        if node_id == "chat":
            return "active"
        if node_id == "llm" and ("agent_tool_calling" in running_tools or "local_ai" in running_tools):
            return "running"
        if cap_id and cap_id in by_id:
            status = by_id[cap_id].get("status", "unavailable")
            if cap_id in running_tools:
                return "running"
            return status
        if node_id == "proactive":
            return by_id.get("proactive_agent", {}).get("status", "available")
        return "available"

    def _select_graph_node(self, node_id):
        if node_id not in ARCHITECTURE_NODES:
            return
        self.selected_node_id = node_id
        self._draw_graph()
        spec = ARCHITECTURE_NODES[node_id]
        by_id = {item["id"]: item for item in getattr(self, "_capabilities", [])}
        running = self.monitor.running_tool_ids()
        status = self._graph_node_status(node_id, by_id, running)
        status_text = status.replace("_", " ").title()
        live_detail = by_id.get(spec.get("capability"), {}).get("detail", "") if spec.get("capability") else ""
        model_text = f" Primary model: {self.model_name}." if node_id == "llm" else ""
        dictionary_text = (f" Editable file: {self.intent_dictionary_path}." if node_id == "intent" and self.intent_dictionary_path else "")
        self.graph_detail_var.set(f"{spec['title']} · {status_text}. {spec['description']}{model_text}{dictionary_text} {live_detail}".strip())

    def _draw_graph(self):
        canvas = self.graph
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(700, canvas.winfo_width())
        height = max(360, canvas.winfo_height())
        center_x = width / 2
        center_y = height / 2
        by_id = {item["id"]: item for item in getattr(self, "_capabilities", [])}
        running_tools = self.monitor.running_tool_ids()
        left_x = max(105, center_x - min(245, width * 0.29))
        right_x = min(width - 105, center_x + min(245, width * 0.29))
        positions = {
            "chat": (center_x, 35),
            "intent": (center_x, 98),
            "llm": (center_x, 166),
            "tools": (center_x, 238),
            "store": (center_x, min(height - 28, 330)),
            "desktop": (left_x, 155),
            "proactive": (right_x, 155),
            "camera": (left_x, 260),
            "voice": (right_x, 260),
        }
        sizes = {"chat": (184, 44), "intent": (220, 46), "llm": (220, 48), "tools": (205, 46),
                 "store": (255, 48), "desktop": (174, 54), "proactive": (184, 54),
                 "camera": (174, 54), "voice": (174, 54)}
        rects = {}
        for node_id, (x, y) in positions.items():
            w, h = sizes[node_id]
            rects[node_id] = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        self._node_rects = rects
        canvas.create_text(12, 10, text="LIVE SYSTEM FLOW  ·  BORDER = STATE  ·  CLICK A NODE FOR DETAILS", anchor="nw",
                           fill="#94a3b8", font=("Segoe UI", 8, "bold"))

        # Draw edges before nodes so arrowheads terminate at node borders visually.
        edges = [
            ("chat", "intent", "#64748b", None),
            ("intent", "llm", "#60a5fa", None),
            ("llm", "tools", "#60a5fa", None),
            ("tools", "store", "#34d399", None),
            ("tools", "llm", "#94a3b8", (4, 3)),
            ("desktop", "llm", "#a78bfa", None),
            ("desktop", "proactive", "#a78bfa", (3, 3)),
            ("proactive", "llm", "#fbbf24", (4, 3)),
            ("camera", "tools", "#64748b", None),
            ("voice", "chat", "#64748b", None),
        ]
        def edge_points(rect_a, rect_b):
            ax, ay = (rect_a[0] + rect_a[2]) / 2, (rect_a[1] + rect_a[3]) / 2
            bx, by = (rect_b[0] + rect_b[2]) / 2, (rect_b[1] + rect_b[3]) / 2
            dx, dy = bx - ax, by - ay
            if not dx and not dy:
                return ax, ay, bx, by
            aw, ah = (rect_a[2] - rect_a[0]) / 2, (rect_a[3] - rect_a[1]) / 2
            bw, bh = (rect_b[2] - rect_b[0]) / 2, (rect_b[3] - rect_b[1]) / 2
            ta = min(aw / abs(dx) if dx else float("inf"), ah / abs(dy) if dy else float("inf"))
            tb = min(bw / abs(dx) if dx else float("inf"), bh / abs(dy) if dy else float("inf"))
            return ax + dx * ta, ay + dy * ta, bx - dx * tb, by - dy * tb

        for source, target, colour, dash in edges:
            a, b = rects[source], rects[target]
            x1, y1, x2, y2 = edge_points(a, b)
            kwargs = {"fill": colour, "width": 2 if dash is None else 1, "arrow": "last"}
            if dash:
                kwargs["dash"] = dash
            canvas.create_line(x1, y1, x2, y2, **kwargs)

        for node_id, (x, y) in positions.items():
            spec = ARCHITECTURE_NODES[node_id]
            status = self._graph_node_status(node_id, by_id, running_tools)
            border = _STATUS_COLOURS.get(status, "#475569")
            selected = node_id == self.selected_node_id
            if selected:
                border = "#fbbf24"
            w, h = sizes[node_id]
            tags = (f"node:{node_id}", "architecture_node")
            canvas.create_rectangle(x-w/2, y-h/2, x+w/2, y+h/2, fill="#162338",
                                    outline=border, width=3 if selected else 2, tags=tags)
            if node_id == "llm":
                title = f"PRIMARY LLM · {self.model_name}"
                title = title if len(title) < 33 else "PRIMARY LLM · Llama 3.2 3B"
            else:
                title = spec["title"]
            subtitle = spec["subtitle"]
            if node_id in ("llm", "tools", "store", "desktop", "proactive", "camera", "voice"):
                subtitle = f"{subtitle} · {status.upper()}"
            canvas.create_text(x, y-7, text=title, fill="#f8fafc", font=("Segoe UI", 8, "bold"), tags=tags)
            canvas.create_text(x, y+11, text=subtitle, fill="#cbd5e1", font=("Segoe UI", 7), tags=tags)
            canvas.tag_bind(f"node:{node_id}", "<Button-1>", lambda _event, key=node_id: self._select_graph_node(key))
            canvas.tag_bind(f"node:{node_id}", "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
            canvas.tag_bind(f"node:{node_id}", "<Leave>", lambda _event: canvas.configure(cursor=""))

        canvas.create_text(width - 10, height - 7,
                           text="Solid = request/data flow   ·   Dashed = feedback/background path",
                           anchor="se", fill="#94a3b8", font=("Segoe UI", 7))
        # Keep the detail pane synchronized after periodic live refreshes.
        if hasattr(self, "graph_detail_var"):
            spec = ARCHITECTURE_NODES.get(self.selected_node_id, ARCHITECTURE_NODES["llm"])
            status = self._graph_node_status(self.selected_node_id, by_id, running_tools).replace("_", " ").title()
            live_detail = by_id.get(spec.get("capability"), {}).get("detail", "") if spec.get("capability") else ""
            model_text = f" Primary model: {self.model_name}." if self.selected_node_id == "llm" else ""
            dictionary_text = (f" Editable file: {self.intent_dictionary_path}." if self.selected_node_id == "intent" and self.intent_dictionary_path else "")
            self.graph_detail_var.set(f"{spec['title']} · {status}. {spec['description']}{model_text}{dictionary_text} {live_detail}".strip())

    def refresh(self):
        if not self.window.winfo_exists():
            return
        self._capabilities = self.registry.snapshot()
        stats = self.monitor.stats()
        active = sum(1 for c in self._capabilities if c["status"] == "active")
        self.metric_vars["capabilities"].set(str(len(self._capabilities)))
        self.metric_vars["active"].set(str(active))
        self.metric_vars["calls"].set(str(stats["total"]))
        self.metric_vars["failed"].set(str(stats["failed"]))
        latency = self.monitor.latency_summary("local_ai") if hasattr(self.monitor, "latency_summary") else {"samples": 0, "average_ms": None, "last_ms": None}
        average = latency.get("average_ms")
        llm_client = getattr(self.core, "llm", None) if self.core is not None else None
        llm_last_metrics = getattr(llm_client, "last_metrics", {}) if llm_client is not None else {}
        last_request_ms = llm_last_metrics.get("request_ms") if isinstance(llm_last_metrics, dict) else None
        if average is not None:
            self.metric_vars["llm_latency"].set(f"{average} ms avg")
        elif last_request_ms is not None:
            self.metric_vars["llm_latency"].set(f"{last_request_ms} ms last")
        else:
            self.metric_vars["llm_latency"].set("No data yet")
        agent = getattr(self.core, "tool_orchestrator", None) if self.core is not None else None
        base_mode = getattr(agent, "retrieval_mode", self.settings.get("tool_retrieval_mode", "unknown"))
        mode = ("editable dictionary + BM25 fallback" if agent and getattr(agent, "route_config", None)
                else base_mode)
        route = list(getattr(agent, "last_route", []) or []) if agent else []
        rounds = getattr(agent, "last_model_rounds", None) if agent else None
        calls = getattr(agent, "last_tool_calls", None) if agent else None
        route_text = ", ".join(route) if route else "none on last request"
        measured_rounds = isinstance(rounds, int) and rounds > 0
        round_text = str(rounds) if measured_rounds else "not yet measured"
        call_text = str(calls) if measured_rounds and calls is not None else "not yet measured"
        avg_text = (f"{average} ms average over {latency.get('samples', 0)} measured calls" if average is not None
                    else (f"last request {last_request_ms} ms (not an average)" if last_request_ms is not None
                          else "no measured LLM latency yet"))
        self.graph_caption.set(
            f"Primary model: {self.model_name} · tool routing: {mode} · last routed tools: {route_text} · "
            f"last tool calls/rounds: {call_text}/{round_text} · LLM: {avg_text}"
        )

        selected = self.cap_tree.selection()
        for item in self.cap_tree.get_children():
            self.cap_tree.delete(item)
        for cap in self._capabilities:
            self.cap_tree.insert("", "end", iid=cap["id"], text=cap["name"],
                                 values=(cap["category"], cap["status"], cap["description"], cap["detail"]))
        if selected and self.cap_tree.exists(selected[0]):
            self.cap_tree.selection_set(selected[0])

        for item in self.event_tree.get_children():
            self.event_tree.delete(item)
        for event in self.monitor.recent_events(100):
            stamp = datetime.datetime.fromtimestamp(event["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
            duration = f"{event['duration_ms']} ms" if event["duration_ms"] is not None else "—"
            summary = event["summary"]
            if event["status"] == "failed" and event["error"]:
                summary = (summary + " · " + event["error"]).strip(" ·")
            self.event_tree.insert("", "end", values=(stamp, event["tool_name"], event["source"],
                                                         event["status"], duration, summary))
        self._refresh_memory_counts()
        self._draw_graph()
        self.footer_var.set(
            f"Updated {datetime.datetime.now().strftime('%H:%M:%S')} · "
            "Local telemetry only · Raw prompts, memory contents and camera frames are not logged"
        )

    def _refresh_memory_counts(self):
        if self.memory is None:
            return
        tables = {"profile": "profile", "turns": "turns", "facts": "facts",
                  "notes": "notes", "tasks": "tasks", "reminders": "reminders"}
        try:
            with self.memory._db_lock:
                for key, table in tables.items():
                    row = self.memory.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    self.memory_count_vars[key].set(str(int(row[0])))
        except Exception:
            for value in self.memory_count_vars.values():
                value.set("Unavailable")

    def _schedule_refresh(self):
        if self.window.winfo_exists():
            self._after_id = self.window.after(1800, self._refresh_loop)

    def _refresh_loop(self):
        if not self.window.winfo_exists():
            return
        self.refresh()
        self._schedule_refresh()

    def close(self):
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
        self.window.destroy()
