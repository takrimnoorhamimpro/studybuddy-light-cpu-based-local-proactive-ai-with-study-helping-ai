"""
llm.py  -  Talks to Ollama (the program that runs the AI brain).

Think of Ollama as a media player and the model as the song.
This file just says "play this" and gets the reply back.

Always keeps num_gpu = 0 (CPU only) because the GPU gave garbage output.
"""

import http.client
import json
import socket
import threading
import time
import urllib.parse

import requests


class LLMError(Exception):
    """Raised when Ollama is not running or gives a bad answer."""


class _Cancelled(Exception):
    """Internal: the caller cancelled this request (e.g. the user started typing)."""


class _RawResponse:
    """Minimal requests-like response over http.client so a blocked request can be aborted instantly."""

    def __init__(self, conn, resp, done, cancel_event):
        self._conn, self._resp, self._done, self._cancel = conn, resp, done, cancel_event
        self.status_code = resp.status
        self._text = None

    def _aborted(self):
        return self._cancel is not None and self._cancel.is_set()

    @property
    def text(self):
        if self._text is None:
            try:
                self._text = self._resp.read().decode("utf-8", errors="replace")
            except (OSError, http.client.HTTPException, ValueError):
                if self._aborted():
                    raise _Cancelled()
                raise requests.ConnectionError("connection lost while reading the reply")
            finally:
                self.close()
        return self._text

    def json(self):
        return json.loads(self.text)

    def iter_lines(self):
        try:
            while True:
                line = self._resp.readline()
                if not line:
                    return
                yield line.rstrip(b"\r\n")
        except (OSError, http.client.HTTPException, ValueError):
            if self._aborted():
                return                      # aborted on purpose: end the stream quietly
            raise requests.ConnectionError("connection lost while streaming the reply")

    def close(self):
        self._done.set()
        try:
            self._conn.close()
        except Exception:
            pass


def _cancellable_post(url, body, timeout, cancel_event):
    """POST JSON like requests.post, but closing the socket the moment cancel_event is set.

    Ollama reads the whole prompt before it sends a single byte, so a normal client cannot stop a
    low-priority job (memory review, proactive message) during that phase. Dropping the connection
    makes Ollama abandon the work, freeing the CPU for the user's message right away.
    """
    parts = urllib.parse.urlsplit(url)
    limit = max(timeout) if isinstance(timeout, (tuple, list)) else timeout
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=limit)
    done = threading.Event()

    def watchdog():
        while not done.wait(0.1):
            if cancel_event.is_set():
                for _ in range(50):          # the socket only exists after connect(); wait for it briefly
                    sock = getattr(conn, "sock", None)
                    if sock is not None:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        break
                    if done.wait(0.05):
                        break
                try: conn.close()
                except Exception: pass
                return

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        conn.request("POST", parts.path or "/", body=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        done.set()
        if cancel_event.is_set():
            raise _Cancelled()
        raise requests.ConnectionError(str(exc))
    return _RawResponse(conn, resp, done, cancel_event)


def resolve_keep_alive(settings):
    """Translate UI preferences into Ollama's keep_alive API value.

    Supported modes: 30 minutes (default), 1 hour, custom minutes, unlimited.
    Ollama accepts duration strings such as ``30m``/``1h`` and ``-1`` for
    keeping the model loaded indefinitely.
    """
    settings = settings or {}
    mode = str(settings.get("model_keep_alive_mode", "30 minutes")).strip().lower()
    if mode in ("unlimited", "indefinite", "forever", "-1"):
        return -1
    if mode in ("1 hour", "1 hour (60 minutes)", "60 minutes", "1h"):
        return "1h"
    if mode == "custom":
        try:
            minutes = int(settings.get("model_keep_alive_custom_minutes", 30))
        except (TypeError, ValueError):
            minutes = 30
        minutes = max(1, min(minutes, 10080))
        return f"{minutes}m"
    # Unknown/missing values safely fall back to the requested default.
    return "30m"


def trim_to_sentence(text):
    """If a reply was cut mid-sentence, drop the broken tail (keep full sentences)."""
    text = text.strip()
    if not text or text[-1] in ".!?\"')":
        return text
    cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
    return text[:cut + 1] if cut > 0 else text


class OllamaClient:
    def __init__(self, url="http://localhost:11434", model="llama3.2:3b",
                 options=None, timeout=180, keep_alive="30m", embedding_model="nomic-embed-text"):
        self.url = url.rstrip("/")
        self.model = model
        self.embedding_model = str(embedding_model or "nomic-embed-text")
        self.last_embed_metrics = {}
        # Keep CPU-only. Do not remove num_gpu.
        self.options = dict(options) if options else {"num_gpu": 0, "num_thread": 4, "num_ctx": 3072, "num_predict": 96, "temperature": 0.2}
        self.options["num_gpu"] = 0
        self.timeout = timeout
        self.keep_alive = keep_alive
        # Latest Ollama timing counters, useful for real-device benchmarking.
        self.last_metrics = {}

    def is_alive(self):
        """True if Ollama answers."""
        try:
            r = requests.get(self.url + "/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _available_model_names(self):
        """Return names installed in this exact Ollama server, or [] if unavailable."""
        try:
            response = requests.get(self.url + "/api/tags", timeout=5)
            if response.status_code != 200:
                return []
            payload = response.json()
            models = payload.get("models", [])
            return sorted({str(item.get("name") or item.get("model") or "").strip()
                           for item in models if isinstance(item, dict)
                           and str(item.get("name") or item.get("model") or "").strip()})
        except (requests.RequestException, ValueError, TypeError):
            return []

    def _equivalent_installed_tag(self, installed_names):
        """Resolve equivalent aliases (e.g. llama3.2 -> llama3.2:3b/latest).

        Do not silently change to a different model family or from 3B to 1B.
        """
        preferred = str(self.model or "").strip()
        if preferred in installed_names:
            return preferred
        base = preferred.split(":", 1)[0].lower()
        if base == "llama3.2":
            # Official `latest` and `3b` tags refer to the 3B default; don't use :1b.
            for candidate in ("llama3.2:3b", "llama3.2:latest", "llama3.2"):
                if candidate in installed_names:
                    return candidate
            return None
        if preferred and ":" not in preferred:
            candidate = preferred + ":latest"
            if candidate in installed_names:
                return candidate
        return None

    def _model_not_found_error(self, installed_names):
        preferred = str(self.model or "llama3.2:3b")
        inventory = ", ".join(installed_names[:12]) if installed_names else "could not read local model list"
        if preferred.split(":", 1)[0].lower() == "llama3.2":
            command = "ollama pull llama3.2:3b"
        else:
            command = "ollama pull " + preferred
        return LLMError(
            "The configured chat model '" + preferred + "' is not installed in the Ollama server at " + self.url + ". "
            "Installed local models: " + inventory + ". Run `" + command + "` in Command Prompt, then restart Study Buddy. "
            "If the model is installed already, run `ollama list` and confirm Study Buddy uses the same Ollama server/URL."
        )

    def chat_completion(self, messages, tools=None, on_token=None, options=None, response_format=None,
                        cancel_event=None):
        """Return Ollama's structured assistant message, including tool_calls.

        Pass ``cancel_event`` (a threading.Event) for low-priority background work: setting it aborts the
        request immediately, even while Ollama is still reading the prompt, and returns {"cancelled": True}.
        """
        try:
            return self._chat_completion_inner(messages, tools, on_token, options, response_format, cancel_event)
        except _Cancelled:
            self.last_metrics = {"error": "cancelled"}
            return {"message": {"role": "assistant", "content": ""}, "cancelled": True}

    def _chat_completion_inner(self, messages, tools, on_token, options, response_format, cancel_event):
        """Return Ollama's structured assistant message, including tool_calls.

        Tool definitions are JSON Schema function declarations. This method only
        requests tool calls; the allow-listed Python ToolOrchestrator executes them.
        If a configured model alias is missing, it checks /api/tags and retries only
        with an equivalent local tag. It never silently switches model families.
        """
        streaming = callable(on_token)
        body = {
            "model": self.model,
            "messages": messages,
            "stream": streaming,
            "keep_alive": self.keep_alive,
            "options": dict(self.options, **(options or {})),
        }
        if response_format is not None:
            body["format"] = response_format
        # Per-call options can tune generation length but can never enable GPU use.
        body["options"]["num_gpu"] = 0
        if tools is not None:
            body["tools"] = tools
        started = time.perf_counter()

        def post():
            if cancel_event is not None:
                return _cancellable_post(self.url + "/api/chat", body, self.timeout, cancel_event)
            return requests.post(self.url + "/api/chat", json=body, timeout=self.timeout, stream=streaming)

        try:
            r = post()
        except requests.RequestException as e:
            self.last_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                 "error": type(e).__name__}
            raise LLMError("Cannot reach Ollama. Is it running? (" + str(e) + ")")
        if r.status_code == 404 and "model" in (r.text or "").lower():
            installed = self._available_model_names()
            resolved = self._equivalent_installed_tag(installed)
            if resolved and resolved != self.model:
                self.model = resolved
                body["model"] = resolved
                try:
                    r = post()
                except requests.RequestException as e:
                    self.last_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                         "error": type(e).__name__}
                    raise LLMError("Cannot reach Ollama while retrying model tag '" + resolved + "': " + str(e))
            elif not resolved:
                raise self._model_not_found_error(installed)
        if r.status_code != 200:
            if r.status_code == 404 and "model" in (r.text or "").lower():
                raise self._model_not_found_error(self._available_model_names())
            raise LLMError("Ollama error " + str(r.status_code) + ": " + r.text[:300])
        try:
            if streaming:
                data = self._consume_chat_stream(r, on_token)
                if cancel_event is not None and cancel_event.is_set():
                    data["cancelled"] = True
                    data.setdefault("message", {"role": "assistant", "content": ""})
            else:
                data = r.json()
            if not isinstance(data.get("message"), dict):
                raise ValueError("message is missing")
            prompt_ms = float(data.get("prompt_eval_duration") or 0) / 1_000_000
            eval_ms = float(data.get("eval_duration") or 0) / 1_000_000
            eval_count = int(data.get("eval_count") or 0)
            self.last_metrics = {
                "model": data.get("model", self.model),
                "request_ms": round((time.perf_counter() - started) * 1000, 1),
                "load_ms": round(float(data.get("load_duration") or 0) / 1_000_000, 1),
                "prompt_tokens": int(data.get("prompt_eval_count") or 0),
                "prompt_eval_ms": round(prompt_ms, 1),
                "generated_tokens": eval_count,
                "generation_ms": round(eval_ms, 1),
                "tokens_per_second": round(eval_count / (eval_ms / 1000), 2) if eval_ms > 0 else None,
                "done_reason": data.get("done_reason"),
            }
            return data
        except requests.RequestException as e:
            self.last_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                 "error": type(e).__name__}
            raise LLMError("Ollama stream was interrupted. Please retry; any unfinished response was not committed. (" + str(e) + ")")
        except (ValueError, TypeError):
            raise LLMError("Unexpected structured answer from Ollama.")

    @staticmethod
    def _consume_chat_stream(response, on_token):
        """Consume Ollama's newline-delimited chat stream and forward text chunks.

        Tool calls are retained from the latest chunk that includes them. The UI may
        display streamed text provisionally; the caller still validates the completed
        response before committing it to chat history.
        Returning False from ``on_token`` cancels the request. This is used to stop a
        low-priority proactive generation as soon as a user starts an interactive chat.
        """
        result = {}
        role = "assistant"
        content_parts = []
        tool_calls = None
        cancelled = False
        try:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8", errors="replace")
                try:
                    chunk = json.loads(raw_line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(chunk, dict):
                    continue
                result.update({k: v for k, v in chunk.items() if k != "message"})
                message = chunk.get("message") or {}
                if message.get("role"):
                    role = str(message["role"])
                if isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
                    tool_calls = message["tool_calls"]
                fragment = message.get("content")
                if isinstance(fragment, str) and fragment:
                    content_parts.append(fragment)
                # Invoke the callback for empty-content stream chunks too, so a
                # cancellation signal can stop a low-priority generation even while
                # Ollama is emitting metadata/tool-call chunks.
                try:
                    keep_going = on_token(fragment if isinstance(fragment, str) else "")
                except Exception:
                    # A rendering callback must never crash inference.
                    keep_going = True
                if keep_going is False:
                    cancelled = True
                    break
                if chunk.get("done"):
                    break
        finally:
            try:
                response.close()
            except Exception:
                pass
        result["message"] = {"role": role, "content": "".join(content_parts)}
        if tool_calls:
            result["message"]["tool_calls"] = tool_calls
        if cancelled:
            result["cancelled"] = True
        return result

    def embed(self, inputs, model=None, keep_alive="30m"):
        """Create local vector embeddings through Ollama's /api/embed endpoint.

        Accepts one string or a list of strings and returns a list of vectors. The
        caller can cache document vectors and embed only new queries after indexing.
        """
        if isinstance(inputs, str):
            inputs = [inputs]
        if not isinstance(inputs, (list, tuple)) or not all(isinstance(x, str) for x in inputs):
            raise ValueError("Embedding input must be text or a list of text strings")
        if not inputs:
            return []
        body = {
            "model": model or self.embedding_model,
            "input": list(inputs),
            "truncate": True,
            "keep_alive": keep_alive,
            "options": {key: value for key, value in self.options.items()
                        if key in ("num_gpu", "num_thread")},
        }
        started = time.perf_counter()
        try:
            response = requests.post(self.url + "/api/embed", json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            self.last_embed_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                       "error": type(exc).__name__}
            raise LLMError("Cannot reach Ollama embedding endpoint. Is Ollama running? (" + str(exc) + ")")
        if response.status_code != 200:
            self.last_embed_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                       "status_code": response.status_code}
            raise LLMError("Ollama embedding error " + str(response.status_code) + ": " + response.text[:240])
        try:
            data = response.json()
            vectors = data.get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(inputs):
                raise ValueError("embedding vectors are missing")
            if not all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in vectors):
                raise ValueError("embedding vectors have an invalid shape")
            self.last_embed_metrics = {
                "model": data.get("model", model or self.embedding_model),
                "request_ms": round((time.perf_counter() - started) * 1000, 1),
                "load_ms": round(float(data.get("load_duration") or 0) / 1_000_000, 1),
                "prompt_tokens": int(data.get("prompt_eval_count") or 0),
            }
            return vectors
        except (ValueError, TypeError):
            raise LLMError("Unexpected embedding response from Ollama.")

    def running_info(self):
        """What Ollama actually loaded (like `ollama ps`): size, CPU/GPU split, context. {} if unavailable."""
        try:
            data = requests.get(self.url + "/api/ps", timeout=3).json()
        except (requests.RequestException, ValueError):
            return {}
        wanted = str(self.model or "").split(":")[0].casefold()
        for item in data.get("models") or []:
            name = str(item.get("name") or item.get("model") or "")
            if name.casefold().split(":")[0] != wanted:
                continue
            size = float(item.get("size") or 0)
            vram = float(item.get("size_vram") or 0)
            gpu_share = int(round(100 * vram / size)) if size else 0
            return {"name": name, "size_gb": round(size / 1024 ** 3, 2), "gpu_percent": gpu_share,
                    "cpu_percent": 100 - gpu_share, "context_length": item.get("context_length"),
                    "expires_at": item.get("expires_at")}
        return {}

    def chat(self, messages, on_token=None, options=None, response_format=None, cancel_event=None):
        """Text-only call; optional request-scoped options and streaming callback."""
        data = self.chat_completion(messages, on_token=on_token, options=options,
                                    response_format=response_format, cancel_event=cancel_event)
        if data.get("cancelled"):
            return ""
        try:
            text = str(data["message"].get("content") or "").strip()
            if data.get("done_reason") == "length":
                text = trim_to_sentence(text)
            return text
        except (KeyError, TypeError):
            raise LLMError("Unexpected answer from Ollama.")
