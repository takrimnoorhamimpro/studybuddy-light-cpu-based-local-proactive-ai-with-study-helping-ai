"""Consent-gated, user-led companion conversation state."""
from __future__ import annotations

import re
import time

from companion.sensitive import is_sensitive


class RelationshipBuilder:
    """Manage consent and short, natural getting-to-know-you conversations.

    The model follows the user's story. This class gates memory consent and
    personal-topic boundaries without imposing an availability interview or a
    forced closing message.
    """

    SESSION_TURNS = 8
    def __init__(self, memory, companion, *, clock=time.time):
        self.memory = memory
        self.companion = companion
        self.clock = clock
        self.phase = None
        self.session_turns = 0
        self.deferred_this_session = False
        self.discard_user_turn = False

    @staticmethod
    def _yes(text):
        value = str(text or "").strip().replace("’", "'")
        if re.search(r"(?i)\b(?:busy|not now|no thanks|not free|not available)\b", value):
            return False
        return bool(re.fullmatch(
            r"\s*(?:yes|yeah|yep|yup|yes please|sure|absolutely|okay|ok|"
            r"i agree|i’d like that|i'd like that|that sounds good|go ahead|you can)\s*[.!]?\s*",
            value, re.I))

    @staticmethod
    def _busy(text):
        value = str(text or "").strip().replace("’", "'")
        return bool(re.fullmatch(
            r"\s*(?:no|no thanks|no thank you|nope|nah|not now|skip|later|"
            r"i'?m busy|i am busy|busy(?: right now)?|focused(?: right now| for now)?|concentrating(?: right now| for now)?|maybe later|not at the moment|"
            r"i'?m working|i am working|i'?m (?:still )?focused(?:\s+(?:right now|at the moment|for now))?(?:\s+(?:yeah|yes|though|right))?|"
            r"i am (?:still )?focused(?:\s+(?:right now|at the moment|for now))?(?:\s+(?:yeah|yes|though|right))?|focused right now|"
            r"i'?m concentrating|i am concentrating|in a meeting|in class|in a lecture|"
            r"not available|not free|can't talk|cannot talk|can't chat|cannot chat)\s*[.!]?\s*",
            value, re.I)
            or re.search(r"(?i)\b(?:i'?m busy|i am busy|busy right now|not free|not available|"
                         r"i can['’]?t talk|i cannot talk|i can['’]?t chat|i cannot chat|caught up right now|"
                         r"i'?m focused(?:\s+(?:right now|at the moment|for now)|\s+on (?:this|that|it|my work|work|studying|the video))?(?:\s+(?:yeah|yes|though|right))?|"
                         r"i am focused(?:\s+(?:right now|at the moment|for now)|\s+on (?:this|that|it|my work|work|studying|the video))?(?:\s+(?:yeah|yes|though|right))?|"
                         r"focused right now|i'?m concentrating(?:\s+(?:right now|at the moment|for now))|"
                         r"i am concentrating(?:\s+(?:right now|at the moment|for now))|"
                         r"in a meeting|in class(?: right now)?|in a lecture|maybe later|not now|no thanks|"
                         r"i do not want to (?:talk|chat|share)|i don't want to (?:talk|chat|share)|"
                         r"not interested|not a good time|stop asking|don't ask me|do not ask me|"
                         r"please don['’]?t disturb me|do not disturb me|leave me to it)\b", value))

    def cancel(self):
        self.phase = None
        self.session_turns = 0

    def _consent_enabled(self):
        return self.companion.setting("relationship_memory_consent", "") == "yes"

    def _facts_enabled(self):
        return str(self.companion.setting("tier_facts", "1")).lower() not in ("0", "false", "off", "no")

    def start(self, buddy_name="Buddy", user_name=None, *, restart=False, new_session=False):
        if restart:
            self.companion.set_setting("relationship_opt_out", "0")
        if self.companion.setting("relationship_opt_out", "0") == "1" or not self._facts_enabled():
            return None
        if new_session:
            self.deferred_this_session = False

        if not self._consent_enabled():
            self.phase = "consent"
            if self.companion.setting("relationship_intro_shown", "0") != "1":
                self.companion.set_setting("relationship_intro_shown", "1")
                return (f"Hi! I’m {buddy_name}, your local AI study buddy. I’d like us to get to know each other "
                        "naturally, and I can remember non-sensitive things you choose to share on this PC. "
                        "You can review or forget them anytime. Is that okay? Say yes to opt in, no or skip to decline, "
                        "or type anything else to chat normally.")
            return ("Before I use personal details to get to know you, is it okay if I remember non-sensitive "
                    "things you choose to share on this PC? Say yes to opt in, no or skip to decline, "
                    "or type anything else to chat normally.")

        self.phase = "conversation"
        self.session_turns = 0
        return self._day_checkin(user_name)

    def _day_checkin(self, user_name=None):
        """Build the short, deterministic English day check-in."""
        if not user_name:
            try:
                user_name = self.memory.get_profile("user_name")
            except Exception:
                user_name = None
        name = str(user_name or "").strip()
        address = " " + name if name else ""
        return f"Hey{address}, how's your day going?"

    def respond(self, text):
        """Consume consent and personal-topic boundaries; chat stays in ChatCore."""
        value = str(text or "").strip().replace("’", "'")
        self.discard_user_turn = False
        if not self.phase:
            return None

        if self.phase == "consent":
            self.phase = None
            self.companion.set_setting("relationship_intro_shown", "1")
            if self._yes(value):
                self.companion.set_setting("relationship_memory_consent", "yes")
                self.companion.set_setting("relationship_opt_out", "0")
                self.companion.add_consent_ledger("relationship_memory", True, "relationship_builder_opt_in")
                self._confirm_existing_candidates()
                self.phase = "conversation"
                self.session_turns = 0
                return None
            if self._busy(value):
                self.companion.set_setting("relationship_opt_out", "1")
                self.companion.add_consent_ledger("relationship_memory", False, "relationship_builder_declined")
                return "No problem. I won’t start getting-to-know-you prompts or save details from them. We can just chat."
            # A non-answer (including a question about the feature) is handled by normal chat.
            self.deferred_this_session = True
            return None

        if self.phase == "topic_permission":
            self.phase = None
            if self._yes(value):
                self.phase = "conversation"
                self.session_turns = 0
                return None
            if self._busy(value):
                self.deferred_this_session = True
            if self._busy(value) and not re.search(
                    r"(?i)\b(?:help|explain|solve|show|write|review|plan|search|find|tell me|can you|could you|please)\b",
                    value):
                return "No problem—I’ll skip the personal question. We can talk about anything else."
            # Ordinary text falls through to the normal assistant response pipeline.
            return None

        if self.phase != "conversation":
            return None
        if re.fullmatch(r"\s*(?:stop asking|stop these questions|pause getting to know me|leave me alone)\s*[.!]?\s*", value, re.I):
            self.companion.set_setting("relationship_opt_out", "1")
            self.cancel()
            return "Understood. I’ll stop the getting-to-know-you prompts. You can restart them from My World whenever you want."
        # A defer/skip during the personal conversation is a boundary, not an
        # invitation for the model to fill the silence with another personal
        # question (for example, changing from a childhood topic to the user's day).
        defer = bool(
            self._busy(value)
            or re.search(
                r"(?i)\b(?:i(?:'ll| will) (?:talk|tell you|share|say more|come back to|answer) .*? later|"
                r"i(?:'d| would) rather (?:talk|answer|share) later|"
                r"let(?:'s| us) (?:talk|discuss|come back to) (?:this|that|it) later|"
                r"i(?:'m| am) not ready to (?:talk|answer|share)|"
                r"maybe another time|some other time|not right now)\b", value))
        if defer:
            self.deferred_this_session = True
            self.cancel()
            # Leave substantive questions or requests to normal chat while still
            # ending the personal-topic thread.
            if re.search(
                    r"(?i)\b(?:help|explain|solve|show|write|review|plan|search|find|tell me|can you|could you|please|what|how|why)\b",
                    value):
                return None
            return "Of course—we can come back to that whenever you feel like it. No need to answer now."
        return None

    def _confirm_existing_candidates(self):
        """Promote prior explicit, ordinary conversation facts after broad opt-in."""
        try:
            for item in self.companion.list_nodes(include_candidates=True):
                if (item.get("status") == "candidate" and item.get("source") == "conversation"
                        and str(item.get("sensitivity") or "normal") == "normal"
                        and not is_sensitive(item.get("label", ""))):
                    self.companion.confirm_fact(item["id"])
        except Exception:
            # Consent and chat must remain usable if a legacy database lacks a queue.
            pass

    def begin_user_turn(self):
        """Advance an opted-in chat without imposing an artificial turn limit."""
        if self.phase != "conversation":
            return False
        self.session_turns += 1
        return True

