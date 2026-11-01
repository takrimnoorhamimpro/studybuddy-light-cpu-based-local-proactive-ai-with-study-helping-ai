# Study Buddy v11 Phase 0 — Patch Notes

## Folder tree (NEW / CHANGED)

```text
StudyBuddy/
├── app/
│   ├── companion/                         NEW
│   │   ├── __init__.py                   NEW
│   │   ├── migrations.py                 NEW
│   │   └── privacy.py                    NEW
│   ├── personal_companion.py             CHANGED
│   ├── intent_router.py                  CHANGED
│   ├── chat_core.py                      CHANGED
│   ├── desktop_app.py                    CHANGED
│   ├── memory.py                         CHANGED
│   ├── lifecycle.py                      CHANGED
│   ├── background_agent.py               CHANGED
│   ├── test_companion_phase0.py          NEW
│   ├── test_review_fixes.py              NEW
│   └── test_lifecycle.py                 CHANGED
├── config/
│   └── intent_dictionary.json            CHANGED
├── scripts/
│   └── run_tests.py                      NEW
├── README.md                             CHANGED
├── CHANGELOG.md                           NEW
├── PHASE0_AUDIT.md                        NEW
├── PHASE0_PATCH_NOTES.md                  NEW
└── VERSION.txt                            CHANGED
```

## Behavior changes

- Onboarding expires after five minutes of inactivity. Normal questions/requests release to normal chat; a second ambiguous reply releases rather than nagging. English YES / NO / SKIP variants remain deterministic. Name collection strips common prefixes, validates 1–3 words / 40 characters, and saves only after a second explicit YES.
- Scheduled follow-ups use `pending → claimed → delivered`, with explicit release on busy/error, stale-claim recovery, and pending-row deduplication. Unknown follow-up kinds are not consumed.
- `Memory.forget(keyword)` cascades through conversation and companion data by default. `preview_forget(keyword)` returns read-only per-table counts. Tasks/reminders are preserved unless `include_tasks=True`; the GUI requests separate confirmation before deleting them. The audit entry does not store the keyword.
- First companion-schema migration uses `PRAGMA user_version`, an additive transaction and a timestamped backup for an existing on-disk database.
- `LifecycleService` receives the active profile from the GUI and background agent.
- README/changelog/version now describe v11 Phase 0. The duplicate v9 README was removed.

## Review fixes in this build

- Intent and dialog vocabulary is English-only. `has_pending` now clears onboarding state after five minutes even when queried before another user message arrives.

- Onboarding timestamp expires after 300 seconds of inactivity; unrelated or second ambiguous messages are reprocessed through the normal chat path.
- Name input strips `my name is`, `call me`, `i am`, `i'm`, `it's`, and `name is`; it rejects implausible strings and still requires a second YES to save.
- `hmm` is no longer a global affirmative.
- `preview_forget()` is read-only and includes matching/cascade counts. `forget_topic()` leaves tasks/reminders untouched unless `include_tasks=True`; the GUI asks separately before task/reminder deletion.

## Migration note

No user database was migrated during this build. On the first launch of this version, an existing on-disk database with an older `user_version` gets a consistent `memory.db.bak-YYYYMMDD-HHMMSS` backup before additive schema changes. Keep the original project and `users/<profile>/memory.db` backed up before replacing an installation. Do not delete the backup until Windows migration and restore checks pass.

## Sandbox verification

- `python -m compileall -q app chat_cli.py` — passed.
- `python scripts/run_tests.py` — 275 ran, 275 passed, 0 skipped, 0 failed. `python -m compileall -q app chat_cli.py` — passed.
- The suite uses fake LLMs and SQLite tests; it does not certify Windows UI or real Ollama behavior.

## Manual Windows checklist

1. Copy the current project folder and `users/<profile>/` before installing.
2. Launch with Ollama running locally and confirm normal small talk still feels unchanged.
3. In My World, choose “Start getting-to-know-you chat”; reply `yes`, provide a name, then explicitly `yes` again. Verify the name appears as confirmed only after the second yes.
4. Repeat and reply `no`, `skip`, `not now`, `later`, and `stop`; confirm no fact is saved and the app does not get stuck in a YES/NO loop.
5. Schedule “ask later” twice; confirm one pending prompt, then leave the app busy at delivery time and verify the prompt remains pending and appears later.
6. Confirm a graph node, preview/forget a distinctive word, and verify conversation/companion data is removed while tasks/reminders remain unless separately confirmed in the GUI.
7. Inspect `users/<profile>/memory.db.bak-*`, confirm the current database still contains old records plus new schema, and test restore from a copy—not the only live database.
8. Verify tray notifications, quiet hours, window-title privacy filtering, camera/voice optionality and app startup/exit on the target Windows version.

## Known limitations

- This is Phase 0 only. Confirmed graph memories are not yet ranked/injected into normal chat; that is Phase 1.
- Onboarding currently asks for one preferred-name fact, not the complete multi-question onboarding plan.
- GUI and CLI proactive policies are not unified yet. The old CLI path is not shown to feed prior conversation into generation; instead, its monitor lacks the title evidence required by the conservative generator and may therefore emit no nudge.
- No real Windows + Ollama test was run in this environment.
