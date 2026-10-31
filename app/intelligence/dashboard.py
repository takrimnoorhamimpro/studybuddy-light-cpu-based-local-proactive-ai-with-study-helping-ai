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
    "intent": {"title": "CHAT ROUTER", "subtitle": "Editable keyword dictionary · fast paths", "capability": None,
                "description": "Every incoming chat turn reaches ChatCore routing. The editable keyword dictionary helps route intent; greetings, exact commands and pending dialogs can be handled locally, while other turns go to the model and may expose a small set of relevant tools."},
    "llm": {"title": "PRIMARY LLM", "subtitle": "Ollama · one chat model", "capability": "local_ai",
            "description": "The only primary language model. It handles normal conversation and native tool calling. No second chat LLM or required embedding model is part of this architecture."},
    "tools": {"title": "TOOL EXECUTOR", "subtitle": "Only when a tool is needed", "capability": "agent_tool_calling",
              "description": "The model may request a registered native tool. Python validates and runs the handler, then returns its result. Many chat turns do not enter this path."},
    "store": {"title": "SQLITE STATE", "subtitle": "Durable data · explicit recall", "capability": "memory",
              "description": "SQLite stores conversation turns, My World facts, notes, tasks, completion state and reminders. Recent current-chat history is included in dialogue; relevant consented My World facts may be added as a small bounded context, while notes and older transcripts are retrieved through explicit recall."},
    "review": {"title": "MY WORLD REVIEW", "subtitle": "Opt-in · after a quiet pause", "capability": "memory_review",
                "description": "When relationship memory consent is enabled, an idle worker reviews up to 60 retained chat turns after 90 seconds without chat or model work. It pauses when the user returns, and saves only clear, non-sensitive details supported by the user's own words."},
    "desktop": {"title": "DESKTOP SIGNALS", "subtitle": "Fresh app / safe title", "capability": "desktop_awareness",
                 "description": "A lightweight collector supplies coarse foreground-application, idle-time and privacy-filtered window-title metadata. An explicit desktop question may use a recent in-memory external-window snapshot when Study Buddy itself is foreground. It never reads page contents or screen pixels."},
    "proactive": {"title": "PROACTIVE AGENT", "subtitle": "Low-priority · opt-in triggers", "capability": "proactive",
                  "description": "The scheduler considers enabled deadline, study-rhythm, session-return, wind-down and window-title triggers, then applies quiet-hour, cooldown, consent and boundary checks. For title-based assistant guesses, the LLM gets fresh sanitized app/title metadata plus safety and character instructions, but no past chats, tasks, reminders or saved notes. A delivered check-in can add one-turn context to the user's reply."},
    "camera": {"title": "CAMERA FEATURE", "subtitle": "Explicit consent only", "capability": "camera_preview",
                "description": "Camera preview starts only after explicit permission. Optional expression features run locally when their model is installed; raw frames are not sent to the chat LLM."},
    "voice": {"title": "VOICE", "subtitle": "Optional speech I/O", "capability": "voice_input",
               "description": "Optional local speech input/output. Voice features are separate IO adapters and do not create a second reasoning model."},
}


class IntelligenceDashboard:
    def __init__(self, parent, registry, monitor, memory=None, model_name="qwen2.5:1.5b", intent_dictionary_path=None,
                 core=None, settings=None):
        self.parent = parent
        self.registry = registry
        self.monitor = monitor
        self.memory = memory
        self.model_name = str(model_name or "qwen2.5:1.5b")
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
        self._last_trace_events = []
        self._last_trace_nodes = set()
        self._last_trace_edges = set()
        self._last_trace_text = "No correlated chat path captured yet."
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
        ttk.Label(title_box, text="Live component status and the latest observed chat route",
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
        self.notebook.add(self.activity_tab, text="Runtime Activity")
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
        value_font = ("Segoe UI", 14, "bold") if key == "llm_latency" else ("Segoe UI", 20, "bold")
        ttk.Label(frame, textvariable=value, font=value_font).pack(anchor="w")
        self.metric_vars[key] = value
        return frame

    def _build_overview(self):
        self.metric_vars = {}
        metrics = ttk.Frame(self.overview_tab)
        metrics.pack(fill="x", pady=(0, 10))
        for title, key in (("Capabilities", "capabilities"), ("Active", "active"),
                           ("Events retained", "calls"), ("Failed events", "failed"),
                           ("Chat LLM median / p90", "llm_latency")):
            self._metric(metrics, title, key).pack(side="left", fill="x", expand=True, padx=(0, 7))

        graph_frame = ttk.LabelFrame(self.overview_tab, text="System map + last chat trace · click a node for details", padding=8)
        graph_frame.pack(fill="both", expand=True)
        self.graph = tk.Canvas(graph_frame, height=390, background="#0b1220", highlightthickness=0)
        self.graph.pack(fill="both", expand=True)
        self.graph.bind("<Configure>", lambda _e: self._draw_graph())
        self.graph_caption = tk.StringVar(value="Gold edges show the latest traced chat path. Gray edges show supported possible flows.")
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
        if node_id == "review" and "memory_review" in running_tools:
            return "running"
        if node_id == "proactive" and "proactive_request" in running_tools:
            return "running"
        if cap_id and cap_id in by_id:
            status = by_id[cap_id].get("status", "unavailable")
            if cap_id in running_tools:
                return "running"
            return status
        if node_id == "proactive":
            return by_id.get("proactive_agent", {}).get("status", "available")
        return "available"

    @staticmethod
    def _trace_route(events):
        """Map correlated, privacy-safe events to the path taken by the latest chat."""
        if not events or not any(event.get("tool_id") == "chat_request" for event in events):
            return set(), set(), "No correlated chat path captured yet."

        nodes = {"chat", "intent"}
        edges = {"chat_router"}
        ordered = [event for event in events if event.get("tool_id") != "chat_request"]

        model_events = sorted(
            (event for event in ordered if event.get("tool_id") == "local_ai"),
            key=lambda event: float(event.get("started_at") or 0),
        )
        # OllamaClient.chat() delegates to chat_completion(); when both wrappers
        # are monitored, collapse their nearly identical nested timing records.
        unique_model_events = []
        for event in model_events:
            if unique_model_events:
                previous = unique_model_events[-1]
                same_start = abs(float(event.get("started_at") or 0) - float(previous.get("started_at") or 0)) < 0.02
                same_duration = abs(int(event.get("duration_ms") or 0) - int(previous.get("duration_ms") or 0)) < 1500
                if same_start and same_duration:
                    continue
            unique_model_events.append(event)

        if unique_model_events:
            nodes.add("llm")
            edges.add("router_llm")

        tool_events = [event for event in ordered if event.get("source") == "LLM tool call"]
        if tool_events:
            nodes.add("tools")
            edges.add("llm_tools")
            if len(unique_model_events) > 1 and unique_model_events[-1].get("started_at", 0) > tool_events[0].get("started_at", 0):
                edges.add("tools_llm")
            else:
                edges.add("tools_chat")

        memory_context_events = [event for event in ordered if event.get("tool_id") == "flow_memory_context"]
        storage_events = [event for event in ordered if (
            event.get("source") == "Memory"
            or event.get("tool_id") in {"memory", "tasks", "reminders", "flow_store", "flow_memory_context"}
        )]
        if storage_events:
            nodes.add("store")
            if tool_events:
                edges.add("tools_store")
            elif (not memory_context_events
                  or any(event.get("tool_id") == "flow_store" for event in storage_events)):
                edges.add("router_store")

        if memory_context_events:
            nodes.add("store")
            nodes.add("llm")
            edges.add("store_llm")

        desktop_events = [event for event in ordered if event.get("tool_id") in {"desktop_awareness", "flow_desktop"}]
        if desktop_events:
            nodes.add("desktop")
            if tool_events:
                edges.add("tools_desktop")
            if unique_model_events:
                edges.add("desktop_llm")

        camera_events = [event for event in ordered if event.get("tool_id") in {"camera_preview", "flow_camera"}]
        if camera_events:
            nodes.add("camera")
            if tool_events:
                edges.add("tools_camera")
            elif any(event.get("tool_id") == "camera_preview" for event in camera_events):
                edges.add("chat_camera")
            if unique_model_events:
                edges.add("camera_llm")

        proactive_events = [event for event in ordered if event.get("tool_id") == "flow_proactive"]
        if proactive_events:
            nodes.add("proactive")
            edges.add("proactive_chat")
            if unique_model_events:
                edges.add("proactive_llm")

        voice_events = [event for event in ordered if event.get("tool_id") == "flow_voice"]
        if voice_events:
            nodes.add("voice")
            edges.add("voice_chat")

        labels = {
            "chat": "Chat", "intent": "Router", "llm": "LLM", "tools": "Tools",
            "store": "Local data", "desktop": "Desktop context", "camera": "Camera feature",
            "voice": "Voice input", "proactive": "Proactive context",
        }
        path = (["voice", "chat", "intent"] if voice_events else ["chat", "intent"])
        for event in sorted(ordered, key=lambda item: (float(item.get("started_at") or 0), int(item.get("id") or 0))):
            tool_id = event.get("tool_id")
            source = event.get("source")
            node = None
            if tool_id == "local_ai":
                node = "llm"
            elif source == "LLM tool call":
                node = "tools"
                if tool_id == "desktop_awareness":
                    path.append("desktop")
                elif tool_id == "camera_preview":
                    path.append("camera")
            elif tool_id == "camera_preview":
                node = "camera"
            elif tool_id in {"flow_desktop", "flow_camera", "flow_proactive", "flow_voice", "flow_store", "flow_memory_context"}:
                node = {"flow_desktop": "desktop", "flow_camera": "camera",
                        "flow_proactive": "proactive", "flow_voice": "voice",
                        "flow_store": "store", "flow_memory_context": "store"}.get(tool_id)
            elif source == "Memory" or tool_id in {"memory", "tasks", "reminders"}:
                node = "store"
            if node == "voice" and voice_events:
                continue  # Voice is the input source before the chat node.
            if node and (not path or path[-1] != node):
                path.append(node)
        if path[-1] not in {"chat", "intent"}:
            path.append("chat")
        path_text = " → ".join(labels.get(node, node.title()) for node in path)
        return nodes, edges, "Last chat path: " + path_text

    @staticmethod
    def _format_ms(value):
        if value is None:
            return "—"
        value = max(0, float(value))
        return f"{value / 1000:.1f}s" if value >= 1000 else f"{int(value)}ms"

    def _llm_metrics_detail(self):
        llm_client = getattr(self.core, "llm", None) if self.core is not None else None
        metrics = getattr(llm_client, "last_metrics", {}) if llm_client is not None else {}
        if not isinstance(metrics, dict) or not metrics:
            return ""
        parts = []
        for label, key in (("queue", "queue_wait_ms"), ("load", "load_ms"),
                           ("prompt", "prompt_eval_ms"), ("generation", "generation_ms")):
            if metrics.get(key) is not None:
                parts.append(label + " " + self._format_ms(metrics.get(key)))
        if metrics.get("tokens_per_second") is not None:
            parts.append(f"{metrics['tokens_per_second']} tok/s")
        return (" Latest model call: " + ", ".join(parts) + ".") if parts else ""

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
        llm_metrics_text = self._llm_metrics_detail() if node_id == "llm" else ""
        self.graph_detail_var.set(f"{spec['title']} · {status_text}. {spec['description']}{model_text}{dictionary_text} {live_detail}{llm_metrics_text}".strip())

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
            "store": (center_x, min(height - 55, 330)),
            "desktop": (left_x, 155),
            "review": (left_x, 330),
            "proactive": (right_x, 155),
            "camera": (left_x, 260),
            "voice": (right_x, 260),
        }
        sizes = {"chat": (184, 44), "intent": (220, 46), "llm": (220, 48), "tools": (205, 46),
                 "store": (255, 48), "desktop": (174, 54), "review": (198, 54), "proactive": (184, 54),
                 "camera": (174, 54), "voice": (174, 54)}
        rects = {}
        for node_id, (x, y) in positions.items():
            w, h = sizes[node_id]
            rects[node_id] = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        self._node_rects = rects
        canvas.create_text(12, 10, text="SYSTEM MAP  ·  BORDER = LIVE STATUS  ·  GOLD = LAST CHAT PATH  ·  GRAY = POSSIBLE FLOW", anchor="nw",
                           fill="#94a3b8", font=("Segoe UI", 8, "bold"))

        # These are supported possible connections. The gold overlay is derived
        # from actual, correlated events for the most recent chat request.
        edges = [
            ("chat_router", "chat", "intent", "both", None, None),
            ("router_llm", "intent", "llm", "both", None, None),
            ("llm_tools", "llm", "tools", "both", None, None),
            ("tools_store", "tools", "store", "both", None, None),
            ("store_llm", "store", "llm", "last", (4, 3), "memory_llm"),
            ("store_review", "store", "review", "both", (3, 3), None),
            ("review_llm", "review", "llm", "both", (4, 3), "review_lane"),
            ("router_store", "intent", "store", "both", (3, 3), "left_lane"),
            ("tools_chat", "tools", "chat", "last", (3, 3), "left_lane"),
            ("desktop_llm", "desktop", "llm", "both", (4, 3), None),
            ("tools_desktop", "tools", "desktop", "both", (4, 3), None),
            ("chat_camera", "chat", "camera", "last", (3, 3), "left_lane"),
            ("desktop_proactive", "desktop", "proactive", "last", (3, 3), "bottom_lane"),
            ("proactive_llm", "proactive", "llm", "both", (4, 3), None),
            ("proactive_chat", "proactive", "chat", "last", (4, 3), "right_lane"),
            ("tools_camera", "tools", "camera", "both", (4, 3), None),
            ("camera_llm", "camera", "llm", "last", (4, 3), None),
            ("voice_chat", "voice", "chat", "both", (3, 3), "voice_lane"),
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

        for edge_id, source, target, arrow, dash, route in edges:
            a, b = rects[source], rects[target]
            if route == "left_lane":
                lane = center_x - 125
                x1, y1 = a[0], (a[1] + a[3]) / 2
                x2, y2 = b[0], (b[1] + b[3]) / 2
                points = (x1, y1, lane, y1, lane, y2, x2, y2)
            elif route == "bottom_lane":
                outside_left = left_x - 110
                outside_right = right_x + 110
                route_y = height - 20
                x1, y1 = a[0], (a[1] + a[3]) / 2
                x2, y2 = b[2], (b[1] + b[3]) / 2
                points = (x1, y1, outside_left, y1, outside_left, route_y,
                          outside_right, route_y, outside_right, y2, x2, y2)
            elif route == "right_lane":
                outside = min(width - 14, right_x + 110)
                x1, y1 = a[2], (a[1] + a[3]) / 2
                x2, y2 = b[2], (b[1] + b[3]) / 2
                points = (x1, y1, outside, y1, outside, y2, x2, y2)
            elif route == "voice_lane":
                outside = width - 12
                x1, y1 = a[2], (a[1] + a[3]) / 2
                x2, y2 = b[2], (b[1] + b[3]) / 2
                points = (x1, y1, outside, y1, outside, y2, x2, y2)
            elif route == "review_lane":
                outside = left_x - 110
                x1, y1 = a[0], (a[1] + a[3]) / 2
                x2, y2 = b[0], (b[1] + b[3]) / 2
                corridor_y = b[3] + 12
                points = (x1, y1, outside, y1, outside, corridor_y, x2, corridor_y, x2, y2)
            elif route == "memory_llm":
                lane = center_x - 125
                x1, y1 = a[0], (a[1] + a[3]) / 2
                x2, y2 = b[0], (b[1] + b[3]) / 2
                points = (x1, y1, lane, y1, lane, y2, x2, y2)
            else:
                x1, y1, x2, y2 = edge_points(a, b)
                points = (x1, y1, x2, y2)
            observed = edge_id in self._last_trace_edges
            background_active = (
                (edge_id in {"store_review", "review_llm"} and "memory_review" in running_tools)
                or (edge_id in {"desktop_proactive", "proactive_llm"} and "proactive_request" in running_tools)
            )
            edge_colour = "#c084fc" if background_active else ("#fbbf24" if observed else "#3b4b62")
            kwargs = {"fill": edge_colour,
                      "width": 3 if (observed or background_active) else (1 if dash else 1.5), "arrow": arrow}
            if dash:
                kwargs["dash"] = dash
            canvas.create_line(*points, **kwargs)

        for node_id, (x, y) in positions.items():
            spec = ARCHITECTURE_NODES[node_id]
            status = self._graph_node_status(node_id, by_id, running_tools)
            border = _STATUS_COLOURS.get(status, "#475569")
            selected = node_id == self.selected_node_id
            if selected:
                border = "#fbbf24"
            fill = "#27304a" if node_id == "review" and "memory_review" in running_tools else (
                "#203654" if node_id in self._last_trace_nodes else "#162338")
            w, h = sizes[node_id]
            tags = (f"node:{node_id}", "architecture_node")
            canvas.create_rectangle(x-w/2, y-h/2, x+w/2, y+h/2, fill=fill,
                                    outline=border, width=3 if selected else 2, tags=tags)
            if node_id == "llm":
                title = f"PRIMARY LLM · {self.model_name}"
                title = title if len(title) < 33 else f"PRIMARY LLM · {self.model_name[:15]}…"
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
                           text="Gold = last chat path   ·   Purple = active background work   ·   Gray = possible route",
                           anchor="se", fill="#94a3b8", font=("Segoe UI", 7))
        # Keep the detail pane synchronized after periodic live refreshes.
        if hasattr(self, "graph_detail_var"):
            spec = ARCHITECTURE_NODES.get(self.selected_node_id, ARCHITECTURE_NODES["llm"])
            status = self._graph_node_status(self.selected_node_id, by_id, running_tools).replace("_", " ").title()
            live_detail = by_id.get(spec.get("capability"), {}).get("detail", "") if spec.get("capability") else ""
            model_text = f" Primary model: {self.model_name}." if self.selected_node_id == "llm" else ""
            dictionary_text = (f" Editable file: {self.intent_dictionary_path}." if self.selected_node_id == "intent" and self.intent_dictionary_path else "")
            llm_metrics_text = self._llm_metrics_detail() if self.selected_node_id == "llm" else ""
            self.graph_detail_var.set(f"{spec['title']} · {status}. {spec['description']}{model_text}{dictionary_text} {live_detail}{llm_metrics_text}".strip())

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
        trace_reader = getattr(self.monitor, "latest_chat_trace", None)
        self._last_trace_events = trace_reader() if callable(trace_reader) else []
        self._last_trace_nodes, self._last_trace_edges, self._last_trace_text = self._trace_route(self._last_trace_events)
        latency = self.monitor.latency_summary("local_ai") if hasattr(self.monitor, "latency_summary") else {"samples": 0, "average_ms": None, "last_ms": None}
        average = latency.get("average_ms")
        median = latency.get("median_ms")
        p90 = latency.get("p90_ms")
        if median is not None and p90 is not None:
            self.metric_vars["llm_latency"].set(f"{self._format_ms(median)} / {self._format_ms(p90)}")
        else:
            self.metric_vars["llm_latency"].set("No data yet")
        avg_text = (f"Chat LLM calls (newest {latency.get('samples', 0)} successful): "
                    f"mean {self._format_ms(average)}, median {self._format_ms(median)}, "
                    f"p90 {self._format_ms(p90)}, last {self._format_ms(latency.get('last_ms'))}"
                    if average is not None else "No completed LLM call has been measured yet.")
        self.graph_caption.set(
            f"{self._last_trace_text} · {avg_text} · "
            f"Telemetry retains up to 2,500 recent events; currently {stats['total']} stored, {stats['failed']} failed."
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
