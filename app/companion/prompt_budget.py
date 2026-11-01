"""Deterministic prompt estimator and conservative trimming."""
from __future__ import annotations
import logging
from companion.user_model import estimate_tokens
log=logging.getLogger(__name__)

class PromptBudget:
    @staticmethod
    def fit(system_parts, history, user_text, limit=1800, persona_text=""):
        """Fit assembled prompt; never truncate safety core or the user's text."""
        system=str(system_parts or "")
        turns=[dict(x) for x in (history or [])]
        original_persona=persona_text
        current_persona=original_persona
        truncated=False
        def estimate():
            return estimate_tokens(system)+sum(estimate_tokens(x.get("content", "")) for x in turns)+estimate_tokens(user_text)
        # Drop lowest-ranked persona bullets first; UserModel renders higher-ranked items first.
        while estimate()>limit and current_persona and current_persona in system:
            lines=current_persona.splitlines()
            if len(lines)>2:
                reduced="\n".join(lines[:-1])
                system=system.replace(current_persona,reduced,1)
                current_persona=reduced
                truncated=True
            else:
                system=system.replace("\n\n"+current_persona,"",1)
                if current_persona in system: system=system.replace(current_persona,"",1)
                current_persona=""
                truncated=True
        # Drop oldest turns, preserving at least the most recent turn if one exists.
        while estimate()>limit and len(turns)>1:
            turns.pop(0); truncated=True
        # If the one retained turn is itself unusually long, shorten that turn as a last
        # history-only measure while preserving one history message and the full user text.
        if estimate()>limit and turns:
            original=str(turns[-1].get("content", ""))
            low,high=0,len(original)
            best=""
            while low<=high:
                mid=(low+high)//2
                turns[-1]["content"]=original[:mid]
                if estimate()<=limit:
                    best=original[:mid]; low=mid+1
                else:
                    high=mid-1
            turns[-1]["content"]=best; truncated=True
        # Optional context lines may be removed; safety, language, character, and user text stay.
        if estimate()>limit:
            for marker in ("Situation: ", "CURRENT DESKTOP CONTEXT", "ONE-TURN PROACTIVE REPLY CONTEXT"):
                while estimate()>limit and marker in system:
                    idx=system.find(marker)
                    start=system.rfind("\n\n", 0, idx)
                    if start < 0: start=idx
                    end=system.find("\n\n", idx)
                    if end < 0: end=len(system)
                    system=system[:start]+system[end:]
                    truncated=True
        total=estimate()
        if total>limit:
            # Do not damage safety core or user text; signal this unusual config in logs.
            log.warning("Prompt estimate remains above budget after optional context/history trimming: %s > %s",total,limit)
        elif truncated:
            log.warning("PromptBudget trimmed persona/history/optional context to fit the configured limit")
        return system, turns, total, truncated
