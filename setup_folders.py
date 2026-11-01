"""
setup_folders.py  -  Run this ONCE to create the Study Buddy folder layout.

    python setup_folders.py
    python setup_folders.py myprofile      (use another profile name)

It never deletes or overwrites anything. If a file already exists, it is kept.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = sys.argv[1] if len(sys.argv) > 1 else "hamim"

DEFAULT_SETTINGS = {
    "model_chat": "llama3.2:3b",
    "model_embed": "nomic-embed-text",
    "ollama_url": "http://localhost:11434",
    "ollama_options": {"num_gpu": 0, "num_thread": 4, "num_ctx": 3072, "num_predict": 96, "temperature": 0.2},
    "strictness": "medium",
    "recent_turns": 3,
    "retrieved_notes": 2,
    "max_prompt_facts": 4,
    "tool_max_rounds": 2,
    "tool_max_calls_per_turn": 4,
    "adapters_to_keep": 5,
    "profile": PROFILE,
    "language": "en",
    "voice_enabled": False,
    "speech_model": "tiny",
    "awareness_enabled": True,
    "proactive_enabled": True,
    "proactive_cooldown_minutes": 180,
    "proactive_min_user_quiet_seconds": 90,
    "proactive_switch_stability_seconds": 25,
    "proactive_same_context_cooldown_minutes": 10,
    "same_category_nudge_minutes": 180,
    "quiet_start": "23:00",
    "quiet_end": "07:00",
    "pomodoro_minutes": 25,
    "break_minutes": 5,
    "auto_start_enabled": True,
    "webcam_enabled": False,
    "microphone_mode": "push_to_talk",
    "internet_permission": "ask",
    "tool_calling_enabled": True,
    "tool_retrieval_mode": "hybrid",
    "tool_retrieval_max_tools": 8,
    "model_keep_alive_mode": "Unlimited",
    "backup_encryption_required": True,
}

FOLDERS = [
    "app",
    "config",
    "logs",
    "users/" + PROFILE + "/library",
    "users/" + PROFILE + "/training/pool",
    "users/" + PROFILE + "/training/adapters",
    "users/" + PROFILE + "/training/eval_reports",
    "users/" + PROFILE + "/backups",
]


def main():
    for folder in FOLDERS:
        path = os.path.join(ROOT, folder)
        os.makedirs(path, exist_ok=True)
        print("ok folder:", folder)

    settings_path = os.path.join(ROOT, "config", "settings.json")
    if not os.path.exists(settings_path):
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        print("created: config/settings.json")
    else:
        print("kept existing: config/settings.json")

    print("\nDone. Profile name:", PROFILE)


if __name__ == "__main__":
    main()
