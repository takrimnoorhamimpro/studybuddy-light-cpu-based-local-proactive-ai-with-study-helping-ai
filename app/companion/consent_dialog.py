"""Deterministic parser and rendering for batched candidate-memory consent."""
import re

YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "yes please", "yes thanks", "yes thank you", "yes to all", "all", "all of them"}
NO = {"no", "nope", "nah", "no thanks", "no thank you", "none", "none of them"}
DEFER = {"skip", "later", "not now", "pass"}
CANCEL = {"cancel", "stop"}

def render_batch(candidates):
    lines=["I noticed a few things you mentioned. Want me to remember them?"]
    for i,item in enumerate(list(candidates)[:3],1):
        label=str(item.get("label", ""))
        label=re.sub(r"(?i)^the user(?:'s)?\s+", "", label).rstrip(".")
        lines.append(f"{i}. {label[:120]}")
    lines.append('Reply "yes" for all, "yes 1" or "yes 1,2" for some, "no" to decline them, or "skip" to decide later.')
    return "\n".join(lines)

def parse_reply(text, count):
    value=" ".join(str(text or "").casefold().strip().split())
    if value in YES: return ("confirm", list(range(1,count+1)))
    m=re.fullmatch(r"yes\s+([1-9](?:\s*(?:,|and)\s*[1-9])*)",value)
    if m:
        nums=[int(n) for n in re.findall(r"[1-9]",m.group(1))]
        if any(n>count for n in nums): return ("out_of_range",[])
        return ("confirm",sorted(set(nums))) if nums else ("ambiguous",[])
    m=re.fullmatch(r"yes\s+([1-3](?:\s*(?:,|and)\s*[1-3])*)",value)
    if m:
        nums=[int(n) for n in re.findall(r"[1-3]",m.group(1))]
        return ("confirm", sorted(set(n for n in nums if n<=count))) if nums else ("ambiguous",[])
    m=re.fullmatch(r"only\s+([1-3])",value)
    if m: return ("confirm",[int(m.group(1))]) if int(m.group(1))<=count else ("ambiguous",[])
    m=re.fullmatch(r"([1-3])\s+and\s+([1-3])",value)
    if m:
        nums=sorted(set(map(int,m.groups())))
        return ("confirm",[n for n in nums if n<=count]) if any(n<=count for n in nums) else ("ambiguous",[])
    if value in NO: return ("reject",list(range(1,count+1)))
    if value in DEFER: return ("defer",list(range(1,count+1)))
    if value in CANCEL: return ("cancel",list(range(1,count+1)))
    return ("ambiguous",[])


def should_deliver_consent(*, now, session_started_at, last_interaction_ts, last_prompt_ts,
                           prompt_count_today, pause_until, idle_seconds=120,
                           cooldown_seconds=1800, daily_cap=3, facts_enabled=True,
                           proactive_enabled=True, busy=False, worker_busy=False,
                           dialog_pending=False, quiet=False):
    """Pure delivery gate shared by the scheduler and deterministic tests."""
    return bool(
        facts_enabled and proactive_enabled and not busy and not worker_busy
        and not dialog_pending and not quiet
        and float(now)-float(session_started_at) >= 60
        and float(now)-float(last_interaction_ts) >= max(120, int(idle_seconds))
        and float(now)-float(last_prompt_ts) >= max(1800, int(cooldown_seconds))
        and int(prompt_count_today) < max(1, min(3, int(daily_cap)))
        and float(now) >= float(pause_until)
    )
