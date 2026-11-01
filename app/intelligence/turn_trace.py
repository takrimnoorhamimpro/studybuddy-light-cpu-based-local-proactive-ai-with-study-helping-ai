"""In-memory trace of how each chat message flowed through the app (never written to disk).

Each trace is a list of timed steps (routing, memory + prompt build, AI model, ...), so the
Intelligence Center can show *where the time went* for every reply.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class TurnTrace:
    def __init__(self, maxlen=60):
        self._items = deque(maxlen=max(10, int(maxlen)))
        self._lock = threading.RLock()
        self._local = threading.local()
        self._seq = 0

    def begin(self, text):
        current = getattr(self._local, "current", None)
        if current is not None and not current.get("done"):
            current["_depth"] = current.get("_depth", 0) + 1      # nested handle_message call
            return current
        with self._lock:
            self._seq += 1
            trace = {"id": self._seq, "ts": time.time(), "text": " ".join(str(text or "").split())[:70],
                     "steps": [], "total_ms": None, "route": "", "done": False, "_depth": 0}
            self._items.append(trace)
        self._local.current = trace
        return trace

    def step(self, name, ms, **detail):
        trace = getattr(self._local, "current", None)
        if trace is None or trace.get("done"):
            return
        with self._lock:
            trace["steps"].append({"name": str(name), "ms": round(max(0.0, float(ms)), 1), "detail": dict(detail)})

    def finish(self, trace, total_ms, result=None):
        if trace is None:
            return
        if trace.get("_depth", 0) > 0:
            trace["_depth"] -= 1
            return
        with self._lock:
            steps = trace["steps"]
            names = [s["name"] for s in steps]
            used_ai = "AI model" in names
            measured = sum(s["ms"] for s in steps)
            overhead = max(0.0, float(total_ms) - measured)
            label = "Routing, safety checks & reply handling" if used_ai else "Local handling (no AI model)"
            steps.insert(0, {"name": label, "ms": round(overhead, 1), "detail": {}})
            tools = 0
            for step in steps:
                tools = max(tools, int(step["detail"].get("tools_offered") or 0))
            flag = (result or {}).get("flag") if isinstance(result, dict) else None
            if used_ai:
                trace["route"] = "AI reply + tools" if tools else "AI reply"
            else:
                trace["route"] = "Local flow" + (" (%s)" % flag if flag and flag != "none" else "")
            trace["total_ms"] = round(float(total_ms), 1)
            trace["done"] = True
        if getattr(self._local, "current", None) is trace:
            self._local.current = None

    def recent(self, limit=40):
        with self._lock:
            return [dict(t, steps=[dict(s) for s in t["steps"]]) for t in list(self._items)[-limit:]][::-1]
