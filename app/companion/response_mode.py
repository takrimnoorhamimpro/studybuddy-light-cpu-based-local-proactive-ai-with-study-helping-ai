"""Deterministic response-length selection."""
import re
_CONTENT_NOUN=r"\b(?:router|network|physics|math|mathematics|history|science|computer|programming|algorithm|database|gravity|photosynthesis|economics|grammar|language|concept|theory|equation|function|system|process|difference|cause|effect|example|topic|term|method|technology|essay|assignment|exam|study|learning|meaning|definition)\b"
RESPONSE_EFFORT_BUDGETS = {
    "Low": 20,
    "Medium": 40,
    "High": 80,
    "Extra": 144,
    "Max": 256,
    "Insane": 512,
}

def _has_unfinished_assistant_turn(history):
    if not history:
        return False
    previous = next((str(turn.get("content") or "").strip()
                     for turn in reversed(history)
                     if turn.get("role") == "assistant"), "")
    if not previous:
        return False
    # A short new turn after a cut-off sentence/list is usually a continuation,
    # so preserve enough generation budget to finish the thought.
    return bool(
        (len(previous) >= 80 and not re.search(r"[.!?…][\"'”’)]*$", previous))
        or re.search(r"(?:[:,;–—-]|\b(?:and|or|but|because|to|with|for))\s*$", previous, re.I)
        or re.search(r"(?:^|\n)\s*\d+[.)]?\s*$", previous)
    )

def choose_mode(user_text, history_hint=None):
    text=str(user_text or '').strip(); words=text.split()
    if len(words) <= 8 and _has_unfinished_assistant_turn(history_hint): return 'deep'
    if (len(words)<=4 and not re.match(r"(?i)^(what|how|who|when|where|why)\b",text) and "?" not in text) or re.fullmatch(r"(?i)(hi|hello|hey|thanks|thank you|yes|no|okay|ok|sure|yep|nope)[.! ]*",text): return 'quick'
    deep_cue=bool(re.search(r"(?i)\b(explain|walk me through|why|how does|compare|in detail|step by step)\b",text))
    genuine_question='?' in text or bool(re.match(r"(?i)^(why|how|what|when|where|who|compare|explain|walk me through)\b",text))
    if deep_cue and genuine_question: return 'deep'
    if re.match(r"(?i)^(what|how|who|when|where|why)\b",text) and re.search(_CONTENT_NOUN,text): return 'chat'
    return 'chat'
