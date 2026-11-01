"""Deterministic, consent-gated retrieval of personal companion facts."""
from __future__ import annotations
from companion.sensitive import is_instruction_like
from dataclasses import dataclass
import math, re, time, logging

_STOP = set("a an the and or but if then than so to of in on at for from with by as is am are was were be been being do does did have has had i me my mine we our you your he she they them it this that these those what which who when where why how can could would should will just very about into over under after before not no yes please tell explain help make get use using study studying learn learning thing things user plan planning organize organise".split())
_WORD = re.compile(r"[a-z]+")
_FIRST_PERSON = re.compile(r"\b(?:i|me|my|mine|we|our|ours)\b", re.I)

@dataclass
class PersonaContext:
    text: str
    node_ids: list[str]
    est_tokens: int
    truncated: bool


def estimate_tokens(text: str) -> int:
    return int(math.ceil(len(str(text or "")) / 3.6))


def _stem(word: str) -> str:
    w = word.lower()
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            w = w[:-len(suffix)]
            if suffix == "ing" and len(w) > 2 and w[-1] == w[-2]: w = w[:-1]
            break
    return w


def _tokens(text):
    return {_stem(w) for w in _WORD.findall(str(text or "").lower()) if w not in _STOP and len(w) > 2}


def _neutral(label: str) -> str:
    value = str(label or "").strip()[:120]
    value = re.sub(r"(?i)^my\s+", "The user's ", value)
    value = re.sub(r"(?i)^i am\s+", "The user is ", value)
    value = re.sub(r"(?i)^i'm\s+", "The user is ", value)
    value = re.sub(r"(?i)^i prefer\s+", "The user prefers ", value)
    value = re.sub(r"(?i)^i like\s+", "The user likes ", value)
    value = re.sub(r"(?i)^i love\s+", "The user enjoys ", value)
    value = re.sub(r"(?i)^i enjoy\s+", "The user enjoys ", value)
    value = re.sub(r"(?i)^i hate\s+", "The user dislikes ", value)
    value = re.sub(r"(?i)^i dislike\s+", "The user dislikes ", value)
    value = _FIRST_PERSON.sub("the user", value)
    return value


class UserModel:
    def __init__(self, memory, personal_companion, config, clock=time.time):
        self.memory, self.personal_companion = memory, personal_companion
        self.config = config or {}
        self.clock = clock

    def _facts_enabled(self):
        value = self.personal_companion.setting("tier_facts", "1")
        return str(value).strip().lower() not in {"0", "false", "off", "no"}

    def persona_context(self, user_text: str, *, budget_tokens: int = 200, max_items: int = 4, exclude_ids=None) -> PersonaContext:
        if not self.memory or not self._facts_enabled():
            return PersonaContext("", [], 0, False)
        query = _tokens(user_text)
        if re.search(r"(?i)\b(plan|planning|schedule|organize|organise)\b", str(user_text or "")) and re.search(r"(?i)\b(week|day|study|work|time)\b", str(user_text or "")):
            # Planning requests should consider active deadlines/goals even when the wording differs.
            query.discard("study")
            query.update({"exam", "deadline", "goal", "schedule"})
        if not query:
            return PersonaContext("", [], 0, False)
        planning_request = bool(re.search(r"(?i)\b(plan|planning|schedule|organize|organise)\b", str(user_text or "")) and re.search(r"(?i)\b(week|day|study|work|time)\b", str(user_text or "")))
        now = self.clock()
        try:
            # Push a conservative lexical prefilter into SQLite. It retains all
            # always-on preferences and planning-relevant node kinds, while avoiding
            # Python tokenization of hundreds of unrelated labels on each turn.
            conditions=["kind='preference'"]
            if planning_request:
                conditions.append("kind IN ('deadline','schedule') OR (kind='goal' AND (label LIKE '%goal%' OR label LIKE '%aim%' OR label LIKE '%want%' OR label LIKE '%target%' OR label LIKE '%finish%' OR label LIKE '%complete%' OR label LIKE '%learn%'))")
            terms=sorted(query)
            conditions.extend(["label LIKE ?" for _ in terms])
            sql=("SELECT id,label,kind,expires_ts,updated_ts,importance,source,evidence FROM personal_nodes "
                 "WHERE status='confirmed' AND consented=1 AND COALESCE(sensitivity,'normal')='normal' AND ("
                 + " OR ".join(conditions) + ")")
            params=[f"%{term}%" for term in terms]
            with self.memory._db_lock:
                rows = self.memory.db.execute(sql,params).fetchall()
        except Exception:
            return PersonaContext("", [], 0, False)
        eligible=[]
        preferences=[]
        min_overlap=float(self.config.get("relevance_min_overlap", 0.12) or 0.12)
        excluded={str(x) for x in (exclude_ids or [])}
        from companion.memory_quality import low_quality_reason
        for row in rows:
            if str(row["id"]) in excluded: continue      # already shown in the Profile Card
            if low_quality_reason(row["label"], row["evidence"], row["kind"], row["source"]): continue
            expiry=row["expires_ts"]
            if expiry is not None and float(expiry) <= now: continue
            label=str(row["label"] or "")[:120]
            if not label or is_instruction_like(label): continue
            kind=str(row["kind"] or "")
            # The preferred name is owned by identity/profile, not duplicated as persona context.
            if kind == "profile": continue
            overlap_tokens=_tokens(label)
            overlap=(len(query & overlap_tokens) / max(1, len(query | overlap_tokens)))
            if planning_request and (kind in ("deadline", "schedule") or (kind=="goal" and re.search(r"(?i)\b(goal|aim|want|target|finish|complete|learn)\b",label)) or re.search(r"(?i)\b(exam|test|quiz|assignment|deadline)\b", label)):
                # A planning question is relevant to active deadlines and explicit goals even without shared nouns.
                overlap = max(overlap, min_overlap + 0.01)
            if overlap > 0:
                # Shared stems are already represented by _tokens/_stem.
                recency=math.exp(-max(0.0, now-float(row["updated_ts"] or now))/(90*86400))
                importance=max(0.0,min(1.0,float(row["importance"] or 0.5)))
                score=0.6*overlap+0.2*recency+0.2*importance
                if overlap >= min_overlap:
                    eligible.append((score, row, label))
            if kind == "preference" and re.search(r"(?i)\b(answer|response|reply|short|brief|concise|detailed|tone|casual|formal|bullets|bullet points|format|examples|step by step|study habits|flashcards|quiz|dislikes|encouragement|help)\b", label): preferences.append((float(row["importance"] or 0.5), row, label))
        eligible.sort(key=lambda x:(-x[0], str(x[1]["id"])))
        pref_cap=max(0,min(2,int(self.config.get("always_on_preference_items",2))))
        pref_ids={x[1]["id"] for x in sorted(preferences,key=lambda x:(-x[0],str(x[1]["id"])))[:pref_cap]}
        picked=[]; seen=set()
        for item in sorted(eligible, key=lambda x:(0 if x[1]["id"] in pref_ids else 1, -x[0], str(x[1]["id"]))):
            if item[1]["id"] not in seen:
                picked.append(item); seen.add(item[1]["id"])
        # Always-on preferences may be injected without lexical overlap, capped at two.
        for _,row,label in sorted(preferences,key=lambda x:(-x[0],str(x[1]["id"])))[:pref_cap]:
            if row["id"] not in seen:
                picked.append((0.0,row,label)); seen.add(row["id"])
        max_items=max(0,int(max_items)); budget_tokens=max(0,int(budget_tokens))
        lines=[]; ids=[]; truncated=False
        header="Things the user chose to tell you (use a detail only if it helps this reply; never list them or mention memory):"
        used=estimate_tokens(header)
        for _,row,label in picked:
            neutral=_neutral(label)
            line="- "+neutral
            cost=estimate_tokens(line)+1
            if len(lines)>=max_items or used+cost>budget_tokens:
                truncated=True; continue
            lines.append(line); ids.append(str(row["id"])); used+=cost
        if not lines:
            if truncated: logging.getLogger(__name__).warning("PersonaContext omitted because no item fit its token budget")
            return PersonaContext("", [], 0, bool(picked))
        rendered=header+"\n"+"\n".join(lines)
        if truncated: logging.getLogger(__name__).warning("PersonaContext truncated to its configured token/item budget")
        return PersonaContext(rendered, ids, estimate_tokens(rendered), truncated)

    def mark_used(self, node_ids):
        ids=[str(x) for x in (node_ids or [])]
        if not ids or not self.memory: return 0
        now=self.clock()
        with self.memory._db_lock:
            marks=','.join('?' for _ in ids)
            cur=self.memory.db.execute(f"UPDATE personal_nodes SET last_used_ts=?, use_count=COALESCE(use_count,0)+1 WHERE id IN ({marks})",[now]+ids)
            self.memory.db.commit(); return cur.rowcount

    def describe_known(self) -> list[str]:
        if not self.memory or not self._facts_enabled(): return []
        with self.memory._db_lock:
            rows=self.memory.db.execute("SELECT label,kind,expires_ts FROM personal_nodes WHERE status='confirmed' AND consented=1 AND COALESCE(sensitivity,'normal')='normal' ORDER BY importance DESC, updated_ts DESC").fetchall()
        now=self.clock(); out=[]
        for row in rows:
            if row["expires_ts"] is not None and float(row["expires_ts"])<=now: continue
            raw=str(row["label"] or "")[:120]
            if is_instruction_like(raw): continue
            label=_neutral(raw)
            if label and label not in out: out.append(label)
        return out
