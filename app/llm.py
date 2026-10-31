"""
llm.py  -  Talks to Ollama (the program that runs the AI brain).

Think of Ollama as a media player and the model as the song.
This file just says "play this" and gets the reply back.

Always keeps num_gpu = 0 (CPU only) because the GPU gave garbage output.
"""

import requests
import time
import json
import threading


class LLMError(Exception):
    """Raised when Ollama is not running or gives a bad answer."""


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
    def __init__(self, url="http://127.0.0.1:11434", model="qwen2.5:1.5b",
                 options=None, timeout=180, keep_alive="30m", embedding_model="nomic-embed-text"):
        self.url = url.rstrip("/")
        self.model = model
        self.embedding_model = str(embedding_model or "nomic-embed-text")
        self.last_embed_metrics = {}
        # Keep CPU-only. Do not remove num_gpu.
        self.options = dict(options) if options else {"num_gpu": 0, "num_thread": 4, "num_ctx": 3072, "num_predict": 40, "temperature": 0.2}
        self.options["num_gpu"] = 0
        self.timeout = timeout
        self.keep_alive = keep_alive
        # Chat, proactive title checks, and quiet memory review share one local model.
        # Serialize calls so background work cannot compete with foreground chat.
        self._chat_lock = threading.Lock()
        self._active_request_lock = threading.Lock()
        self._active_response = None
        self._active_cancel_event = None
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
        """Resolve equivalent aliases for the selected local model.

        Do not silently change model families or parameter sizes.
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
        if base == "qwen2.5" and preferred.split(":", 1)[-1].lower() == "1.5b":
            for candidate in ("qwen2.5:1.5b", "qwen2.5:1.5b-instruct-q4_K_M"):
                if candidate in installed_names:
                    return candidate
            return None
        if preferred and ":" not in preferred:
            candidate = preferred + ":latest"
            if candidate in installed_names:
                return candidate
        return None

    def _model_not_found_error(self, installed_names):
        preferred = str(self.model or "qwen2.5:1.5b")
        inventory = ", ".join(installed_names[:12]) if installed_names else "could not read local model list"
        command = "ollama pull " + preferred
        return LLMError(
            "The configured chat model '" + preferred + "' is not installed in the Ollama server at " + self.url + ". "
            "Installed local models: " + inventory + ". Run `" + command + "` in Command Prompt, then restart Study Buddy. "
            "If the model is installed already, run `ollama list` and confirm Study Buddy uses the same Ollama server/URL."
        )

    def cancel_active_request(self, cancel_event):
        """Close a matching low-priority stream after its owner requests cancellation."""
        if cancel_event is None or not cancel_event.is_set():
            return False
        with self._active_request_lock:
            if self._active_cancel_event is not cancel_event or self._active_response is None:
                return False
            response = self._active_response
        try:
            response.close()
            return True
        except Exception:
            return False

    def chat_completion(self, messages, tools=None, on_token=None, options=None, response_format=None,
                        cancel_event=None):
        """Serialize access to the resident model and include time spent queued."""
        queued_at = time.perf_counter()
        with self._chat_lock:
            queue_wait_ms = round((time.perf_counter() - queued_at) * 1000, 1)
            try:
                if cancel_event is not None and cancel_event.is_set():
                    self.last_metrics = {"queue_wait_ms": queue_wait_ms, "cancelled": True}
                    return {"message": {"role": "assistant", "content": ""}, "cancelled": True}
                return self._chat_completion_unlocked(
                    messages, tools=tools, on_token=on_token, options=options,
                    response_format=response_format, cancel_event=cancel_event,
                )
            finally:
                self.last_metrics["queue_wait_ms"] = queue_wait_ms

    def _chat_completion_unlocked(self, messages, tools=None, on_token=None, options=None, response_format=None,
                                  cancel_event=None):
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
        try:
            r = requests.post(self.url + "/api/chat", json=body, timeout=self.timeout, stream=streaming)
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
                    r = requests.post(self.url + "/api/chat", json=body, timeout=self.timeout, stream=streaming)
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
                if cancel_event is not None:
                    with self._active_request_lock:
                        self._active_response = r
                        self._active_cancel_event = cancel_event
                    try:
                        if cancel_event.is_set():
                            r.close()
                            return {"message": {"role": "assistant", "content": ""}, "cancelled": True}

                        def continue_stream(fragment):
                            if cancel_event.is_set():
                                return False
                            return on_token(fragment) is not False

                        data = self._consume_chat_stream(r, continue_stream)
                    finally:
                        with self._active_request_lock:
                            if self._active_response is r:
                                self._active_response = None
                                self._active_cancel_event = None
                else:
                    data = self._consume_chat_stream(r, on_token)
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
            if cancel_event is not None and cancel_event.is_set():
                self.last_metrics = {"request_ms": round((time.perf_counter() - started) * 1000, 1),
                                     "cancelled": True}
                return {"message": {"role": "assistant", "content": ""}, "cancelled": True}
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
