"""
identity.py  -  Extract explicit profile names and build a role map for the LLM.

Conversation replies stay with the LLM. Saved names are supplied as structured
role context so the model can use them naturally in the current conversation.
"""

import json
import re

_NAME = r"([A-Za-z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,2})"
# (?i:...) = case-insensitive only for the lead-in words; the name itself
# keeps its capitals so "Takrim Noor Hamim, you" does not swallow "you".
_CALL_ME = re.compile(r"(?i:\b(?:call me|you can call me))\s+" + _NAME)
_MY_NAME = re.compile(r"(?i:\bmy name(?: is|'s)|\bi am called|\bpeople call me)\s+" + _NAME)
_I_AM_NOT = re.compile(r"(?i:\bi am)\s+" + _NAME + r"\s*,?\s+not\s+" + _NAME + r"\b")
_YOUR_NAME = re.compile(
    r"(?i:\byour name(?: is|'s)|\bi(?:'ll| will| would like to| want to) call you"
    r"|\byou(?: are|'re) called|\bi name you)\s+" + _NAME)

_NICKNAME = re.compile(
    r"(?i:\b(?:call you|call u|your name is|your name's|i(?:'ll| will| want to| would like to) call you|"
    r"i(?:'ll| will) nickname you|your nickname is)\s+)" + _NAME
)

_BAD_NAMES = {"not", "a", "an", "the", "just", "so", "very", "is", "also", "still",
              "my", "your", "you", "me", "and", "but", "what", "who"}

# "choose your name", "pick a different name for you", "pick a name"
_PICK = re.compile(
    r"(?i:\b(?:pick|choose|select|decide|come up with|make up|give yourself)\b[^.?!]*\bname\b)")
_PICK_NOT_ME = re.compile(r"(?i:\bmy\s+(?:\w+\s+){0,2}?name\b)")
_WANT_NEW = re.compile(r"(?i:\b(?:different|another|new|other)\b)")

CANDIDATES = ["Milo", "Nova", "Sage", "Luna", "Pixel", "Echo", "Leo", "Maple", "Ziggy", "Olive"]


def _clean(raw):
    """Return a usable name or None."""
    if not raw:
        return None
    parts = raw.strip(" .,!?'\"").split()
    if not parts or parts[0].lower() in _BAD_NAMES:
        return None
    return " ".join(parts)


def _fix_case(name):
    return name.capitalize() if name.islower() else name


def extract_name_correction(text):
    """Return (correct_name, rejected_old_name) for explicit corrections only.

    Example: "I am Takrim, not Pixel" -> ("Takrim", "Pixel"). This is used for
    deterministic profile repair, never injected as general conversational memory.
    """
    match = _I_AM_NOT.search(str(text or ""))
    if not match:
        return None
    correct = _clean(match.group(1))
    rejected = _clean(match.group(2))
    if not correct or not rejected:
        return None
    return _fix_case(correct.split()[0]), _fix_case(rejected.split()[0])


def extract_names(text):
    """Return (user_name, None). The AI's chosen identity is immutable to user renaming."""
    user = None
    # Explicit correction pattern: "I am Takrim, not Pixel".
    correction = extract_name_correction(text)
    if correction:
        user = correction[0]
    m = _CALL_ME.search(text) if not user else None
    if m:
        user = _clean(m.group(1))
        if user:
            user = _fix_case(user.split()[0])
    if not user:
        m = _MY_NAME.search(text)
        if m:
            candidate_end = m.end()
            tail = str(text)[candidate_end:].lstrip()
            # “My name is Pixel? When did I set it up?” is a question, not consent
            # to overwrite the saved profile. Explicit assertions such as “my name is Takrim” remain valid.
            question_like = (tail.startswith("?") or bool(re.search(
                r"(?i)^.{0,50}\b(?:when did i set|did i set|is that my name|are you sure)\b", tail)))
            if not question_like:
                user = _clean(m.group(1))
                if user:
                    user = _fix_case(user.split()[0])
    return user, None


def extract_nickname(text):
    """Return a user-given nickname for the AI without changing its real name."""
    m = _NICKNAME.search(text)
    if not m:
        return None
    nickname = _clean(m.group(1))
    if not nickname:
        return None
    nickname = _fix_case(nickname.split()[0])
    if nickname.lower() in {"me", "myself", "not", "you", "your", "him", "her"}:
        return None
    return nickname


def name_line(user_name, buddy_name):
    """Render the saved names as role data rather than additional prompt rules."""
    roles = {}
    if buddy_name:
        roles["assistant"] = str(buddy_name)
    if user_name:
        roles["user"] = str(user_name)
    return "IDENTITY DATA: " + json.dumps(roles, ensure_ascii=False, separators=(",", ":")) if roles else ""


def wants_buddy_to_pick_name(text):
    """True for 'choose your name', 'pick a different name for you' (not 'my name')."""
    if not _PICK.search(text):
        return False
    about_you = re.search(r"(?i:\bfor you\b|\byour\b|\byourself\b)", text)
    return bool(about_you) or not _PICK_NOT_ME.search(text)


def pick_name(text, user_name, current, rng):
    """Pick a name for Buddy that is not the user's name, not the current one,
    and not any word the user just typed."""
    low = text.lower()
    taken = {(user_name or "").lower(), (current or "").lower()}
    options = [n for n in CANDIDATES if n.lower() not in taken and n.lower() not in low]
    return rng.choice(options or CANDIDATES)
