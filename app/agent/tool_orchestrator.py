"""CPU-conscious, allow-listed Ollama tool routing and execution."""
from __future__ import annotations

import json
import re
import math
import time
from collections import Counter


class ToolOrchestrator:
    MUTATING_TOOLS = {
        "save_memory", "add_task", "complete_task", "delete_task",
        "create_reminder", "start_timer",
    }
    ACTION_REQUEST = re.compile(
        r"\b(add|create|save|remember|store|complete|finish|mark .*complete|delete|remove|"
        r"set .*reminder|remind me|start .*timer|start .*countdown)\b", re.I,
    )
    SUCCESS_CLAIMS = {
        "add_task": re.compile(r"\b(task added|added (?:the )?task)\b", re.I),
        "complete_task": re.compile(r"\b(marked .*complete|marked .*done|task completed)\b", re.I),
        "create_reminder": re.compile(r"\b(reminder saved|reminder (?:is )?set)\b", re.I),
        "start_timer": re.compile(r"\b(timer started|countdown started)\b", re.I),
        "save_memory": re.compile(r"\b(saved (?:a )?(?:note|memory)|memory saved)\b", re.I),
        "delete_task": re.compile(r"\b(task deleted|deleted (?:the )?task)\b", re.I),
    }

    # Tool selection is retrieval, not a list of user-phrase regexes. BM25 ranks each
    # registered tool's name, description, category and parameter descriptions against
    # the utterance. The LLM then chooses whether to call a retrieved tool and supplies
    # arguments; the retriever only reduces schema-token overhead.
    _STOPWORDS = {
        "a", "an", "the", "i", "me", "my", "mine", "you", "your", "yours",
        "we", "our", "us", "is", "are", "am", "was", "were", "be", "been",
        "being", "do", "does", "did", "can", "could", "would", "should",
        "will", "would", "to", "for", "of", "in", "on", "at", "by", "with",
        "and", "or", "but", "if", "then", "than", "that", "this", "it",
        "there", "here", "please", "hey", "hi", "hello", "just", "really",
        "tell", "me", "about", "please", "right", "now", "what", "which",
        "who", "where", "when", "why", "how", "have", "has", "had", "got",
        "get", "as", "from", "into", "very", "much", "like", "something", "second", "seconds",
    }
    # Small vocabulary-level synonym expansion. Unlike phrase rules, these terms are
    # added to the query before ranking and do not select/execute a tool by themselves.
    _CONCEPTS = {
        "remember": {"memory", "history", "conversation", "recall", "past"},
        "recall": {"memory", "history", "conversation", "remember"},
        "previous": {"past", "history", "conversation", "earlier"},
        "previously": {"past", "history", "conversation", "earlier"},
        "earlier": {"past", "history", "conversation"},
        "chat": {"conversation", "history"},
        "chats": {"conversation", "history"},
        "said": {"conversation", "history", "memory"},
        "told": {"conversation", "history", "memory"},
        "discussed": {"conversation", "history"},
        "completed": {"complete", "done", "task"},
        "finished": {"finish", "complete", "done", "task"},
        "done": {"complete", "task"},
        "todo": {"task", "assignment"},
        "assignment": {"task", "todo"},
        "working": {"activity", "desktop", "window", "context"},
        "doing": {"activity", "desktop", "window", "context"},
        "screen": {"desktop", "window", "context"},
        "tab": {"desktop", "window", "browser", "activity", "context"},
        "browser": {"tab", "window", "desktop", "context"},
        "viewing": {"view", "tab", "window", "activity"},
        "view": {"viewing", "tab", "window", "activity"},
        "look": {"camera", "appearance", "expression", "see"},
        "see": {"camera", "appearance", "expression"},
        "seeing": {"camera", "appearance", "expression", "see"},
        "appearance": {"camera", "look", "expression"},
        "features": {"capability", "tools"},
        "tool": {"capability", "feature"},
        "tools": {"capability", "feature"},
    }
    MAX_ROUTED_TOOLS = 6

    @classmethod
    def _normalise_token(cls, token):
        token = token.casefold()
        if len(token) > 6 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
            if len(token) > 2 and token[-1] == token[-2]:
                token = token[:-1]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
            if token.endswith("t"):
                token += "e"
        elif len(token) > 5 and token.endswith("ly"):
            token = token[:-2]
        elif (len(token) > 3 and token.endswith("s") and not token.endswith("ss")
              and not token.endswith(("ous", "us", "is"))):
            token = token[:-1]
        return token

    @classmethod
    def _tokens(cls, text):
        """Tokenize tool/user text, retaining term frequency for proper BM25 scoring."""
        raw = re.findall(r"[a-zA-Z0-9]+", str(text or "").casefold())
        result = []
        for item in raw:
            token = cls._normalise_token(item)
            # These are vocabulary expansions, not action rules: they improve lexical
            # recall while the LLM remains responsible for choosing/executing a tool.
            expansions = set(cls._CONCEPTS.get(item, ())) | set(cls._CONCEPTS.get(token, ()))
            if token and token not in cls._STOPWORDS and len(token) > 1:
                result.append(token)
            result.extend(x for x in expansions if x not in cls._STOPWORDS)
        return result

    @staticmethod
    def _tool_document(spec):
        # Parameter names such as `seconds` can be noisy (e.g. "Newton's second law").
        # Use parameter descriptions/enums rather than raw field names in retrieval text.
        fields = [
            spec.name.replace("_", " "), spec.description, spec.category, spec.capability_id,
        ]
        for value in (spec.parameters.get("properties") or {}).values():
            fields.extend((str(value.get("description") or ""),
                           " ".join(str(x) for x in value.get("enum", []))))
        return " ".join(fields)

    def _rank_tools(self, user_text):
        specs = list(self.registry._tools.values())
        query_tokens = self._tokens(user_text)
        if not specs or not query_tokens:
            return []
        query_terms = set(query_tokens)
        docs = [self._tokens(self._tool_document(spec)) for spec in specs]
        doc_counts = [Counter(doc) for doc in docs]
        df = Counter(term for counts in doc_counts for term in counts.keys())
        avg_len = sum(max(1, sum(counts.values())) for counts in doc_counts) / max(1, len(doc_counts))
        n_docs = len(docs)
        k1, b = 1.5, 0.75
        ranked = []
        for spec, counts in zip(specs, doc_counts):
            doc_len = max(1, sum(counts.values()))
            score = 0.0
            for term in query_terms.intersection(counts):
                tf = counts[term]
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_len))
            if score > 0:
                ranked.append((score, spec.name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked

    @staticmethod
    def _normalise_vector(vector):
        norm = math.sqrt(sum(float(x) * float(x) for x in vector)) or 1.0
        return [float(x) / norm for x in vector]

    def _semantic_rank_tools(self, user_text):
        """Optional local-embedding fallback for paraphrases lexical BM25 misses.

        Tool texts are embedded once per registry version and cached in memory. If the
        embedding model isn't installed/reachable, lexical retrieval remains available.
        """
        embed = getattr(self.llm, "embed", None)
        if not callable(embed):
            return []
        if time.monotonic() < getattr(self, "_semantic_retry_after", 0.0):
            return []
        specs = list(self.registry._tools.values())
        documents = [self._tool_document(spec) for spec in specs]
        fingerprint = tuple((spec.name, doc) for spec, doc in zip(specs, documents))
        try:
            if getattr(self, "_semantic_fingerprint", None) != fingerprint:
                # One batch request initializes both the user query and tool index.
                vectors = embed(["search_query: " + str(user_text or "")] + ["search_document: " + doc for doc in documents])
                if len(vectors) != len(documents) + 1:
                    return []
                query_vector = self._normalise_vector(vectors[0])
                self._semantic_vectors = {
                    spec.name: self._normalise_vector(vector)
                    for spec, vector in zip(specs, vectors[1:])
                }
                self._semantic_fingerprint = fingerprint
            else:
                vectors = embed(["search_query: " + str(user_text or "")])
                if not vectors:
                    return []
                query_vector = self._normalise_vector(vectors[0])
            ranked = []
            for spec in specs:
                vector = self._semantic_vectors.get(spec.name)
                if not vector or len(vector) != len(query_vector):
                    continue
                similarity = sum(a * b for a, b in zip(query_vector, vector))
                ranked.append((similarity, spec.name))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            if not ranked or ranked[0][0] < 0.30:
                self._semantic_retry_after = 0.0
                return []
            self._semantic_retry_after = 0.0
            best = ranked[0][0]
            return [(score, name) for score, name in ranked
                    if score >= max(0.30, best - 0.10)][:self.MAX_ROUTED_TOOLS]
        except Exception:
            # Don't repeatedly hit a missing/offline embedding model on each turn.
            # After a short cooldown, try again so a newly installed model can recover.
            self._semantic_retry_after = time.monotonic() + 60.0
            return []

    @staticmethod
    def _normalise_phrase(text):
        return " ".join(re.findall(r"[a-z0-9]+", str(text or "").casefold()))

    @classmethod
    def _phrase_present(cls, text, phrase):
        haystack = " " + cls._normalise_phrase(text) + " "
        needle = " " + cls._normalise_phrase(phrase) + " "
        return bool(needle.strip()) and needle in haystack

    def _hint_routes(self, query):
        """Read the user's editable intent_dictionary.json and return only explicit routes.

        Phrase and keyword-group hints are deliberately lightweight: no extra model,
        embedding request, or fuzzy matching happens on the ordinary chat path.
        """
        config = self.route_config
        if not isinstance(config, dict):
            return None
        hints = config.get("tool_route_hints") or {}
        matched = set()
        for tool_name, rule in hints.items():
            if tool_name not in self.registry._tools or not isinstance(rule, dict):
                continue
            if any(self._phrase_present(query, phrase) for phrase in rule.get("exclude_phrases", [])):
                continue
            phrases = rule.get("phrases", [])
            groups = rule.get("keyword_groups", [])
            if any(self._phrase_present(query, phrase) for phrase in phrases):
                matched.add(tool_name)
                continue
            # A group match is permitted only for action-oriented schemas with explicit
            # groups in the editable file; it does not match a lone noun such as "task".
            if groups and all(any(self._phrase_present(query, phrase) for phrase in group)
                              for group in groups):
                matched.add(tool_name)
        if matched:
            # Preserve the registry's canonical order and cap prompt size.
            return [name for name in self.registry.names() if name in matched][:self.MAX_ROUTED_TOOLS]

        # Only spend BM25 work when the input resembles an explicit operation/query.
        for pattern in config.get("tool_route_fallback_patterns", []):
            try:
                if re.search(pattern, str(query), re.I | re.S):
                    ranked = self._rank_tools(query)
                    if not ranked:
                        return []
                    best = ranked[0][0]
                    floor = max(2.5, best * 0.58)
                    return [name for score, name in ranked if score >= floor][:self.MAX_ROUTED_TOOLS]
            except re.error:
                continue
        return []

    def tools_for_text(self, user_text):
        """Route only likely tool requests. LLM chooses and arguments; Python validates.

        With the editable route dictionary installed, ordinary conversation receives no
        tool schemas. BM25 is an explicit-operation fallback; embeddings are not called
        on the normal chat path.
        """
        query = str(user_text or "")
        if query in self._route_cache:
            self.last_route = list(self._route_cache[query])
            return list(self.last_route)
        if self.retrieval_mode == "all":
            chosen = self.registry.names()
            self.last_route = list(chosen)
            self._route_cache[query] = list(chosen)
            return list(chosen)
        if self.route_config:
            chosen = self._hint_routes(query) or []
            self.last_route = list(chosen)
            self._route_cache[query] = list(chosen)
            if len(self._route_cache) > 96:
                self._route_cache.pop(next(iter(self._route_cache)))
            return list(chosen)
        ranked = self._rank_tools(query)
        # A strong lexical match is cheaper than an embedding request. Lower-confidence
        # matches get semantic retrieval to separate similar tools (e.g. list vs complete).
        semantic = []
        if self.retrieval_mode == "hybrid" and (not ranked or ranked[0][0] < 3.0):
            semantic = self._semantic_rank_tools(query)
        if semantic:
            lexical_by_name = {name: score for score, name in ranked}
            max_lexical = max(lexical_by_name.values(), default=0.0) or 1.0
            semantic_by_name = {name: score for score, name in semantic}
            selected_scores = {}
            for name in set(lexical_by_name) | set(semantic_by_name):
                lexical_score = min(1.0, lexical_by_name.get(name, 0.0) / max_lexical)
                semantic_score = semantic_by_name.get(name, 0.0)
                # Semantic evidence outweighs broad lexical overlap while retaining
                # exact lexical matches as a useful signal.
                selected_scores[name] = 0.60 * semantic_score + 0.40 * lexical_score
            best = max(selected_scores.values(), default=0.0)
            chosen_set = {name for name, score in selected_scores.items()
                          if score >= max(0.25, best - 0.16)}
        elif ranked:
            best = ranked[0][0]
            chosen_set = {name for score, name in ranked
                          if score >= max(0.25, best * 0.20)}
        else:
            # If the prompt is so generic that it has no content terms (e.g. "What can
            # you do?"), expose only runtime introspection. Other unmatched questions
            # stay on the tool-free chat path rather than receiving irrelevant schemas.
            if not self._tokens(query) and "get_capabilities" in self.registry._tools:
                chosen_set = {"get_capabilities"}
            else:
                chosen_set = set()
        # Preserve registry order for stable prompts and keep schema payload bounded.
        chosen = [name for name in self.registry.names() if name in chosen_set][:self.MAX_ROUTED_TOOLS]
        self.last_route = chosen
        self._route_cache[query] = list(chosen)
        if len(self._route_cache) > 64:
            self._route_cache.pop(next(iter(self._route_cache)))
        return list(chosen)

    def __init__(self, llm, registry, monitor=None, max_rounds=2, max_calls_per_turn=4,
                 max_routed_tools=4, retrieval_mode="bm25", routing_config_path=None):
        self.llm = llm
        self.registry = registry
        self.monitor = monitor
        self.max_rounds = max(1, int(max_rounds))
        self.max_calls_per_turn = max(1, int(max_calls_per_turn))
        self.MAX_ROUTED_TOOLS = max(2, min(int(max_routed_tools), len(registry.names()) or 2))
        self.retrieval_mode = str(retrieval_mode or "bm25").casefold()
        if self.retrieval_mode not in {"hybrid", "bm25", "all"}:
            self.retrieval_mode = "bm25"
        self.routing_config_path = routing_config_path
        self.route_config = {}
        if routing_config_path:
            try:
                with open(routing_config_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                self.route_config = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, TypeError):
                self.route_config = {}
        self.last_route = []
        self._route_cache = {}
        self._semantic_retry_after = 0.0
        self.last_tool_calls = 0
        self.last_model_rounds = 0

    @staticmethod
    def _json_result(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"ok": False, "error": "Tool returned a non-serializable result"})

    def run(self, messages, on_token=None, options=None):
        if not callable(getattr(self.llm, "chat_completion", None)):
            # Compatibility with legacy clients/test doubles. Streaming is supported
            # by OllamaClient; older adapters continue to work without the callback.
            return self.llm.chat(messages)
        if self.monitor:
            with self.monitor.track("agent_tool_calling", "LLM tool orchestration",
                                    "Milo agent", "Tool schemas and private message content omitted"):
                return self._run_tool_loop(messages, on_token=on_token, options=options)
        return self._run_tool_loop(messages, on_token=on_token, options=options)

    def _direct_tool_reply(self, name, payload):
        """Format common, verified utility results without a second LLM pass.

        ``None`` means the result needs model interpretation (for example memory
        search, capability inventory, camera status, or an unexpected result).
        """
        if not isinstance(payload, dict) or not payload.get("ok"):
            if name == "get_camera_status":
                return "I couldn't verify the camera state from the app, so I won't claim that I can see you."
            if name == "get_desktop_context":
                return "I couldn't verify the current desktop context, so I won't guess what you're doing. I cannot inspect screen pixels or browser page contents."
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None

        if name == "get_camera_status":
            if result.get("available") is False:
                return "I can't check camera status because the desktop camera controller isn't connected. I won't guess whether the camera is active."
            if not result.get("preview_active"):
                return "I can't see you right now: the camera preview is off, so there are no live facial cues. Camera images are not sent to the language model."
            sample = str(result.get("latest_expression_sample") or "").strip()
            if not result.get("analysis_available"):
                reason = str(result.get("analysis_reason") or "the local expression model is unavailable").strip()
                return ("The camera preview is active, but local facial-expression analysis is unavailable: "
                        + reason + ". I can't describe your appearance or expression from the raw image because it is not sent to the language model.")
            if sample:
                sample = sample[:650]
                return ("The camera preview is active, but I don't receive or view raw camera images. "
                        "The local expression analyser reports: " + sample + " This is an uncertain visual cue, not proof of your mood.")
            return "The camera preview is active, but there isn't a fresh local facial-expression sample yet. I don't receive raw camera images, so I won't guess how you look."
        elif name == "get_desktop_context":
            if result.get("awareness_enabled") is False:
                return "Desktop Awareness is disabled in Settings, so I don't have current foreground-app context. I can't see screen pixels or browser page contents."
            if result.get("available") is False:
                return "Desktop Awareness is enabled in Settings, but live foreground-app context isn't available on this system. I can't see screen pixels or page contents."
            snapshot = result.get("snapshot") or {}
            category = str(snapshot.get("app_category") or result.get("foreground_category") or "Unknown app")
            app_name = str(snapshot.get("app_name") or "").strip()
            title = str(snapshot.get("window_title") or "").strip()
            if title == "[title hidden for privacy]":
                title = ""
            bits = []
            if app_name and app_name.lower() not in {"unknown", "unknown.exe"}:
                bits.append("foreground app " + app_name)
            bits.append("category " + category)
            if title:
                bits.append("window title " + repr(title[:120]))
            idle = snapshot.get("idle_seconds", result.get("idle_seconds"))
            detail = "; ".join(bits)
            if idle is not None:
                detail += f"; system idle for about {int(idle)} seconds"
            return ("Desktop Awareness reports " + detail + ". I can use the app/category and available window title as clues to make a tentative guess, "
                    "but I cannot see screen pixels, inspect browser page contents, or know exactly what you're doing from the title alone.")
        elif name == "get_capabilities":
            capabilities = result.get("capabilities") or []
            if capabilities:
                lines = ["Here is the current capability inventory from the running app:"]
                for item in capabilities:
                    status = str(item.get("status", "unknown")).upper()
                    name_text = str(item.get("name", item.get("id", "Capability")))
                    detail = str(item.get("detail", "")).strip()
                    lines.append(f"- {name_text}: {status}" + (f" — {detail}" if detail else ""))
                lines.append("I only have access to the tools and local context listed here; I cannot inspect screen pixels, browser page contents, or raw camera images.")
                return "\n".join(lines)
        elif name == "search_memory":
            if result.get("available") is False:
                return "I can't access the local memory database right now, so I can't verify saved notes or past turns."
            if not any(key in result for key in ("scope", "query", "facts", "notes", "conversation_matches", "profile")):
                # Unknown/custom memory handlers may have their own result shape;
                # do not pretend it is an empty search result.
                return None
            sections = []
            facts = result.get("facts") or []
            notes = result.get("notes") or []
            turns = result.get("conversation_matches") or []
            if facts:
                sections.append("Saved facts:\n" + "\n".join("- " + str(x) for x in facts[:5]))
            if notes:
                sections.append("Saved notes/summaries:\n" + "\n".join(
                    "- " + str(n.get("period") or "") + (": " if n.get("period") else "") + str(n.get("text") or "")[:240]
                    for n in notes[:5]))
            if turns:
                sections.append("Matching conversation turns:\n" + "\n".join(
                    "- " + (f"[{t.get('time')}] " if t.get("time") else "") + str(t.get("role") or "") + ": " + str(t.get("text") or "")[:220]
                    for t in turns[:5]))
            profile = result.get("profile") or {}
            if profile:
                sections.append("Saved profile:\n" + "\n".join(f"- {k}: {v}" for k, v in profile.items()))
            if not sections:
                # Empty retrieval is not a final conclusion: the model may need a different
                # structured source (e.g. task history) on the next tool round.
                return None
            return "I found these records in this Study Buddy profile's local memory:\n\n" + "\n\n".join(sections)
        elif name == "get_user_profile":
            if result.get("available") is False:
                return "The local profile memory is not connected, so I can't verify saved profile details."
            if result:
                return "Saved profile details:\n" + "\n".join(f"- {k}: {v}" for k, v in result.items())
            return "The local profile database is connected, but I don't have saved profile fields to show."
        elif name == "add_task":
            task = result.get("task") or {}
            if result.get("created") and task.get("title"):
                return f"Task added: {task['title']}."
        elif name == "complete_task":
            if result.get("completed"):
                return f"Marked '{result.get('title', 'the task')}' as complete."
            candidates = result.get("candidates") or []
            if candidates:
                titles = "; ".join(str(item.get("title", "")) for item in candidates[:5] if item.get("title"))
                return "I couldn't identify one task safely. Which one did you mean?" + (" " + titles if titles else "")
            if result.get("reason"):
                return "I couldn't complete that task: " + str(result["reason"]) + "."
        elif name == "delete_task":
            if result.get("deleted"):
                return f"Deleted task: {result.get('title') or 'the selected task'}."
            candidates = result.get("candidates") or []
            if candidates:
                titles = "; ".join(str(item.get("title", "")) for item in candidates[:5] if item.get("title"))
                return "I couldn't identify one task to delete. Which one did you mean?" + (" " + titles if titles else "")
            return "I couldn't find a unique matching open task, so nothing was deleted."
        elif name == "create_reminder":
            if result.get("created"):
                due = result.get("due_at")
                text = result.get("text") or "your reminder"
                return f"Reminder saved for {text}" + (f" ({due})." if due else ".")
            if result.get("needs_followup"):
                return str(result.get("question") or "What should I remind you about?")
        elif name == "start_timer":
            if result.get("started"):
                seconds = int(result.get("seconds") or 0)
                if seconds % 3600 == 0:
                    duration = f"{seconds // 3600} hour(s)"
                elif seconds % 60 == 0:
                    duration = f"{seconds // 60} minute(s)"
                else:
                    duration = f"{seconds} seconds"
                return f"Timer started for {duration}. I'll notify you when it finishes."
            if result.get("reason"):
                return "I couldn't start the timer: " + str(result["reason"]) + "."
        elif name == "save_memory":
            if result.get("saved"):
                return "Saved that to your local memory."
            if result.get("reason"):
                return "I couldn't save that: " + str(result["reason"]) + "."
        elif name == "list_tasks":
            tasks = result.get("tasks") or []
            task_filter = str(result.get("filter") or "open_only")
            completed_only = task_filter == "completed_only"
            include_completed = task_filter in {"open_and_completed", "completed_only"} or any(
                str(task.get("status", "")).casefold() in {"done", "completed"} for task in tasks)
            if not tasks:
                if completed_only:
                    return "Your completed task list is empty."
                return "No matching completed or open tasks were found." if include_completed else "Your open task list is empty."
            lines = []
            for task in tasks[:12]:
                status = str(task.get("status") or "open").casefold()
                label = "Completed" if status in {"done", "completed"} else "Open"
                line = f"- [{label}] " + str(task.get("title") or "Untitled task") if include_completed else "- " + str(task.get("title") or "Untitled task")
                if task.get("due_at"):
                    line += " (due " + str(task["due_at"]) + ")"
                lines.append(line)
            heading = "Your completed tasks:" if completed_only else ("Your tasks (open and completed):" if include_completed else "Your open tasks:")
            return heading + "\n" + "\n".join(lines)
        elif name == "list_reminders":
            reminders = result.get("reminders") or []
            if not reminders:
                return "You have no pending reminders."
            lines = [f"- {item.get('text', 'Reminder')} ({item.get('due_at', 'time unknown')})"
                     for item in reminders[:12]]
            return "Your pending reminders:\n" + "\n".join(lines)
        return None

    def _chat_completion(self, messages, *, tools=None, on_token=None, options=None):
        """Call compatible local clients with request-scoped generation options."""
        import inspect
        method = self.llm.chat_completion
        kwargs = {}
        if tools is not None: kwargs["tools"] = tools
        if callable(on_token): kwargs["on_token"] = on_token
        if options is not None:
            try:
                params = inspect.signature(method).parameters
                if "options" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                    kwargs["options"] = options
            except (TypeError, ValueError):
                pass
        return method(messages, **kwargs)

    def _synthesize_lookup(self, name, working, fallback, on_token=None, options=None):
        """Let the LLM interpret verified lookup results without access to more tools."""
        if not callable(getattr(self.llm, "chat_completion", None)):
            return fallback
        augmented = [dict(message) for message in working]
        policy = (
            "\n\nVERIFIED LOOKUP RESPONSE: The immediately preceding tool result is the source of truth. "
            "Answer the user's latest question using that result, not a generic denial or invented details. "
        )
        if name == "get_desktop_context":
            policy += (
                "For desktop-awareness results, make one brief educated guess from the foreground app and the "
                "privacy-filtered active-window title/category. Naturally signal uncertainty. If title suggests a video, "
                "document, coding project, or research, you may infer a likely activity without claiming to know its content. "
                "Do not say you cannot infer anything when useful title clues are provided. Never claim to see pixels, read a webpage, "
                "or watch a video. If the title is hidden or only a generic category is available, be correspondingly cautious."
            )
        else:
            policy += (
                "For memory search results, summarize only the returned saved facts, notes, or previous turns. Never claim "
                "you lack memory access when records are present. If the result contains no relevant records, say so plainly. "
                "These are records in this app's local profile, not a guarantee of access to other apps or chat histories."
            )
        if augmented and augmented[0].get("role") == "system":
            augmented[0]["content"] = str(augmented[0].get("content") or "") + policy
        else:
            augmented.insert(0, {"role": "system", "content": policy})
        try:
            self.last_model_rounds += 1
            if callable(on_token):
                response = self._chat_completion(augmented, tools=[], on_token=on_token, options=options)
            else:
                response = self._chat_completion(augmented, tools=[], options=options)
            if response.get("cancelled"):
                return fallback
            answer = str((response.get("message") or {}).get("content") or "").strip()
        except Exception:
            return fallback
        if not answer:
            return fallback
        if name == "get_desktop_context":
            # Reject clear overclaims; the deterministic fallback retains app/title provenance.
            if re.search(r"\b(i can see your screen|i can read the webpage|i watched the video|i inspected the page|i know exactly what you are doing)\b", answer, re.I):
                return fallback
            if re.search(r"\b(i can.t see what you.re doing|i cannot tell what you.re doing)\b", answer, re.I) and not re.search(r"\b(looks like|may be|might be|probably|guess|seems|likely|suggests)\b", answer, re.I):
                return fallback
        else:
            if re.search(r"\b(i (?:do not|don't|cannot|can't) (?:have )?(?:access|memory|remember)|i have no memory|cannot access previous conversations)\b", answer, re.I):
                return fallback
        return answer

    def _run_tool_loop(self, messages, on_token=None, options=None):
        working = [dict(m) for m in messages]
        user_text = next((str(m.get("content") or "") for m in reversed(working)
                          if m.get("role") == "user"), "")
        selected_names = self.tools_for_text(user_text)
        schemas = self.registry.definitions(selected_names)
        self.last_tool_calls = 0
        self.last_model_rounds = 0

        # Normal conversation never pays the token cost of all tool definitions.
        # It still uses exactly one model pass and cannot accidentally trigger an action.
        if not schemas:
            self.last_model_rounds = 1
            if callable(on_token):
                response = self._chat_completion(working, tools=[], on_token=on_token, options=options)
            else:
                response = self._chat_completion(working, tools=[], options=options)
            message = response.get("message") or {}
            content = str(message.get("content") or "").strip()
            if response.get("cancelled"):
                return ""
            unsupported_claims = [name for name, pattern in self.SUCCESS_CLAIMS.items()
                                  if pattern.search(content)]
            if self.ACTION_REQUEST.search(user_text) and unsupported_claims:
                return ("I haven't confirmed that action through an available tool, so I haven't changed anything. "
                        "Please check the relevant tool status or retry with a supported command.")
            return content or "I couldn't generate a response just now. Please try again."

        calls_used = 0
        successful_mutations = set()
        for _round_index in range(self.max_rounds):
            self.last_model_rounds += 1
            if callable(on_token):
                response = self._chat_completion(working, tools=schemas, on_token=on_token, options=options)
            else:
                response = self._chat_completion(working, tools=schemas, options=options)
            message = response.get("message") or {}
            if response.get("cancelled"):
                return ""
            tool_calls = message.get("tool_calls") or []
            content = str(message.get("content") or "").strip()
            if not tool_calls:
                unsupported_claims = [name for name, pattern in self.SUCCESS_CLAIMS.items()
                                      if pattern.search(content) and name not in successful_mutations]
                if self.ACTION_REQUEST.search(user_text) and unsupported_claims:
                    return ("I haven't confirmed that action through the required tool, so I haven't changed anything. "
                            "Please try that request again or check the relevant tool status.")
                return content

            working.append(message)
            tool_results = []
            single_call_name = None
            for call in tool_calls:
                if calls_used >= self.max_calls_per_turn:
                    working.append({"role": "tool", "tool_name": "system", "content":
                                    self._json_result({"ok": False, "error": "Tool-call limit reached for this turn."})})
                    break
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                args = fn.get("arguments") or {}
                spec = self.registry._tools.get(name)
                if name not in selected_names:
                    payload = {"ok": False, "error": "That tool is not available for this request route."}
                else:
                    label = spec.name.replace("_", " ").title() if spec else name or "Unknown tool"
                    capability_id = (spec.capability_id or name) if spec else (name or "unknown_tool")
                    event_id = None
                    try:
                        if self.monitor:
                            event_id = self.monitor.start(capability_id, label, "LLM tool call",
                                                          "Arguments and returned content omitted")
                        result = self.registry.execute(name, args)
                        payload = {"ok": True, "result": result}
                        if name in self.MUTATING_TOOLS and isinstance(result, dict):
                            outcome_key = next((key for key in ("saved", "created", "completed", "deleted", "started")
                                                if key in result), None)
                            if outcome_key is not None and result.get(outcome_key) is True:
                                successful_mutations.add(name)
                        if self.monitor and event_id is not None:
                            self.monitor.finish(event_id, "success")
                    except Exception as exc:
                        payload = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
                        if self.monitor and event_id is not None:
                            self.monitor.finish(event_id, "failed", f"{type(exc).__name__}: details omitted")
                calls_used += 1
                self.last_tool_calls += 1
                tool_results.append((name, payload))
                working.append({"role": "tool", "tool_name": name or "unknown_tool",
                                "content": self._json_result(payload)})
            else:
                # A common, single action with a verified result is already complete.
                # Avoid a second CPU inference just to paraphrase structured data.
                if len(tool_calls) == 1 and len(tool_results) == 1:
                    called_name = tool_results[0][0]
                    direct = self._direct_tool_reply(tool_results[0][0], tool_results[0][1])
                    # Sensor state, capability state, and retrieved memory are
                    # authoritative data. Do not let a small model override them
                    # with a hallucinated free-form statement after a tool call.
                    if direct is not None:
                        called_payload = tool_results[0][1]
                        if called_name == "get_desktop_context":
                            result_obj = (called_payload.get("result") or {}) if isinstance(called_payload, dict) else {}
                            snapshot_obj = result_obj.get("snapshot") if isinstance(result_obj, dict) else None
                            if (isinstance(result_obj, dict) and result_obj.get("awareness_enabled") is not False
                                    and result_obj.get("available") is not False
                                    and isinstance(snapshot_obj, dict)
                                    and not snapshot_obj.get("private_context")
                                    and snapshot_obj.get("app_category") != "Private"):
                                return self._synthesize_lookup(called_name, working, direct, on_token=on_token, options=options)
                        if called_name == "search_memory":
                            result_obj = (called_payload.get("result") or {}) if isinstance(called_payload, dict) else {}
                            has_records = isinstance(result_obj, dict) and any(
                                result_obj.get(key) for key in ("facts", "notes", "conversation_matches", "profile"))
                            if has_records:
                                return self._synthesize_lookup(called_name, working, direct, on_token=on_token, options=options)
                        return direct
                continue
            break

        # Complex/multi-tool requests still get a final natural-language explanation.
        self.last_model_rounds += 1
        if callable(on_token):
            final = self._chat_completion(working, tools=[], on_token=on_token, options=options)
        else:
            final = self._chat_completion(working, tools=[], options=options)
        if final.get("cancelled"):
            return ""
        final_text = str((final.get("message") or {}).get("content") or
                         "I ran into the tool-call limit before finishing.").strip()
        unsupported_claims = [name for name, pattern in self.SUCCESS_CLAIMS.items()
                              if pattern.search(final_text) and name not in successful_mutations]
        if self.ACTION_REQUEST.search(user_text) and unsupported_claims:
            return ("I haven't confirmed that action through the required tool, so I haven't changed anything. "
                    "Please try that request again or check the relevant tool status.")
        return final_text
