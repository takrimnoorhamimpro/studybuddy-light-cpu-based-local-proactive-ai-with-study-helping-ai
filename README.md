# Study Buddy v11 — Phase 2: A Companion That Reaches Out Well

Study Buddy is an English-only, local desktop study companion for Windows. Its default chat model is `qwen2.5:1.5b` through Ollama, with CPU-only inference, Tkinter, and local SQLite. The smaller default was selected for lower reply latency on this PC; `model_chat` remains user-configurable.

## Phase 2 changes

- One `InitiativePolicy.evaluate(...)` decision point for the GUI and CLI compatibility shim.
- Confirmed deadlines and due tasks become `open_loops`; 3-day, 1-day, and morning-of steps are deterministic, calm, snoozable, and never replay a missed step as backlog.
- Opt-in `RhythmTracker` is OFF by default. It stores coarse weekday/hour/app-category counts only, never titles, page text, app names, screenshots, or keystrokes. A read-only Patterns section explains low confidence and supports reset.
- `FeedbackLogger` records shown/replied/ignored/negative/muted/unseen outcomes without storing nudge bodies. Beta acceptance adapts by trigger and 3-hour time bucket; repeated ignores halve the rate for seven days and three negative reactions disable a trigger until re-enabled.
- Every nudge has a human-readable reason, appears in the Nudge log (last 20), and can be explained with “why did you message me”. Typed feedback includes “not helpful”, “less often”, “stop these”, “leave me alone”, and “not now”.
- Non-title initiatives use per-trigger cooldowns and handwritten templates; the daily cap is optional, and 0 means unlimited. Window-title check-ins are event-based: each stable foreground visit is sent to the local model, with a configurable repeat interval for a title that stays open and no blanket hourly cooldown. Raw titles stay out of persistent memory and event logs.
- Schema v3 is additive, transactional, backup-first, and clears legacy evidence that fails the strengthened sensitive filter. Instruction-like preferences are blocked both during extraction and at prompt-render time.
- Phase 1 remains in place: consent-gated facts, deterministic extraction, memory transparency, adaptive reply length, prompt-budget enforcement, and CPU-only execution.

## Companion conversations and background memory review

- On launch, an opted-in companion opens with one short English question: “Hey [name], how’s your day going?” The greeting is deterministic, so startup does not wait for another model call. First-time memory consent remains a separate, explicit choice.
- Companion conversations follow the user's lead through ordinary chat. Unfilled My World areas and old summaries are no longer copied into every prompt; relevant confirmed preferences still enter through bounded persona retrieval, while the quiet reviewer curates new details later. The model asks at most one natural follow-up, avoids checklist-style interviews, follows topic changes, and respects stop/busy boundaries.
- After Study Buddy launches, a new foreground window or browser tab can trigger curiosity once it stays in front for the configured dwell (25 seconds by default), regardless of keyboard or mouse input. Each check-in is written by the local LLM from the app and sanitized title; it makes a short guess about the likely topic or activity instead of reading the full caption aloud. Voice reads the same generated message. The next three chat turns receive the title as temporary context so natural follow-ups do not depend on specific keywords. The Settings panel lets the user adjust the dwell, minimum gap between different windows (0 seconds by default), repeat interval for the same title (5 minutes by default; 0 disables repeats), and recap interval for window-related chats (30 minutes by default; 0 disables recaps). Quiet hours, private or meeting windows, typing, and explicit pauses still apply.
- The local model receives the current sanitized app, category, and title for each proactive check-in. On a same-title repeat, it also receives its previous check-in and a short, session-only exchange with the user's recent replies, so it can continue naturally instead of asking an already answered question. Changing leading view counters do not create fake new windows. Caption dumps, binary platform questions, repeated questions, and third-person narration are rejected; normal chat gets one LLM rewrite only if it echoes a long title. If a proactive generation fails or returns no useful check-in, the message is skipped rather than replaced with a fixed guess. If the foreground window changes during generation, Luna drops the stale result and can check again after the user returns. The title remains temporary chat context for the next three turns or five minutes, whichever comes first; a short related exchange is held in memory for up to 15 minutes to support a natural repeat.
- When the configured interval elapses from the first queued window check-in, the local LLM can recap a batch of up to 12 title-related exchanges from this app session and ask one fresh question. Both unanswered Buddy check-ins and user/Buddy dialogue are included. Titles and Buddy's guesses are uncertain clues; the user's own words are stronger evidence. Larger backlogs are handled in later batches. The queue stays in memory only. A recap waits for current chat/model work to finish, and defers for a private or meeting window, a pause, or quiet hours; new messages do not restart the interval. No pixels, page contents, or saved profile are used.
- When the configured interval elapses from the first queued window check-in, the local LLM can recap a batch of up to 12 title-related exchanges from this app session and ask one fresh question. Both unanswered Buddy check-ins and user/Buddy dialogue are included. Titles and Buddy's guesses are uncertain clues; the user's own words are stronger evidence. Malformed output containing skip markers or third-person narration is discarded rather than shown. Larger backlogs are handled in later batches. The queue stays in memory only. A recap waits for current chat/model work to finish, and defers for a private or meeting window, a pause, or quiet hours; new messages do not restart the interval. No pixels, page contents, or saved profile are used.
- “No,” “skip,” “I’m focused,” and “don’t disturb me” are treated as availability boundaries. A pause lasts until the user's next message unless they give a duration; any message from the user resumes future check-ins early.
- With personal-memory consent enabled, an asynchronous local review waits for 90 seconds without chat or model activity, then reviews up to 60 retained messages together. It does not require PC inactivity. Typing or a new chat turn cancels the review and leaves its cursor unchanged so the batch can be retried after the next quiet period. One pass can add up to 5 evidence-backed notes to the redesigned My World areas: Everyday context, Interests & enjoyment, Goals & progress, Values & meaning, People & belonging, Good moments, Difficult moments, Support & communication, and Life moments (for legacy entries). Good and difficult moments must be concrete and clearly meaningful to the user; transient moods, generic reactions, and window activity are excluded. Notes are not psychological diagnoses or personality scores.
- Ollama's JSON schema mode is used for memory review when the installed client supports it. Python still checks that each saved label is supported by a direct user quote, is non-sensitive, and is not a duplicate.
- All chat and background calls share one Ollama model and are serialized. Typing or sending a message cancels an active low-priority review/title stream and closes its response so foreground chat can take the model. The architecture dashboard reports queue, model-load, prompt and generation timings separately.

## Run locally

1. Install the project's existing requirements only; Phase 2 adds no dependency.
2. Start Ollama and ensure `qwen2.5:1.5b` is available locally. `setup_cpu_optimized.bat` installs it.
3. Launch the existing Windows desktop entry point as documented by your installation. The startup fixer checks the selected model and warms it in Ollama before the app opens, so the first chat does not pay the model-load delay.
4. Run tests from the project root:

```powershell
py -3.12 -m compileall -q app scripts
py -3.12 scripts/run_tests.py
py -3.12 scripts/sim_proactive.py
```

Earlier project validation passed 371 tests and the deterministic simulation. Automated tests were not run for this latency change.

With the final `127.0.0.1` endpoint, a warm in-memory `ChatCore.handle_message` small-talk run streamed its first text in **0.42 s** and completed in **1.64 s** on the i5-8265U. A controlled Ollama stream request with the same prompt received response headers in **0.62 s** through `127.0.0.1` versus **2.13 s** through `localhost`. These are local samples, not latency guarantees. Run `py -3.12 scripts\benchmark_ollama.py --runs 3` while the PC is idle to measure your current setup.

## Windows manual-test checklist

- Run the GUI with the local Ollama model; confirm the one-question English day check-in, natural user-led small talk and My World follow-ups, quiet-debounce review/cancellation, ordinary chat tool routing, and model residency. The full GUI/tray flow still needs a manual launch check after this model change.
- Confirm deadline candidate wording includes its date, then test yes/later/stop. Only an explicit yes should hand a pre-written request to normal chat.
- Test quiet hours, pause-duration choice, visible pause acknowledgement, automatic resume on the next user message or app restart, unlimited daily nudges or a selected cap, busy/private/meeting windows, typing during generation, pending dialogs, title stability, and stale-window cancellation.
- Leave Rhythm learning OFF and verify no writes; enable it, collect samples over at least five distinct days, inspect Patterns, turn it off, and delete patterns.
- Test “why did you message me”, “not helpful”, “less often”, “stop these”, and “leave me alone”; verify trigger mute/disable/re-enable and the last-20 Nudge log.
- Migrate a copy of a v2 database, verify one backup and `PRAGMA user_version=3`, then reopen it and verify no second backup.
- Verify reminder/task due notifications still behave as before and are not duplicated by open-loop nudges.

## Known limitations

- Python 3.12, Tkinter 8.6, and the local Qwen and Llama Ollama models are available in this environment. The full visual GUI/tray interaction and startup behavior still need a manual launch check.
- Rhythm patterns need several distinct active days and enough samples; low-confidence profiles intentionally do not change behavior.
- InitiativePolicy nudges remain template-based except for the existing `title_clue` trigger. Relationship check-ins and opted-in personal conversation use the local chat model; the background memory curator validates every proposed detail against direct user evidence.
- The CLI source is at the repository root (`chat_cli.py`), not `app/chat_cli.py`; the shim preserves its public API. The existing window-stability timer lives in `DesktopApp`, while `ContextEngine` supplies the filtered category/title clue.
- Phase 3 has not started. It may add reflection/weekly review and optional growth or mood features only after explicit approval.
