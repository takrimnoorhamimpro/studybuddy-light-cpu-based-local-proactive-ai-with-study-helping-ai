"""Print deterministic estimates for worst-case Phase 1 prompt scenarios."""
import os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,"app"))
from prompt_builder import build_system_prompt
from companion.prompt_budget import PromptBudget
from safety import load_safety
import json

with open(os.path.join(ROOT,"config","settings.json"),encoding="utf-8") as f: settings=json.load(f)
with open(os.path.join(ROOT,"config","character.json"),encoding="utf-8") as f: character=json.load(f)
safety=load_safety(os.path.join(ROOT,"config"))
scenarios=[
    ("baseline", [], "Explain how TCP establishes a connection.", ""),
    ("long history + persona", [{"role":"user","content":"Previous study discussion "*100},{"role":"assistant","content":"Answer about exam preparation "*100}]*4, "Explain a weekly study plan for my networking exam.", "Things the user chose to tell you (use a detail only if it helps this reply; never list them or mention memory):\n- The user is preparing for their networking exam on Nov 20.\n- The user prefers short answers."),
    ("camera + capability + history", [{"role":"user","content":"Question about settings "*100},{"role":"assistant","content":"Detailed response "*100}]*4, "What capabilities do you have?", ""),
]
limit=1800
for name,history,user,persona in scenarios:
    system=build_system_prompt(safety.prompt_core(),character,owner_facts=None,persona_context=persona)
    if "camera" in name:
        system += "\n\nCAMERA CONSENT RULES: " + ("camera privacy consent rules "*35)
        system += "\n\nCAPABILITY CONTEXT: " + ("available local tool capability description "*30)
    system += "\n\nSituation: " + ("current app context "*80)
    system += "\n\nCURRENT DESKTOP CONTEXT: " + ("sanitized app title "*80)
    fitted,turns,estimate,truncated=PromptBudget.fit(system,history,user,limit=limit,persona_text=persona)
    print(f"{name}: estimated_tokens={estimate}, turns_kept={len(turns)}, truncated={truncated}, within_limit={estimate <= limit}")
    if estimate > limit: raise SystemExit(1)
