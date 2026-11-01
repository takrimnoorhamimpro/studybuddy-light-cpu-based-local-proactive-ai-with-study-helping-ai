# Study Buddy v11 — Phase 2: A Companion That Reaches Out Well

Study Buddy is an English-only, local desktop study companion for Windows. It uses one resident `llama3.2:3b` through Ollama, CPU-only inference, Tkinter, and local SQLite. Phase 2 adds restrained, explainable proactive behavior without adding a model call for any new trigger.

## Phase 2 changes

- One `InitiativePolicy.evaluate(...)` decision point for the GUI and CLI compatibility shim.
- Confirmed deadlines and due tasks become `open_loops`; 3-day, 1-day, and morning-of steps are deterministic, calm, snoozable, and never replay a missed step as backlog.
- Opt-in `RhythmTracker` is OFF by default. It stores coarse weekday/hour/app-category counts only, never titles, page text, app names, screenshots, or keystrokes. A read-only Patterns section explains low confidence and supports reset.
- `FeedbackLogger` records shown/replied/ignored/negative/muted/unseen outcomes without storing nudge bodies. Beta acceptance adapts by trigger and 3-hour time bucket; repeated ignores halve the rate for seven days and three negative reactions disable a trigger until re-enabled.
- Every nudge has a human-readable reason, appears in the Nudge log (last 20), and can be explained with “why did you message me”. Typed feedback includes “not helpful”, “less often”, “stop these”, “leave me alone”, and “not now”.
- Non-title initiatives use the daily cap/cooldowns and handwritten templates. Window-title check-ins are event-based: a stable, meaningful title is considered once per app session, without a blanket hourly cooldown. Their generation remains memory/history-free and retains `[SKIP]` and `reply_is_grounded` checks.
- Schema v3 is additive, transactional, backup-first, and clears legacy evidence that fails the strengthened sensitive filter. Instruction-like preferences are blocked both during extraction and at prompt-render time.
- Phase 1 remains in place: consent-gated facts, deterministic extraction, memory transparency, adaptive reply length, prompt-budget enforcement, and CPU-only execution.

## Companion conversations and background memory review

- On launch, an opted-in companion opens with one short English question: “Hey [name], how’s your day going?” The greeting is deterministic, so startup does not wait for another model call. First-time memory consent remains a separate, explicit choice.
- Companion conversations follow the user's lead through ordinary chat. My World gaps are only gentle context for a relevant follow-up; the model asks at most one question, avoids checklist-style interviews, follows topic changes, and stops personal prompts when the user declines or asks to stop.
- A title-based check-in needs a meaningful, non-private foreground title that stays stable for the configured switch interval (8 seconds by default). It runs while the PC is active, skips quiet hours, meetings, long inactivity, typing, and user-requested pauses, and rechecks that the same window is still in front before showing the result. A distinct title is considered once per app session; there is no blanket hourly title cooldown.
- The local model receives the exact sanitized title as text, not the page or screen. Its guess is tentative and checked against that title. If the user changes windows during generation, Luna drops the stale message. A title stays available for at most one relevant reply and five minutes.
- “No,” “skip,” “I’m focused,” and “don’t disturb me” are treated as availability boundaries. A pause lasts until the user's next message unless they give a duration; any message from the user resumes future check-ins early.
- With personal-memory consent enabled, an asynchronous local review waits for 90 seconds without chat or model activity, then reviews up to 60 retained messages together. It does not require PC inactivity. Typing or a new chat turn cancels the review and leaves its cursor unchanged so the batch can be retried after the next quiet period. One pass can add up to 5 evidence-backed notes to the redesigned My World areas: Everyday context, Interests & enjoyment, Goals & progress, Values & meaning, People & belonging, Good moments, Difficult moments, Support & communication, and Life moments (for legacy entries). Good and difficult moments must be concrete and clearly meaningful to the user; transient moods, generic reactions, and window activity are excluded. Notes are not psychological diagnoses or personality scores.
- Ollama's JSON schema mode is used for memory review when the installed client supports it. Python still checks that each saved label is supported by a direct user quote, is non-sensitive, and is not a duplicate.

## Run locally

1. Install the project's existing requirements only; Phase 2 adds no dependency.
2. Start Ollama and ensure `llama3.2:3b` is available locally.
3. Launch the existing Windows desktop entry point as documented by your installation.
4. Run tests from the project root:

```powershell
py -3.12 -m compileall -q app scripts
py -3.12 scripts/run_tests.py
py -3.12 scripts/sim_proactive.py
```

The 2026-10-29 stabilization build passes **369 tests, 0 skipped, 0 failed** under Python 3.12. The simulation uses a fake clock and no model/network/thread calls. Its warm-cache policy timing is a policy-only measurement, not a target-PC timing guarantee.

## Windows manual-test checklist

- Run the GUI with the local Ollama model; confirm the one-question English day check-in, natural user-led small talk and My World follow-ups, quiet-debounce review/cancellation, ordinary chat tool routing, and model residency. A component smoke check verified the title-only generation path with `llama3.2:3b`, but it did not launch the full GUI/tray app.
- Confirm deadline candidate wording includes its date, then test yes/later/stop. Only an explicit yes should hand a pre-written request to normal chat.
- Test quiet hours, pause-duration choice, visible pause acknowledgement, automatic resume on the next user message or app restart, daily cap 0–5 for non-title initiatives, busy/private/meeting windows, typing during generation, pending dialogs, title stability, and stale-window cancellation.
- Leave Rhythm learning OFF and verify no writes; enable it, collect samples over at least five distinct days, inspect Patterns, turn it off, and delete patterns.
- Test “why did you message me”, “not helpful”, “less often”, “stop these”, and “leave me alone”; verify trigger mute/disable/re-enable and the last-20 Nudge log.
- Migrate a copy of a v2 database, verify one backup and `PRAGMA user_version=3`, then reopen it and verify no second backup.
- Verify reminder/task due notifications still behave as before and are not duplicated by open-loop nudges.

## Known limitations

- Python 3.12, Tkinter 8.6, and local `llama3.2:3b` were available for the previous stabilization build. The relationship conversation and background-review changes in this working update have not yet been validated on the full Windows GUI; confirm them on the target PC.
- Rhythm patterns need several distinct active days and enough samples; low-confidence profiles intentionally do not change behavior.
- InitiativePolicy nudges remain template-based except for the existing `title_clue` trigger. Relationship check-ins and opted-in personal conversation use the local chat model; the background memory curator validates every proposed detail against direct user evidence.
- The CLI source is at the repository root (`chat_cli.py`), not `app/chat_cli.py`; the shim preserves its public API. The existing window-stability timer lives in `DesktopApp`, while `ContextEngine` supplies the filtered category/title clue.
- Phase 3 has not started. It may add reflection/weekly review and optional growth or mood features only after explicit approval.
