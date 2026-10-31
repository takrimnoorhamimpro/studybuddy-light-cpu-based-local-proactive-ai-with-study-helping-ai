"""
safety.py  -  The FIXED safety layer for Study Buddy.

Simple idea: this is like a smoke alarm. It is normal code, not AI.
Training can never change it, and the owner cannot edit the rules.
Only contacts.json (trusted helpline / counsellor list) is editable.

Main functions:
    load_safety(config_dir)           -> Safety object
    safety.check_input(text)          -> "high" | "medium" | "none"
    safety.build_input_reply(...)     -> fixed warm reply text
    safety.check_output(text)         -> (is_safe, reason)
    safety.safe_output(text)          -> text or the fallback reply
    safety.check_language(text)       -> (ok, message)
"""

import json
import os
import re
import unicodedata

# Fixed message for non-English input (language rule, decision #27)
LANGUAGE_REFUSAL = (
    "Sorry, I can only chat in English for now. "
    "Could you please write that again in English?"
)


def _normalize(text):
    """Make text easy to match: lowercase, straight apostrophes, single spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


_UNSAFE_REMINDER_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:drink|ingest|swallow|consume|eat|take)\b.{0,48}\b(?:blood|bleach|poison|toxic chemicals?|cleaning fluid)\b",
    r"\b(?:blood|bleach|poison|toxic chemicals?|cleaning fluid)\b.{0,48}\b(?:drink|ingest|swallow|consume|eat|take)\b",
))

def unsafe_reminder_content(text):
    """Guard against reminders that directly encourage ingestion of hazardous material.

    This is deliberately narrow: it does not block benign phrases such as "donate blood"
    or ordinary discussion. Call only for content that will be scheduled as a reminder.
    """
    value = str(text or "")
    return any(pattern.search(value) for pattern in _UNSAFE_REMINDER_PATTERNS)


class Safety:
    def __init__(self, rules, contacts):
        self.rules = rules
        self.contacts = contacts or {}
        self._high = [re.compile(p, re.IGNORECASE) for p in rules["input_high"]]
        self._medium = [re.compile(p, re.IGNORECASE) for p in rules["input_medium"]]
        self._out = [re.compile(p, re.IGNORECASE) for p in rules["output_block"]]

    # ---------- fixed text for the model prompt ----------
    def prompt_core(self):
        """Fixed safety text that goes at the TOP of every model prompt."""
        return self.rules["prompt_core"] + "\n" + self.rules["prompt_language"]

    # ---------- language rule ----------
    def check_language(self, text):
        """English only. Any letter from a non-Latin script means refuse."""
        for ch in text:
            if ch.isalpha():
                name = unicodedata.name(ch, "")
                if not name.startswith("LATIN"):
                    return False, LANGUAGE_REFUSAL
        return True, ""

    # ---------- input check ----------
    def check_input(self, text):
        """Returns 'high', 'medium' or 'none'. High wins over medium."""
        t = _normalize(text)
        for rx in self._high:
            if rx.search(t):
                return "high"
        for rx in self._medium:
            if rx.search(t):
                return "medium"
        return "none"

    def _helpline_lines(self):
        """Build helpline text ONLY from numbers the owner filled and verified."""
        lines = []
        c = self.contacts
        counsellor = c.get("university_counsellor", {})
        if counsellor.get("phone") or counsellor.get("email"):
            bits = [counsellor.get("name", "University counsellor")]
            if counsellor.get("phone"):
                bits.append(counsellor["phone"])
            if counsellor.get("email"):
                bits.append(counsellor["email"])
            lines.append("- " + " | ".join(bits))
        for item in c.get("emergency_and_helplines", []):
            number = str(item.get("number", "")).strip()
            if number:
                lines.append("- " + item.get("name", "Helpline") + ": " + number)
        return lines

    def build_input_reply(self, level, trusted_names=None):
        """Fixed warm reply. trusted_names comes from memory (can be empty)."""
        if level == "none":
            return ""
        r = self.rules
        parts = [r["reply_high_opening"] if level == "high" else r["reply_medium_opening"]]

        if trusted_names:
            names = ", ".join(trusted_names[:3])
            parts.append(r["people_line_with_names"].format(names=names))
        else:
            parts.append(r["people_line_ask"])

        if level == "high":
            parts.append(r["pro_line"])
            lines = self._helpline_lines()
            if lines:
                parts.append(r["helpline_intro"] + "\n" + "\n".join(lines))
        return "\n\n".join(parts)

    # ---------- output check ----------
    def check_output(self, text):
        """Returns (is_safe, reason). Blocks replies that encourage harm etc."""
        t = _normalize(text)
        for rx in self._out:
            if rx.search(t):
                return False, rx.pattern
        return True, ""

    def safe_output(self, text):
        ok, _ = self.check_output(text)
        return text if ok else self.rules["output_fallback"]


def load_safety(config_dir):
    """Load safety.json (fixed) and contacts.json (editable)."""
    with open(os.path.join(config_dir, "safety.json"), "r", encoding="utf-8") as f:
        rules = json.load(f)
    contacts = {}
    contacts_path = os.path.join(config_dir, "contacts.json")
    if os.path.exists(contacts_path):
        with open(contacts_path, "r", encoding="utf-8") as f:
            contacts = json.load(f)
    return Safety(rules, contacts)
