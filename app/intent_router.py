"""Config-driven keyword intent detection and stateful confirmation workflows.

Vocabulary and user-facing prompts live in config/intent_dictionary.json. This module
contains generic matching/state-machine logic, not the app's editable word lists.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable


def normalise(text: str) -> str:
    text = str(text or "").casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return " ".join(text.split())


def contains_phrase(text: str, phrase: str) -> bool:
    haystack, needle = f" {normalise(text)} ", f" {normalise(phrase)} "
    return bool(normalise(phrase)) and needle in haystack


class IntentRouter:
    """Small deterministic router with editable vocabulary and safe dialog states."""

    def __init__(self, dictionary_path: str, clock: Callable[[], float] | None = None):
        self.dictionary_path = dictionary_path
        self._clock = clock or time.time
        self.config: dict[str, Any] = {}
        self.state: dict[str, Any] | None = None
        self.reload()

    def reload(self) -> None:
        with open(self.dictionary_path, "r", encoding="utf-8") as handle:
            self.config = json.load(handle)

    @property
    def has_pending(self) -> bool:
        # Onboarding prompts may be delivered while the user is away. Expire them
        # when queried so stale state cannot block another UI action indefinitely.
        if self.state is not None and self.state.get("dialog_type") in ("onboarding", "consent_fact", "nudge_checkin", "rhythm_consent"):
            updated_at = float(self.state.get("updated_at", 0) or 0)
            if self._clock() - updated_at >= 300:
                self.clear()
                return False
        return self.state is not None

    def clear(self) -> None:
        self.state = None

    def start_onboarding(self) -> str:
        """Start the local, skippable companion onboarding dialog."""
        self.state = {"dialog_type": "onboarding", "phase": "availability", "updated_at": self._clock(), "ambiguous_count": 0}
        return ("Would you like to spend a minute getting to know each other? "
                "You can say yes, no, or skip.")

    def _phrases_match(self, text: str, phrases) -> bool:
        return any(contains_phrase(text, phrase) for phrase in phrases or [])

    def _is_direct(self, text: str, patterns) -> bool:
        for pattern in patterns or []:
            try:
                if re.search(pattern, text, re.I | re.S):
                    return True
            except re.error:
                # A broken editable pattern must not prevent regular chat from working.
                continue
        return False

    def detect(self, text: str) -> dict[str, Any] | None:
        """Return a candidate only when keywords suggest an action but don't form
        an explicit direct command already handled by the normal action pipeline."""
        if not text or self._phrases_match(text, self.config.get("global_exclusions", [])):
            return None
        intents = self.config.get("intents", [])
        # First pass: a direct command for ANY intent wins over a weaker keyword
        # candidate for another intent (e.g. "add task: finish assignment" contains
        # both add-task vocabulary and the word "finish" in the task title).
        for intent in intents:
            if self._phrases_match(text, intent.get("exclude_phrases", [])):
                continue
            if self._is_direct(text, intent.get("direct_patterns", [])):
                return None
        for intent in intents:
            if self._phrases_match(text, intent.get("exclude_phrases", [])):
                continue
            groups = intent.get("keyword_groups", [])
            if not groups or not all(self._phrases_match(text, group) for group in groups):
                continue
            return intent
        return None

    def start(self, intent: dict[str, Any], original_text: str) -> str:
        self.state = {
            "intent_id": intent["id"],
            "phase": "confirm",
            "original_text": str(original_text),
        }
        return intent.get("confirm_prompt", "Did you want me to perform this action? Reply YES or NO.")

    def start_consent_fact(self, candidates: list[dict[str, Any]]) -> str:
        """Install a deterministic, non-tool consent dialog for queued candidate facts."""
        items = [{"id": str(x.get("id") or ""), "label": str(x.get("label") or "")[:120]} for x in candidates[:3]]
        self.state = {"dialog_type": "consent_fact", "phase": "consent", "items": items,
                      "updated_at": self._clock(), "ambiguous_count": 0}
        from companion.consent_dialog import render_batch
        return render_batch(items)

    def start_rhythm_consent(self) -> str:
        self.state={"dialog_type":"rhythm_consent","phase":"consent","updated_at":self._clock(),"ambiguous_count":0}
        return ("Would you like me to learn your usual study times? I store only coarse day, hour, and app-category counts on this PC; I never save window titles. Say yes, no, or skip.")

    def start_nudge_checkin(self, *, nudge_id: str, trigger: str, loop_id: str | None = None, suggested_message: str = "") -> str:
        self.state = {"dialog_type": "nudge_checkin", "phase": "reply", "nudge_id": str(nudge_id),
                      "trigger": str(trigger), "loop_id": loop_id, "suggested_message": str(suggested_message),
                      "updated_at": self._clock(), "ambiguous_count": 0}
        return "You can say yes, later, or stop reminding me."

    def _intent(self) -> dict[str, Any]:
        intent_id = (self.state or {}).get("intent_id")
        for item in self.config.get("intents", []):
            if item.get("id") == intent_id:
                return item
        return {}

    def _is_response(self, text: str, key: str) -> bool:
        value = normalise(text)
        phrases = [normalise(p) for p in self.config.get(key, [])]
        # Allow polite additions like "yes, please" while not matching arbitrary
        # text merely containing a yes/no token.
        return any(value == p or value.startswith(p + " ") for p in phrases if p)

    def _is_cancel(self, text: str) -> bool:
        return self._is_response(text, "cancel_phrases")

    def advance(self, text: str, open_tasks: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """Advance an active workflow. Returns a typed action for ChatCore to apply."""
        if not self.state:
            return None
        intent = self._intent()
        intent_id = self.state.get("intent_id", "")
        phase = self.state.get("phase", "confirm")

        if self.state.get("dialog_type") == "consent_fact":
            if self._clock() - float(self.state.get("updated_at", 0) or 0) >= 300:
                self.clear()
                return {"kind": "delegate_current", "original_text": str(text)}
            self.state["updated_at"] = self._clock()
            from companion.consent_dialog import parse_reply
            items = list(self.state.get("items") or [])
            action, indices = parse_reply(text, len(items))
            if action in ("confirm", "reject", "defer", "cancel"):
                selected = [items[i-1] for i in indices if 1 <= i <= len(items)]
                self.clear()
                if action == "confirm": return {"kind": "confirm_candidates", "items": selected, "all_items": items}
                if action == "reject": return {"kind": "reject_candidates", "items": selected, "all_items": items}
                if action in ("defer", "cancel"): return {"kind": "defer_candidates", "items": selected, "all_items": items}
            if action == "out_of_range":
                return {"kind":"reply", "reply":f"Pick numbers from 1 to {len(items)}, or say yes, no, or skip."}
            if self._looks_like_normal_message(text):
                self.clear()
                return {"kind": "delegate_current", "original_text": str(text)}
            count = int(self.state.get("ambiguous_count", 0)) + 1
            self.state["ambiguous_count"] = count
            if count >= 2:
                self.clear()
                return {"kind": "delegate_current", "original_text": str(text)}
            return {"kind": "reply", "reply": 'Please reply "yes", "no", or "skip" to the memory question.'}

        if self.state.get("dialog_type") == "rhythm_consent":
            if self._clock()-float(self.state.get("updated_at",0) or 0)>=300:
                self.clear(); return {"kind":"delegate_current","original_text":str(text)}
            self.state["updated_at"]=self._clock(); value=normalise(text)
            if value in {"yes","yes please","yes thanks","yes thank you","sure","okay","ok"}:
                self.clear(); return {"kind":"enable_rhythm"}
            if value in {"no","no thanks","no thank you","nope","nah"}:
                self.clear(); return {"kind":"decline_rhythm"}
            if value in {"skip","later","not now"}:
                self.clear(); return {"kind":"defer_rhythm"}
            if self._looks_like_normal_message(text):
                self.clear(); return {"kind":"delegate_current","original_text":str(text)}
            count=int(self.state.get("ambiguous_count",0))+1; self.state["ambiguous_count"]=count
            if count>=2:
                self.clear(); return {"kind":"delegate_current","original_text":str(text)}
            return {"kind":"reply","reply":"You can say yes, no, or skip. You can also enable rhythm learning in Settings later."}

        if self.state.get("dialog_type") == "nudge_checkin":
            if self._clock() - float(self.state.get("updated_at", 0) or 0) >= 300:
                self.clear(); return {"kind": "delegate_current", "original_text": str(text)}
            self.state["updated_at"] = self._clock()
            value=normalise(text)
            if value in {"yes", "yes please", "sure", "help me", "plan it"}:
                message=str(self.state.get("suggested_message") or "Help me plan my next study session.")
                loop_id=self.state.get("loop_id"); self.clear()
                return {"kind":"nudge_yes", "original_text":message, "loop_id":loop_id}
            if value in {"later", "not now", "skip", "no thanks"}:
                loop_id=self.state.get("loop_id"); self.clear()
                return {"kind":"nudge_later", "loop_id":loop_id}
            if value in {"stop", "stop reminding me", "done", "drop", "no", "leave me alone", "less often"}:
                loop_id=self.state.get("loop_id"); self.clear()
                return {"kind":"nudge_stop", "loop_id":loop_id, "negative": value in {"stop", "stop reminding me", "leave me alone", "less often"}}
            if self._looks_like_normal_message(text):
                self.clear(); return {"kind":"delegate_current", "original_text":str(text)}
            count=int(self.state.get("ambiguous_count",0))+1; self.state["ambiguous_count"]=count
            if count>=2:
                self.clear(); return {"kind":"delegate_current", "original_text":str(text)}
            return {"kind":"reply", "reply":"You can say yes, later, or stop reminding me."}

        if self.state.get("dialog_type") == "onboarding":
            # Expire only this conversational dialog; the current message is then
            # reprocessed normally by ChatCore. The clock is injectable for tests.
            if self._clock() - float(self.state.get("updated_at", 0) or 0) >= 300:
                self.clear()
                return {"kind": "delegate_current", "original_text": str(text)}
            self.state["updated_at"] = self._clock()
            if self._is_cancel(text):
                self.clear()
                return {"kind": "reply", "reply": "No problem. I'll leave onboarding alone."}
            if self._is_response(text, "skip_phrases"):
                self.clear()
                return {"kind": "reply", "reply": "Okay, skipped. You can start again from My World whenever you like."}
            if phase in ("availability", "confirm_memory") and self._looks_like_normal_message(text):
                self.clear()
                return {"kind": "delegate_current", "original_text": str(text)}
            if phase == "availability":
                if self._is_response(text, "no_phrases"):
                    self.clear()
                    return {"kind": "reply", "reply": "No problem. We can leave this for another time."}
                if not self._is_response(text, "yes_phrases"):
                    return self._ambiguous_onboarding_reply(text)
                self.state["phase"] = "collect_name"
                self.state["ambiguous_count"] = 0
                return {"kind": "reply", "reply": "What name would you like me to use for you? You can also say skip."}
            if phase == "collect_name":
                if self._is_response(text, "no_phrases"):
                    self.clear()
                    return {"kind": "reply", "reply": "No problem. I won't ask for a name."}
                if self._is_response(text, "yes_phrases"):
                    retries = int(self.state.get("name_retry_count", 0))
                    if retries >= 1:
                        self.clear()
                        return {"kind": "delegate_current", "original_text": str(text)}
                    self.state["name_retry_count"] = retries + 1
                    return {"kind": "reply", "reply": "I need the name you'd like me to use, or you can say skip."}
                if self._looks_like_normal_message(text):
                    self.clear()
                    return {"kind": "delegate_current", "original_text": str(text)}
                value = self._clean_plausible_name(text)
                if not value:
                    retries = int(self.state.get("name_retry_count", 0))
                    if retries >= 1:
                        self.clear()
                        return {"kind": "delegate_current", "original_text": str(text)}
                    self.state["name_retry_count"] = retries + 1
                    return {"kind": "reply", "reply": "Please share a plausible name (1–3 words), or say skip."}
                self.state["candidate_label"] = "Preferred name: " + value
                self.state["candidate_name"] = value
                self.state["phase"] = "confirm_memory"
                self.state["ambiguous_count"] = 0
                return {"kind": "reply", "reply": f"Would you like me to remember '{value}' as your preferred name? Say yes, no, or skip."}
            if phase == "confirm_memory":
                label = self.state.get("candidate_label", "")
                if self._is_response(text, "yes_phrases"):
                    self.clear()
                    return {"kind": "save_companion_fact", "label": label, "fact_kind": "profile"}
                if self._is_response(text, "no_phrases"):
                    self.clear()
                    return {"kind": "reply", "reply": "Okay. I won't save that as a personal memory."}
                return self._ambiguous_onboarding_reply(text)

        if self._is_cancel(text):
            self.clear()
            return {"kind": "reply", "reply": "Okay, I cancelled that action."}

        if phase == "confirm":
            if self._is_response(text, "no_phrases"):
                original = self.state.get("original_text", "")
                self.clear()
                return {"kind": "delegate_original", "original_text": original}
            if not self._is_response(text, "yes_phrases"):
                return {"kind": "reply", "reply": "Please reply YES to continue with that action, or NO to send your original message to normal chat."}
            if intent_id == "complete_task":
                tasks = list(open_tasks or [])
                if not tasks:
                    self.clear()
                    return {"kind": "reply", "reply": intent.get("no_items_prompt", "There are no open tasks.")}
                self.state["phase"] = "select_task"
                self.state["items"] = [{"id": int(task["id"]), "title": str(task["title"])} for task in tasks]
                lines = [f"{index}. {item['title']}" for index, item in enumerate(self.state["items"], start=1)]
                prompt = intent.get("details_prompt", "Which task should I complete?")
                return {"kind": "reply", "reply": prompt + "\n\n" + "\n".join(lines[:20])}
            self.state["phase"] = "collect_details"
            return {"kind": "reply", "reply": intent.get("details_prompt", "Please provide the details.")}

        if phase == "select_task":
            items = self.state.get("items", [])
            value = normalise(text).strip()
            selected = None
            if value.isdigit():
                index = int(value) - 1
                if 0 <= index < len(items):
                    selected = items[index]
            if selected is None:
                exact = [item for item in items if normalise(item["title"]) == value]
                partial = [item for item in items if value and value in normalise(item["title"])]
                candidates = exact or partial
                if len(candidates) == 1:
                    selected = candidates[0]
            if selected:
                return {"kind": "execute", "intent": intent_id, "task_id": selected["id"],
                        "task_title": selected["title"]}
            return {"kind": "reply", "reply": intent.get("retry_prompt", "Reply with a task number or exact title.")}

        if phase in ("collect_details", "collect_reminder_time"):
            return {"kind": "execute", "intent": intent_id, "text": str(text).strip(),
                    "partial_text": self.state.get("partial_text", ""), "phase": phase}
        return None

    def _ambiguous_onboarding_reply(self, text: str) -> dict[str, Any]:
        count = int((self.state or {}).get("ambiguous_count", 0)) + 1
        if self.state is not None:
            self.state["ambiguous_count"] = count
        if count >= 2:
            self.clear()
            return {"kind": "delegate_current", "original_text": str(text)}
        if (self.state or {}).get("phase") == "availability":
            reply = "Just say yes, no, or skip. No pressure."
        else:
            reply = "Please say yes to remember it, no to decline, or skip to leave it unsaved."
        return {"kind": "reply", "reply": reply}

    @staticmethod
    def _looks_like_normal_message(text: str) -> bool:
        value = str(text or "").strip()
        words = value.split()
        if "?" in value or len(words) > 6:
            return True
        return bool(re.match(
            r"(?i)^(?:why|what|how|when|where|who|can you|could you|would you|please explain|"
            r"explain|tell me|show me|help me|define|summari[sz]e|compare|solve|calculate|"
            r"add|create|make|set|remind|start|delete|complete|find|search|open|write|draft|translate|"
            r"remember|forget|i need|i want|i have)\b", value
        ))

    @staticmethod
    def _clean_plausible_name(text: str) -> str | None:
        value = str(text or "").strip()
        value = re.sub(r"(?i)^(?:my name is|name is|call me|i am|i'm|it's)\s+", "", value).strip()
        if not value or len(value) > 40 or "?" in value:
            return None
        blocked={"ignore","system","assistant","previous","instructions","instruction","prompt","you","please","nothing","none","anything","skip","stop","yes","no","help","cancel","override","jailbreak","pretend","disregard","forget","all","later","okay","ok","thanks","thank","hello","hi","user","name"}
        if any(word.casefold() in blocked for word in value.split()):
            return None
        if not all(ch.isalpha() or ch in " '-" for ch in value):
            return None
        words = value.split()
        if not 1 <= len(words) <= 3 or any(not any(ch.isalpha() for ch in word) for word in words):
            return None
        return " ".join(words)

    def set_phase(self, phase: str, **values) -> None:
        if self.state is not None:
            self.state["phase"] = phase
            self.state.update(values)

    def set_reminder_time_phase(self, task_text: str) -> None:
        self.set_phase("collect_reminder_time", partial_text=str(task_text).strip())

    def has_time_reference(self, text: str) -> bool:
        # Use complete time expressions, not bare tokens such as "at" or "am";
        # those can appear in ordinary task titles and false-trigger the dialog.
        for pattern in self.config.get("time_patterns", []):
            try:
                if re.search(pattern, text, re.I):
                    return True
            except re.error:
                continue
        return False

    def time_starts_the_phrase(self, text: str) -> bool:
        value = normalise(text)
        starts = [normalise(item) for item in self.config.get("time_leading_phrases", [])]
        return any(value == item or value.startswith(item + " ") for item in starts if item)
