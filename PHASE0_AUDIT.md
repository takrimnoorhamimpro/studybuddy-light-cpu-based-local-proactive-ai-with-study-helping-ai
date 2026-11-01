# v10 to v11 Phase 0 audit and verification

## Section 2 findings checked against source

- **D1 — Partly confirmed.** `desktop_app.py` previously appended the follow-up only to the UI; it did not register an `IntentRouter` dialog. Thus YES/NO/SKIP had no onboarding state. The prompt's claim that a bare `yes` necessarily receives irrelevant tool schemas is **not supported**: the existing request-scoped router returns no tool schemas for `yes`, `no`, and `skip` when loaded with the project's route dictionary.
- **D2 — Confirmed.** `PersonalCompanion.due_followups()` marked all due records delivered before UI acknowledgement, while the event handler dropped them if busy. Production delivery now claims only supported onboarding records and acknowledges after display.
- **D3 — Confirmed.** Each scheduling click created another pending row. Pending records of the same kind now update in place.
- **D4 — Confirmed.** `Memory.forget()` only deleted turns, facts and notes. It now delegates to `PrivacyService`, which cascades through companion graph, follow-up payloads, open loops, matching tasks/reminders, and companion events where present.
- **D5 — Partly confirmed.** `LifecycleService` hard-coded `users/hamim/personality_versions`; it now accepts the active profile and both GUI/background-agent call sites pass it. `hamim` remains only as a legacy default where no profile is configured.
- **D6 — Partly confirmed.** `README.md` was byte-identical to `README_StudyBuddy_CPU_Optimized_v9.md`. The duplicate v9 README was removed and v11 README/changelog/version text added. The earlier Linux run did execute all 253 tests successfully; the claim that 28 tests need Tkinter is environment-dependent, so the new runner explicitly reports GUI-module skips only when Tkinter is unavailable.
- **D7 — Partly confirmed, with an important correction.** There are separate GUI and CLI proactive implementations. However, `ChatCore.generate_proactive_message()` explicitly excludes past conversation/history; the old claim that it feeds old chat guesses into current proactive generation is inaccurate. The CLI monitor does not provide the title snapshot required by that generator, so it is currently likely to emit no nudge. Unifying the two policy pipelines is deferred rather than risking a Phase 0 regression.

## Additional confirmed gaps (deferred to later phases)

- `chat_core.py` only retrieves ordinary facts/notes for explicit memory questions and never reads `personal_nodes` into normal chat prompts (G1).
- Rhythm tracking, feedback learning, conversational candidate extraction, prompt-budget enforcement, and the full Patterns/Privacy UI are not implemented yet (G2/G3/G6 and roadmap sections 6.1–6.12).
- The 64-token default generation cap remains the current normal-chat default; adaptive response modes are Phase 1 (G5).

## Verification in this sandbox

- `python -m compileall -q app chat_cli.py`: passed.
- `python scripts/run_tests.py`: 275 ran, 275 passed, 0 skipped, 0 failed (Tkinter was available in this sandbox).
- Tests cover onboarding YES/NO/SKIP without LLM/tool routing, expiry-aware pending state, the English-only vocabulary allow-list, claim/ack/release/stale claim, deduplication, unknown follow-up kinds, topic-forget cascade, and backup-first migration.
- Not verified here: Windows GUI behavior, Windows API context capture, real Ollama inference/latency, tray notifications, or restore of a real user's production database.
