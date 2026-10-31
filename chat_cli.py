"""
chat_cli.py  -  Quick terminal chat to TEST the core (the window comes next).

    python chat_cli.py

Type /bye to quit. Make sure Ollama is running first.

Commands:
    /remember some fact     save a fact about you
    /trust Name             add someone you trust
    /forget word            delete everything containing that word
    /facts                  show saved facts
    /names                  show who you are and who Buddy is
    /clearchat              forget the recent conversation (fixes a confused chat)
    (say 'my name is X' / 'call me X' / 'your name is Y' - names are saved as names)
Each reply shows how long it took, so you can note the speed for your research.
"""

import os
import sys
import time
import threading


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "app"))

import json  # noqa: E402

from chat_core import ChatCore  # noqa: E402
from memory import Memory  # noqa: E402
from context_awareness import WindowsContextMonitor  # noqa: E402
from initiative import InitiativeEngine  # noqa: E402


def main():
    try:
        with open(os.path.join(BASE_DIR, "config", "settings.json"), encoding="utf-8") as f:
            profile = json.load(f).get("profile", "hamim")
    except OSError:
        profile = "hamim"
    memory = Memory(os.path.join(BASE_DIR, "users", profile, "memory.db"))
    core = ChatCore(BASE_DIR, memory=memory)
    if not core.llm.is_alive():
        print("Ollama is not running. Start Ollama and try again.")
        return
    monitor = WindowsContextMonitor()
    initiative = InitiativeEngine(core, monitor, core.settings)
    stop_event = threading.Event()

    def proactive_loop():
        # Low-cost polling; actual LLM call only happens when a cooldown/idle trigger qualifies.
        while not stop_event.wait(20):
            try:
                nudge = initiative.check_once()
                if nudge:
                    print("\n\nBuddy (checking in): " + nudge + "\nYou: ", end="", flush=True)
                    initiative.acknowledge_shown()
            except Exception:
                # Awareness must never crash chat or affect stored user data.
                continue

    watcher = None
    if core.settings.get("proactive_enabled", True) and monitor.supported:
        watcher = threading.Thread(target=proactive_loop, name="StudyBuddyAwareness", daemon=True)
        watcher.start()
    print("Study Buddy test chat. Type /bye to quit.\n")
    if monitor.supported:
        print("Local awareness is on (app category + idle time; optional audio app names). Type /awareness off to disable nudges.\n")
    while True:
        try:
            text = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        initiative.mark_interaction()
        if text.strip().lower() == "/bye":
            break
        low = text.strip()
        if low.lower().startswith("/remember "):
            said = core.learn_names(low[10:])
            if said:
                print("   " + " ".join(said))     # names are saved as names, not as facts
            else:
                print("   saved." if memory.add_fact(low[10:]) else "   (already saved)")
            continue
        if low.lower() == "/awareness off":
            stop_event.set()
            print("   proactive awareness stopped for this session.")
            continue
        if low.lower() == "/awareness status":
            print("   awareness supported: " + str(monitor.supported) + " | proactive enabled: " + str(not stop_event.is_set()))
            if monitor.supported:
                print("   " + monitor.situation_line(monitor.snapshot()))
            continue
        if low.lower() == "/dismiss":
            initiative.dismiss(60)
            print("   okay, I will give you some space for an hour.")
            continue
        if low.lower() == "/clearchat":
            print("   cleared " + str(core.clear_chat()) + " message(s).")
            continue
        if low.lower() == "/names":
            print("   you: " + str(core.user_name) + " | buddy: " + str(core.buddy_name))
            continue
        if low.lower().startswith("/trust "):
            print("   saved." if core.add_trusted_person(low[7:]) else "   (already saved)")
            continue
        if low.lower().startswith("/forget "):
            print("   deleted " + str(memory.forget(low[8:])) + " item(s).")
            core.reload_history()
            continue
        if low.lower() == "/facts":
            for fact in memory.list_facts(50):
                print("   - " + fact)
            continue
        start = time.time()
        result = core.handle_message(text)
        took = time.time() - start
        if result["reply"]:
            print("\nBuddy: " + result["reply"])
            print("   [" + result["flag"] + " | " + format(took, ".1f") + " s]\n")
    stop_event.set()
    memory.close()
    print("Bye!")


if __name__ == "__main__":
    main()
