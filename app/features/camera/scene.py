"""Live scene understanding through a local Ollama vision model (default: moondream).

A background thread periodically sends the newest camera frame (JPEG, in memory only) to the
local Ollama server and keeps only the short text description. Frames are never saved and never
leave the machine. Work is skipped while the chat model is busy so replies stay fast.
"""
import base64
import threading
import time

import requests


SCENE_PROMPT = "Describe this image in one or two short sentences."
RETRY_PROMPT = "What is in this picture? Answer in one sentence."


def clean_caption(text):
    """Collapse repetition loops ("man, man, man…") and reject captions that are still degenerate."""
    import re
    words = re.findall(r"[A-Za-z']+|[.,!?]", str(text or ""))
    out = []
    for w in words:
        if out and w.lower() == out[-1].lower():
            continue
        out.append(w)
    text = " ".join(out)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"^(?:um|uh|hmm|well)[,\s]+", "", text, flags=re.I).strip()
    alpha = [w.lower() for w in re.findall(r"[A-Za-z']+", text)]
    if len(alpha) < 4 or len(set(alpha)) / len(alpha) < 0.55:
        return ""
    # a word repeated many times in the original (man, X, man, Y, man…) is also a loop
    content = [w for w in alpha if len(w) > 3]
    if content and max(content.count(w) for w in set(content)) > 3:
        return ""
    return text


class SceneAnalyzer:
    def __init__(self, url="http://localhost:11434", model="moondream", interval_seconds=10,
                 should_pause=None, keep_alive="3m", timeout=120, num_thread=4, on_description=None):
        self.url = url.rstrip("/")
        self.model = model
        self.interval = max(3.0, float(interval_seconds))
        self.should_pause = should_pause or (lambda: False)
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.num_thread = num_thread
        self.on_description = on_description
        self.last_description = ""
        self.last_update = 0.0
        self.last_error = ""
        self.last_duration = 0.0
        self.available = True
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    # ---- called from the UI thread (cheap) ----
    def submit_frame(self, frame):
        """Keep only the newest frame; the worker pulls it when it is ready."""
        with self._lock:
            self._frame = frame.copy() if hasattr(frame, "copy") else frame
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="scene-vision", daemon=True)
            self._thread.start()

    def get_recent_description(self, max_age_seconds=30):
        if self.last_description and time.time() - self.last_update <= max_age_seconds:
            return self.last_description
        return ""

    def request_now(self):
        """Ask the worker to analyse immediately (e.g. the user asked what the camera sees)."""
        self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._frame = None
        self.last_description = ""

    # ---- worker ----
    def _take_frame(self):
        with self._lock:
            frame, self._frame = self._frame, None
        return frame

    @staticmethod
    def _encode(frame):
        import cv2
        h, w = frame.shape[:2]
        scale = 512.0 / max(h, w)
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise RuntimeError("could not encode frame")
        return base64.b64encode(buf.tobytes()).decode("ascii")

    def _call(self, image_b64, prompt, penalty, temperature):
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "options": {"num_gpu": 0, "num_thread": self.num_thread, "num_predict": 60, "temperature": temperature,
                        "repeat_penalty": penalty, "top_k": 40, "top_p": 0.9},
        }
        r = requests.post(self.url + "/api/chat", json=body, timeout=self.timeout)
        if r.status_code == 404:
            raise RuntimeError("Vision model '" + self.model + "' is not installed. Run: ollama pull " + self.model)
        if r.status_code != 200:
            raise RuntimeError("Ollama error " + str(r.status_code) + ": " + r.text[:200])
        return clean_caption(" ".join(str((r.json().get("message") or {}).get("content") or "").split()))

    def describe(self, frame):
        image = self._encode(frame)
        text = self._call(image, SCENE_PROMPT, 1.25, 0.3)
        if not text:                       # degenerate output: retry once, stricter, different wording
            text = self._call(image, RETRY_PROMPT, 1.6, 0.5)
        return text

    def _run(self):
        next_due = 0.0
        while not self._stop.is_set():
            self._wake.wait(0.5)
            forced = self._wake.is_set()
            self._wake.clear()
            if self._stop.is_set():
                return
            if not forced and time.time() < next_due:
                continue
            try:
                if self.should_pause():
                    continue
            except Exception:
                pass
            frame = self._take_frame()
            if frame is None:
                continue
            started = time.time()
            try:
                text = self.describe(frame)
                if text:
                    self.last_description = text
                    self.last_update = time.time()
                    if self.on_description:
                        try: self.on_description(text, self.last_update)
                        except Exception: pass
                self.last_error = ""
            except Exception as exc:
                self.last_error = "Scene vision failed: " + str(exc)
                if "not installed" in str(exc):
                    self.available = False
                    return
            self.last_duration = time.time() - started
            # Wait at least the interval, and at least 2x the inference time so CPU is not saturated.
            next_due = time.time() + max(self.interval, 2 * self.last_duration)
