"""Conservative deterministic rejection for sensitive personal-memory candidates."""
import re

_PATTERNS = [
    r"\b(?:diagnos(?:is|ed)|depress(?:ion|ed)|anxiety|panic attack|bipolar|adhd|autis(?:m|tic)|therapy|therapist|medical condition|illness|disease|symptom|pregnan(?:t|cy)|disability|mental health|blood pressure|diabetes|asthma|cancer)\b",
    r"\b(?:medication|medicine|prescription|dose|mg|milligrams|antidepressant|insulin)\b",
    r"\b(?:religion|religious|christian|muslim|islam|hindu|buddhist|atheist|church|mosque|temple|prayer)\b",
    r"\b(?:political party|politics|election|vote for|conservative|liberal|socialist|communist)\b",
    r"\b(?:sexual orientation|gay|lesbian|bisexual|transgender|sexual preference|sexually)\b",
    r"\b(?:\$\s*\d|\b(?:usd|eur|gbp|myr)\s*\d|income|salary|debt|owe \$|bank account|credit card)\b",
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:dollars?|euros?|pounds|ringgit|myr|usd|eur|gbp)\b",
    r"\b(?:password|passcode|secret key|api key|token is|social security|passport number|id number|card number)\b",
    r"\b(?:i\s+live\s+(?:at|in)|my\s+(?:home\s+)?address\s+is)\s+\d+\s+[^,;.!?]{1,100}",
    r"\b\d{1,5}\s+[\w.-]+\s+(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|jalan|boulevard|blvd\.?|court|ct\.?|highway|hwy\.?|apartment|apt\.?|unit|suite|postcode|postal\s+code)\b",
    r"\b(?:jalan|street|road|avenue|lane|apartment|apt\.?|unit|suite|postcode|postal\s+code)\s+[\w#-]+",
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
    r"\b(?:call|text|phone) me at \+?\d[\d ()-]{7,}\b",
    r"\b\+?\d[\d ()-]{7,}\d\b",
    r"\b(?:my|our)\s+(?:(?:\d{1,2}[- ]year[- ]old)\s+)?(?:child|son|daughter|kid|minor|teenager)\b",
    r"\b(?:my|our)\s+\d{1,2}[- ]year[- ]old\b",
    r"\b(?:my|home) address is\b",
    r"\b(?:student id|passport|driver'?s license|driver'?s licence|credit card)\s*(?:number|no\.?|#)?\s*[:#-]?\s*[A-Z0-9-]{5,}\b",
]
_COMPILED=[re.compile(p,re.I) for p in _PATTERNS]

_INSTRUCTION = re.compile(r"(?i)\b(?:system\s*:|assistant\s*:|ignore|disregard|override|jailbreak|prompt|instructions?|you must|you should never|always say|never mention|do not follow|pretend)\b")
_INSTRUCTION_HINT = re.compile(r"(?i)\b(?:system|assistant|ignore|disregard|override|jailbreak|prompt|instruction|must|pretend)\b|always\s+say|never\s+mention|do\s+not\s+follow")

def is_instruction_like(text: str) -> bool:
    value=str(text or "")
    if not _INSTRUCTION_HINT.search(value): return False
    return bool(_INSTRUCTION.search(value))

def is_sensitive(text: str) -> bool:
    value=str(text or "")
    return any(p.search(value) for p in _COMPILED)
