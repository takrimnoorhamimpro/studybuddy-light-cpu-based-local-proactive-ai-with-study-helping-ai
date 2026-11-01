# Study Buddy v10 architecture

- **Primary model:** existing Ollama `llama3.2:3b`; no second model added.
- **Fast chat/tool routing:** inherited from v9, including normal-chat path without irrelevant tool schemas.
- **Desktop context:** existing `ContextEngine.capture()` reads foreground process and window title on Windows. `safe_summary()` redacts sensitive titles; `has_specific_title_clue()` gates proactive check-ins. Proactive prompt is isolated from past chat/task/reminder content.
- **Personal graph:** `app/personal_companion.py`, tables `personal_nodes`, `personal_edges`. Candidate facts are not treated as confirmed. Confirmed facts require explicit consent. CRUD is local SQLite.
- **Follow-ups:** `companion_followups` and `companion_settings` tables. Due onboarding follow-ups are one-shot and gated by active-chat/proactive/quiet-hour state.
- **UI:** `My World` in `app/desktop_app.py` provides graph-like node view, candidate/confirmed status, add, confirm, forget, and scheduled follow-up controls.
- **Data isolation:** per-user database remains under `users/<profile>/memory.db`.

## Known limitations before production
1. Windows-native GUI and foreground title behavior need a real Windows test; this build environment cannot verify Windows APIs.
2. The current visual map uses a deterministic circular Tk Canvas layout, not force-directed graph layout or full edge editing.
3. Onboarding starts from the My World control; a complete conversational yes/no state machine and natural-language extraction-to-candidate flow are not yet fully automated.
4. Follow-up delivery depends on the desktop app being running; delivery after app restart is handled by the persisted due timestamp when the GUI scheduler next runs. It does not promise OS-level toast delivery for these onboarding follow-ups while the GUI is closed.
5. Production readiness still requires tests for scheduler races, database migration/backup restore, UI accessibility, Windows startup/shutdown, and live Ollama model availability/latency.
