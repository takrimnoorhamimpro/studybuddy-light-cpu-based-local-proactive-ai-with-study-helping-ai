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
        # Trim complete exchanges, never leave an orphaned user or assistant
        # message and never feed the model a cut-off sentence as its context.
        while estimate()>limit and turns:
            if (len(turns) >= 2
                    and turns[0].get("role") == "user"
                    and turns[1].get("role") == "assistant"):
                del turns[:2]
            else:
                turns.pop(0)
            truncated=True
        # Optional context lines may be removed; safety, language, character, and user text stay.
        if estimate()>limit:
            for marker in ("Situation: ", "CURRENT DESKTOP OBSERVATION", "RECENT WINDOW OBSERVATION",
                           "PREVIOUS PROACTIVE MESSAGE", "RETRIEVED LOCAL MEMORY"):
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
