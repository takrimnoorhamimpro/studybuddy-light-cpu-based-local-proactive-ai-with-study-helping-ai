"""Rule-based, consent-first extraction from the user's own English messages."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import calendar, re, time
from companion.sensitive import is_sensitive, is_instruction_like
from companion.memory_quality import low_quality_reason

@dataclass
class Candidate:
    label: str
    kind: str
    evidence: str
    confidence: float
    ttl_days: int | None
    expires_ts: float | None

_MONTHS={name.lower():i for i,name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower():i for i,name in enumerate(calendar.month_abbr) if name})
_WEEKDAYS={name.lower():i for i,name in enumerate(calendar.day_name)}

def parse_date(text, now=None):
    now_dt=datetime.fromtimestamp(now if now is not None else time.time()); value=str(text or '')
    if re.search(r"\btomorrow\b",value,re.I): return (now_dt+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    m=re.search(r"\bin\s+(\d{1,2})\s+(day|days|week|weeks)\b",value,re.I)
    if m: return (now_dt+timedelta(days=int(m.group(1))*(7 if m.group(2).lower().startswith('week') else 1))).replace(hour=0,minute=0,second=0,microsecond=0)
    m=re.search(r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",value,re.I)
    if m:
        target=_WEEKDAYS[m.group(2).lower()]; delta=(target-now_dt.weekday())%7
        if m.group(1) and m.group(1).lower()=='next': delta=delta or 7
        elif delta==0 and m.group(1) and m.group(1).lower()=='this': delta=0
        elif delta==0: delta=7
        return (now_dt+timedelta(days=delta)).replace(hour=0,minute=0,second=0,microsecond=0)
    month_pat=r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    m=re.search(r"\b("+month_pat+r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",value,re.I)
    if not m:
        m=re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("+month_pat+r")(?:\s+(\d{4}))?\b",value,re.I)
        if m: day=int(m.group(1)); month=_MONTHS[m.group(2).lower()[:3]]; year=int(m.group(3) or now_dt.year)
        else: return None
    else: month=_MONTHS[m.group(1).lower()[:3]]; day=int(m.group(2)); year=int(m.group(3) or now_dt.year)
    try: result=datetime(year,month,day)
    except ValueError: return None
    if not m.group(3) and result.date()<now_dt.date():
        try: result=datetime(year+1,month,day)
        except ValueError: return None
    return result

_BLOCKED_OBJECT=re.compile(r"(?i)^(?:you|your|u|ur|buddy|study buddy|milo|assistant|chatgpt|the assistant)\b")
_DEADLINE = re.compile(r"(?i)(?P<subject>(?:my|the)\s+(?:(?:[a-z]+\s+){0,3})?(?:assignment|project|report|quiz|test|exam|presentation|submission|lab report|deadline))\s+(?:(?:is|are)\s+)?(?P<verb>due|on|by)\s+(?P<date>.+)$")

_LOW_SIGNAL_EVIDENCE=re.compile(
    r"(?i)\b(?:yeah|yep|uh|um|hmm|haha+|lol|you can chat with me|chat with me|"
    r"i(?:'m| am) free|free right now|tell (?:you|me) later|i(?:'ll| will) tell later|"
    r"long topic|not now|maybe later|focused right now|busy right now|"
    r"i(?:'m| am) (?:focused|concentrating|busy|available)|(?:studying|working) focused)\b")

def _clear_memory_evidence(evidence, *, prompted_answer=False):
    """Keep fast extraction for concise, one-idea statements; defer run-ons to review."""
    value=' '.join(str(evidence or '').split())
    words=re.findall(r"[^\W_]+(?:['’][^\W_]+)?",value,flags=re.UNICODE)
    minimum=1 if prompted_answer else 3
    maximum=12 if prompted_answer else 16
    if len(words)<minimum or len(words)>maximum or _LOW_SIGNAL_EVIDENCE.search(value):
        return False
    if len(re.findall(r"(?i)\bi\b",value))>1:
        return False
    if re.search(r"(?i)\b(?:today is|it is|it's)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",value):
        return False
    return True

def extract(user_text: str, *, now=None, safety_level='none', extra_care=False, ttl_days_by_kind=None):
    text=str(user_text or '').strip()
    if not text or len(text)>500 or len(text.split())<3 or '?' in text or safety_level!='none' or extra_care: return []
    if re.match(r"(?i)^\s*(?:if|what if|suppose|imagine|remind me|add (?:a )?task|create (?:a )?task|set (?:a )?reminder|quote|he said|she said|they said|my (?:friend|brother|sister|classmate|teacher|mother|father|colleague)\b)",text): return []
    if is_sensitive(text) or is_instruction_like(text): return []
    if re.search(r"(?i)\b(?:my friend|my brother|my sister|my classmate|my teacher|he|she|they)\s+(?:is|are|has|have|likes|loves|hates|prefers|studies|studying)",text): return []
    # Split at sentence boundaries; only a matched clause can become evidence.
    clauses=[x.strip() for x in re.split(r"(?<=[.!?])\s+|\s+[;]\s+",text) if x.strip()]
    ttl_map={'deadline':45,'goal':90,'area':45,'schedule':90,'preference':None,'interest':None}
    if isinstance(ttl_days_by_kind,dict): ttl_map.update({k:v for k,v in ttl_days_by_kind.items() if k in ttl_map})
    now_ts=float(now if now is not None else time.time())
    patterns=[
      (r"(?i)\bI(?:'m| am)\s+(studying|learning|revising|taking|preparing\s+for)\s+(.+)$",'area','area',.88),
      (_DEADLINE,'deadline',None,.96),
      (r"(?i)\bI\s+have\s+(?:an?\s+)?(quiz|test|exam)\s+(on|next|this)\s+(.+)$",'deadline','exam',.94),
      (r"(?i)\b(?:my|the)\s+(?:exam|test|quiz|assignment|deadline)\s+(?:is\s+)?(?:on|by)\s+(.+)$",'deadline','generic',.95),
      (r"(?i)\b(?:exam|quiz|deadline)\s+in\s+\d+\s+(?:days?|weeks?)\b",'deadline','generic',.93),
      (r"(?i)\bmy\s+goal\s+is\s+(.+)$",'goal','goal',.9),
      (r"(?i)\bI\s+(?:usually|normally)\s+study\s+(at|in)\s+(.+)$",'schedule','schedule',.85),
      (r"(?i)\bI\s+prefer\s+(.+)$",'preference','preference',.9),
      (r"(?i)\bI\s+(?:like|love|enjoy)\s+(.+)$",'interest','interest',.8),
      (r"(?i)\bI\s+(?:hate|dislike)\s+(.+)$",'preference','dislike',.8),
      (r"(?i)\bmy\s+favorite\s+(.+?)\s+is\s+(.+)$",'interest','favorite',.85),
    ]
    for clause in clauses:
      for pattern,kind,mode,confidence in patterns:
        m=re.search(pattern,clause)
        if not m: continue
        date=parse_date(clause,now_ts) if kind=='deadline' else None
        if kind=='deadline':
          subject=(m.group('subject') if mode is None else None)
          if subject:
            noun=re.sub(r"(?i)^(?:my|the)\s+",'',subject).strip()
            date_text=m.group('date').strip(' .,!;:')
            label=f"The user's {noun} is {m.group('verb')} {date_text}."
          elif mode=='exam':
            prep=m.group(2).lower(); date_text=m.group(3).strip(' .,!;:')
            label=f"The user has a {m.group(1)} {prep+' ' if prep in ('next','this') else 'on '}{date_text}."
          else:
            noun=re.search(r"(?i)\b(assignment|project|report|quiz|test|exam|presentation|submission|lab report|deadline)\b",clause)
            if not noun: continue
            suffix=clause.split(noun.group(1),1)[-1].strip(' .,!;:')
            label=f"The user's {noun.group(1)} {suffix}"[:120]
            if not label.endswith('.'): label+='.'
        elif mode=='area': label=f"The user is {m.group(1).lower()} {re.sub(r'(?i)\bmy\b','their',m.group(2).strip(' .,!;:'))} .".replace(' .','.')
        elif mode=='goal': label=f"The user's goal is {m.group(1).strip(' .,!;:')} .".replace(' .','.')
        elif mode=='schedule': label=f"The user usually studies {m.group(1).lower()} {m.group(2).strip(' .,!;:')} .".replace(' .','.')
        elif mode=='preference':
          obj=m.group(1).strip(' .,!;:')
          if _BLOCKED_OBJECT.match(obj) or not re.search(r"(?i)\b(answer|response|reply|short|brief|concise|detailed|tone|casual|formal|bullets|bullet points|format|examples|step by step|study habits|flashcards|quiz)\b",obj): continue
          label=f"The user prefers {obj}."
        elif mode=='interest':
          obj=m.group(1).strip(' .,!;:')
          if _BLOCKED_OBJECT.match(obj): continue
          label=f"The user enjoys {obj}."
        elif mode=='dislike':
          obj=m.group(1).strip(' .,!;:')
          if _BLOCKED_OBJECT.match(obj): continue
          label=f"The user dislikes {obj}."
        else: label=f"The user's favorite {m.group(1).strip()} is {m.group(2).strip(' .,!;:')} .".replace(' .','.')
        label=' '.join(label.split())[:120]
        evidence=' '.join(clause.split())[:160]
        if len(label.split())<4: continue
        if not _clear_memory_evidence(evidence): continue
        ttl=ttl_map.get(kind); expiry=(date.timestamp()+86400 if date else (None if kind=='deadline' else (now_ts+ttl*86400 if ttl is not None else None)))
        return [Candidate(label,kind,evidence,confidence,ttl,expiry)]
    return []


def extract_relationship_candidates(user_text: str, *, previous_prompt='', now=None):
    """Extract a few explicit, non-sensitive self-details after relationship opt-in.

    This is deliberately pattern-based: titles, assistant guesses, and implied
    biographical details are never evidence. ``previous_prompt`` only supports
    short answers such as "studying" when the companion had just asked what the
    user is doing. The caller still requires explicit memory consent.
    """
    text = str(user_text or '').strip()
    prompt = str(previous_prompt or '').strip()
    if (not text or len(text) > 500 or '?' in text or is_sensitive(text)
            or is_instruction_like(text)):
        return []
    if re.fullmatch(r"(?i)(?:yes|yeah|yep|no|nope|sure|okay|ok|skip|pass|later|busy|thanks|thank you)[.! ]*", text):
        return []
    if re.match(r"(?i)^(?:help|explain|show|find|search|add|create|delete|complete|remind|start|open|what|why|how|can you|could you)\b", text):
        return []

    now_ts = float(now if now is not None else time.time())
    out = []
    seen = set()

    def add(label, kind, evidence, ttl_days=None, *, prompted_answer=False):
        kind_hint = kind
        label = ' '.join(str(label or '').split()).strip(' .,!;:')
        evidence = ' '.join(str(evidence or '').split())[:160]
        key = label.casefold()
        if prompted_answer and low_quality_reason(label, evidence, kind_hint, "conversation"):
            return
        if (not label or key in seen or len(label.split()) < 3
                or len(label) > 180 or is_sensitive(label)
                or is_instruction_like(label)
                or not _clear_memory_evidence(evidence, prompted_answer=prompted_answer)):
            return
        seen.add(key)
        expires = now_ts + ttl_days * 86400 if ttl_days else None
        out.append(Candidate(label, kind, evidence, .88, ttl_days, expires))

    # Direct first-person statements are reliable enough to become memory once
    # the user has explicitly opted in. Keep each label short and easy to review.
    patterns = (
        (r"(?i)\bI(?:'m| am)\s+(?:a\s+)?(?:student|studying|working)\s+at\s+([^.!?;]{2,100})",
         lambda m: (f"The user's school, university, or workplace is {m.group(1).strip()}.", 'area', 180)),
        (r"(?i)\bI(?:'m| am)\s+(?:a\s+)?(student|undergraduate|graduate student|teacher|researcher)\b(?!\s+at\b)",
         lambda m: (f"The user is {m.group(1).lower()}.", 'study_work', 90)),
        (r"(?i)\bI\s+(?:study\s+at|attend|go to)\s+([^.!?;]{2,100})",
         lambda m: (f"The user's school or study place is {m.group(1).strip()}.", 'area', 180)),
        (r"(?i)\bI\s+study\s+(?!at\b)([^.!?;]{2,100})",
         lambda m: (f"The user studies {m.group(1).strip()}.", 'area', 180)),
        (r"(?i)\bI\s+work\s+(?:at|for)\s+([^.!?;]{2,100})",
         lambda m: (f"The user's workplace is {m.group(1).strip()}.", 'area', 180)),
        (r"(?i)\bI\s+work\s+as\s+(?:a\s+|an\s+)?([^.!?;]{2,80})",
         lambda m: (f"The user's work is {m.group(1).strip()}.", 'study_work', 180)),
        (r"(?i)\bmy\s+(?:school|university|college|workplace|employer)\s+is\s+(?!in\b|located\s+in\b)([^.!?;]{2,100})",
         lambda m: (f"The user's school, university, college, or workplace is {m.group(1).strip()}.", 'area', 180)),
        (r"(?i)\bmy\s+(?:school|university|college|workplace)\s+is\s+(?:located\s+)?in\s+([^.!?;]{2,80})",
         lambda m: (f"The user's school, university, college, or workplace is located in {m.group(1).strip()}.", 'location', 180)),
        (r"(?i)\bI\s+live\s+in\s+([^.!?;]{2,80})",
         lambda m: (f"The user lives in {m.group(1).strip()}.", 'location', 180)),
        (r"(?i)\bmy\s+hobb(?:y|ies)\s+(?:is|are|include)\s+([^.!?;]{2,120})",
         lambda m: (f"The user's hobbies include {m.group(1).strip()}.", 'interest', None)),
        (r"(?i)\bI\s+(?:enjoy|like|love)\s+([^.!?;]{2,100})",
         lambda m: (f"The user enjoys {m.group(1).strip()}.", 'interest', None)),
        (r"(?i)\bI\s+(?:prefer|usually prefer)\s+([^.!?;]{2,100})",
         lambda m: (f"The user prefers {m.group(1).strip()}.", 'preference', None)),
        (r"(?i)\bI\s+(?:hate|dislike)\s+([^.!?;]{2,100})",
         lambda m: (f"The user dislikes {m.group(1).strip()}.", 'preference', None)),
        (r"(?i)\bI\s+have\s+(?:a|an|one|two|three|some)\s+(friend|brother|sister|cousin|classmate|roommate)s?\b",
         lambda m: (f"The user has a {m.group(1).lower()}.", 'people', None)),
    )
    for pattern, build in patterns:
        match = re.search(pattern, text)
        if match:
            label, kind, ttl = build(match)
            add(label, kind, match.group(0), ttl)
            if len(out) >= 3:
                return out

    # Turn a short answer into a stable profile fact only when the immediately
    # preceding assistant question establishes its meaning. A passing answer to
    # "how was your day?" or "what is open?" is not a life moment; meaningful
    # episodes are selected later from a bounded idle-time conversation review.
    answer = text.strip().strip(' .,!;:')
    if prompt and not out and len(answer) <= 120 and len(answer.split()) <= 12 and not is_sensitive(answer):
        low_prompt = prompt.casefold()
        if re.search(r"\b(?:what are you doing these days|what are you up to (?:these days|lately)|studying or working|what do you do for work|what do you do for a living)\b", low_prompt):
            if re.fullmatch(r"(?i)(?:a student|both|studying and working)", answer):
                label = ("The user is studying and working." if answer.casefold() in ("both", "studying and working")
                         else "The user is " + answer.lower() + ".")
                add(label, 'study_work', answer, 90, prompted_answer=True)
        elif re.search(r"\b(?:what are you studying|what do you study|what subject|what are you working on)\b", low_prompt):
            add(f"The user is studying {answer}.", 'area', answer, 180, prompted_answer=True)
        elif re.search(r"\b(?:which|what)\s+(?:school|university|college|workplace)|\bwhere do you (?:study|work)", low_prompt):
            add(f"The user's school, university, college, or workplace is {answer}.", 'area', answer, 180, prompted_answer=True)
        elif re.search(r"\b(?:where.*located|what city|where do you live)\b", low_prompt):
            add(f"The user is based in {answer}.", 'location', answer, 180, prompted_answer=True)
        elif re.search(r"\b(?:hobb|interest|enjoy|free time|outside (?:study|work)|like doing)\w*\b", low_prompt):
            add(f"The user enjoys {answer}.", 'interest', answer, prompted_answer=True)
        elif re.search(r"\b(?:study help|study support|how.*help.*work|prefer.*help)\b", low_prompt):
            add(f"The user prefers this kind of study support: {answer}.", 'preference', answer, prompted_answer=True)
        elif re.search(r"\b(?:family|friends|people.*important)\b", low_prompt):
            add(f"People important to the user: {answer}.", 'people', answer, prompted_answer=True)
        # Deliberately do not turn conversational filler into episode memories.
        # A selected life moment needs context and explicit significance, which
        # the background review evaluates across the recent conversation.

    return out[:3]
