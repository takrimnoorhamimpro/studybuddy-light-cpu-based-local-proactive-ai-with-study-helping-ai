"""Quality gate for My World memories.

Older versions saved raw chat replies ("The user enjoys yeah you can chat with me i am free")
as interests. This module recognises such low-value labels so they can be cleaned up and kept
out of the prompt. Only conversation-sourced nodes are judged; anything the user typed by hand
or answered during onboarding is never touched.
"""
from __future__ import annotations

import re
import time

_FILLER = re.compile(
    r"(?i)\b(?:yeah|yep|yup|haha+|lol|um+|uh+|hmm+|actually|you can chat|chat with me|i(?:'m| am) free|"
    r"tell (?:you|me) later|so long|long topic|not now|maybe later|today is (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"these days|right now)\b")
_STATUS_WORDS = {"working", "focused", "busy", "free", "fine", "good", "ok", "okay", "tired", "bored", "available",
                 "nothing", "idk", "same", "normal", "great", "well", "studying"}
_PREFIX = re.compile(
    r"(?i)^the user(?:'s)?\s+(?:enjoys|likes|loves|dislikes|prefers|is studying|is working on|is based in|is|current role is|"
    r"goal is(?:\s+to)?|usually studies|has an?|has)\s+")
_FIRST_PERSON = re.compile(r"(?i)\b(?:i|i'm|i'll|my|me)\b")


def label_body(label):
    """The part of a label after the 'The user …' prefix."""
    value = " ".join(str(label or "").split()).strip(" .")
    body = _PREFIX.sub("", value, count=1)
    body = re.sub(r"(?i)^the user's\s+", "", body)        # "The user's exam is due Friday" -> "exam is due Friday"
    return body.strip(" .")


def low_quality_reason(label, evidence="", kind="", source="conversation"):
    """Return a short reason string when a memory is junk, otherwise ''. Safe: never raises."""
    try:
        if str(source or "conversation") != "conversation":
            return ""                                   # onboarding / manual entries are always trusted
        body = label_body(label)
        words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", body)
        evidence_words = re.findall(r"[^\W_]+", str(evidence or ""))
        if not words:
            return "empty"
        if len(words) == 1 and words[0].casefold() in _STATUS_WORDS:
            return "bare status word"
        if len(words) <= 2 and words[0].casefold() in _STATUS_WORDS and kind in ("area", "study_work", "interest"):
            return "bare status word"
        if _FILLER.search(body) or _FILLER.search(str(evidence or "")):
            return "chat filler"
        if _FIRST_PERSON.search(body):
            return "raw sentence (first person left in)"
        if kind == "interest" and (len(words) > 10 or len(evidence_words) > 14):
            return "run-on sentence saved as an interest"
        return ""
    except Exception:
        return ""


def find_low_quality(personal_companion):
    """List [(node_id, label, reason)] for stored conversation memories that look like junk."""
    found = []
    for node in personal_companion.list_nodes(include_candidates=True):
        reason = low_quality_reason(node.get("label"), node.get("evidence"), node.get("kind"),
                                    node.get("source") or "conversation")
        if reason and str(node.get("status") or "") in ("confirmed", "candidate"):
            found.append((str(node["id"]), str(node.get("label") or ""), reason))
    return found


def clean_up(personal_companion, node_ids):
    """Expire the given nodes (kept in the DB, never used in replies). Returns how many changed."""
    ids = [str(x) for x in node_ids or []]
    if not ids:
        return 0
    memory = personal_companion.memory
    marks = ",".join("?" for _ in ids)
    with memory._db_lock:
        cur = memory.db.execute(
            f"UPDATE personal_nodes SET status='expired',consented=0,updated_ts=? "
            f"WHERE id IN ({marks}) AND source='conversation'", [time.time()] + ids)
        memory.db.execute(f"DELETE FROM candidate_queue WHERE node_id IN ({marks})", ids)
        memory.db.commit()
        return cur.rowcount
