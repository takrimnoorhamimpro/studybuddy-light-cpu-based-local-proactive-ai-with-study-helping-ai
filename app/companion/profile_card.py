"""Profile Card: a compact, stable summary of My World for the system prompt.

Instead of searching memories word-by-word on every message (which misses most of them), the model
gets one short card (~80-120 tokens) with the user's confirmed studies, goals, deadlines, interests
and preferences. It only changes when My World changes, so the prompt prefix stays stable.
Only confirmed + consented + non-sensitive + non-expired + good-quality details are used.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from companion.memory_quality import label_body, low_quality_reason

# (heading, kinds, max items) in display order; PRIORITY decides what is dropped first when tight.
SECTIONS = (
    ("Studies/work", ("area", "study_work"), 2),
    ("Goals", ("goal",), 3),
    ("Upcoming", ("deadline",), 2),
    ("Routine", ("routine", "schedule"), 2),
    ("Interests", ("interest",), 4),
    ("Prefers", ("preference", "support_style"), 3),
    ("Values", ("value",), 2),
    ("People", ("people",), 2),
)
_DROP_ORDER = ("Values", "People", "Routine", "Interests", "Goals", "Upcoming", "Prefers", "Studies/work")
_HEADER = "The user chose to share these details (use them naturally only when relevant, never recite them):"


@dataclass
class CardResult:
    text: str
    node_ids: list
    est_tokens: int


def _tokens(text):
    return int(math.ceil(len(str(text or "")) / 3.6))


class ProfileCard:
    def __init__(self, memory, personal_companion, clock=time.time):
        self.memory, self.pc, self.clock = memory, personal_companion, clock
        self._cache_key = None
        self._cache = CardResult("", [], 0)

    def _enabled(self):
        value = self.pc.setting("tier_facts", "1")
        return str(value).strip().lower() not in {"0", "false", "off", "no"}

    def _rows(self, now):
        with self.memory._db_lock:
            return self.memory.db.execute(
                "SELECT id,label,kind,evidence,source,expires_ts,updated_ts,importance FROM personal_nodes "
                "WHERE status='confirmed' AND consented=1 AND COALESCE(sensitivity,'normal')='normal' "
                "ORDER BY importance DESC, updated_ts DESC").fetchall()

    def build(self, max_tokens=120):
        if not self.memory or not self._enabled():
            return CardResult("", [], 0)
        now = self.clock()
        try:
            rows = self._rows(now)
        except Exception:
            return CardResult("", [], 0)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        key = (day, int(max_tokens), tuple((r["id"], r["updated_ts"], r["label"]) for r in rows))
        if key == self._cache_key:
            return self._cache
        picked = {}
        for row in rows:
            kind = str(row["kind"] or "")
            if row["expires_ts"] is not None and float(row["expires_ts"]) <= now:
                continue
            if low_quality_reason(row["label"], row["evidence"], kind, row["source"]):
                continue
            body = label_body(row["label"])
            words = body.split()
            if not words or len(words) > 14:
                continue
            if kind == "deadline" and row["expires_ts"] is not None:
                days = int(math.ceil((float(row["expires_ts"]) - 86400 - now) / 86400))
                if days >= 0:
                    body += " (today)" if days == 0 else " (in %d day%s)" % (days, "" if days == 1 else "s")
            for heading, kinds, limit in SECTIONS:
                if kind in kinds:
                    bucket = picked.setdefault(heading, [])
                    if len(bucket) < limit and body.casefold() not in [b[0].casefold() for b in bucket]:
                        bucket.append((body, str(row["id"])))
                    break
        text, ids = self._render(picked, max_tokens)
        self._cache_key, self._cache = key, CardResult(text, ids, _tokens(text))
        return self._cache

    @staticmethod
    def _render(picked, max_tokens):
        sections = dict(picked)
        studies = sections.get("Studies/work")
        if studies and len(studies) > 1:      # drop a bare "studying"/"working" role when a real field is known
            kept = [b for b in studies if b[0].casefold() not in ("studying", "working", "a student")]
            if kept: sections["Studies/work"] = kept
        while sections:
            lines = [_HEADER] + ["%s: %s." % (h, "; ".join(b[0] for b in sections[h]))
                                 for h, _k, _l in SECTIONS if h in sections]
            text = "\n".join(lines)
            if _tokens(text) <= max_tokens:
                ids = [b[1] for h in sections for b in sections[h]]
                return text, ids
            for heading in _DROP_ORDER:          # shed the least important section first
                if heading in sections:
                    del sections[heading]
                    break
        return "", []
