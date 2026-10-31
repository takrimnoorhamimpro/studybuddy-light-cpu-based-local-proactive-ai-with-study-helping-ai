# Study Buddy v11 Phase 2 architecture

Study Buddy remains a single-resident-model, local CPU application using `llama3.2:3b`. Python services own extraction, consent, retrieval, prompt budgeting, and persistence. The LLM does not write to SQLite.

## Phase 1 data flow

```text
User turn
  -> existing safety and dialog routing
  -> deterministic fast paths or normal model/tool route
  -> post-turn English-only rule extractor (no model call)
  -> candidate node + candidate_queue (never injected)
  -> idle/rate-limit/quiet-hours gate
  -> visible batched consent prompt + history entry
  -> deterministic YES / selected YES / NO / SKIP
  -> confirmed / rejected / deferred candidate state + consent ledger

Relevant future user turn
  -> UserModel deterministic lexical relevance scoring
  -> confirmed + consented + normal-sensitivity + unexpired nodes only
  -> PersonaContext under budget
  -> PromptBudget: persona, oldest history, optional situation
  -> one local llama3.2:3b generation
```

## Constraints preserved

- One local chat model; CPU-only (`num_gpu=0`); no new model, embedding, network, telemetry, dependency, or thread.
- Title-based initiative generation remains isolated from PersonaContext and saved personal facts. Relationship check-ins use personal context only after explicit relationship-memory consent.
- Ordinary small talk with no relevant stored facts receives no PersonaContext.
- Candidate memories are never injected before explicit consent. The facts tier can disable extraction, consent prompts, and retrieval.
- Prompt estimates use `ceil(len(text) / 3.6)` and the default maximum is 1,800 estimated tokens. Safety core and user text are never trimmed.
- Schema v2 is additive, transactional, and backup-first. Existing user data is preserved.

## Known limitations

Rule-based extraction recognizes only its tested English patterns; it will miss other phrasings by design. Date parsing supports month-name dates, tomorrow, and next weekday, and ignores ambiguous numeric dates. The token estimate is approximate, not an Ollama tokenizer measurement. A Linux sandbox benchmark using `time.perf_counter()` over ten combined extraction/retrieval runs on 500 in-memory confirmed nodes measured a 7.75 ms median and 11.65 ms maximum (one runtime-jitter outlier); these timings are not representative of the target Windows PC.

## Companion conversation and background memory lane — 2026-10-30

At launch, an opted-in `RelationshipBuilder` opens with one deterministic English question about the user's day. This avoids an extra startup inference and removes the availability interview. The ordinary chat model then follows what the user says, with confirmed preferences and My World gaps as optional context for a relevant follow-up. It can continue ordinary chat without a fixed turn cap. First-time memory consent remains an explicit deterministic flow.

The desktop scheduler starts the local memory-review worker only after 90 seconds without chat or model activity. It does not require PC inactivity. Each pass reviews up to 60 retained messages together and can propose at most five evidence-backed notes. New typing or a chat turn cancels the review before persistence and leaves the cursor unchanged. Python checks direct user quotes, sensitivity, duplicates, and current consent before storing anything. The reviewer maps self-reported interests, values, goals, people, experiences, routines, and support preferences; it does not infer diagnoses or fixed psychological traits. Positive and difficult experience notes require a clear user signal that the event mattered.

## My World design rationale

The sections are practical memory shelves, not a personality test. Their shape draws on Seligman's PERMA model (positive emotion, engagement, relationships, meaning, and accomplishment) and Self-Determination Theory's autonomy, competence, and relatedness needs. That led to Interests & enjoyment, Good moments, People & belonging, Values & meaning, Goals & progress, and Support & communication, alongside everyday context and clearly meaningful difficult experiences. McAdams's narrative-identity work also supports remembering selected experiences in the user's own story rather than converting them into fixed trait labels. These theories inform the product's categories; they do not validate this app as a psychological measurement tool.

The owner can inspect, manually add, and remove memories in My World. This follows human-computer interaction research that recommends making conversational memories visible and user-controllable; direct editing can be added as a later UI improvement.

Research sources: [Ryan & Deci (2000), Self-Determination Theory](https://selfdeterminationtheory.org/SDT/documents/2000_RyanDeci_SDT.pdf); [Seligman (2011), What Is Well-Being?](https://www.authentichappiness.sas.upenn.edu/learn/wellbeing); [McAdams (2001), The Psychology of Life Stories](https://doi.org/10.1037/1089-2680.5.2.100); [Huang et al. (2023), Memory Sandbox](https://arxiv.org/abs/2308.01542).

## Phase 2 preview

## Phase 2 proactive decision flow

`DesktopApp._scheduler_tick` is the only GUI policy entry point and still runs on the existing five-second Tk tick. It gathers the already-filtered `ContextEngine` summary and UI state, then calls `InitiativePolicy.evaluate(now, ctx, state)` once. Explicit user-created reminders and task-due notifications remain on their original path. The root-level `chat_cli.py` uses `app/initiative.py` as a compatibility shim and passes `cli_mode=True`, so CLI decisions are limited to open-loop and session-return templates.

The policy applies hard gates before scoring: proactive/fact/rhythm consent, explicit/default quiet hours, pause and 24-hour “leave me alone”, active UI/no typing, no dialog or background title worker, private/meeting/self-window exclusion, a shared 60-minute global cooldown, the shared 0–5 daily cap, and trigger-specific cooldowns. Eligible triggers are scored as:

`score = base_value × acceptance(trigger, 3-hour bucket) × timing_fit − annoyance_cost`

`base_value`, `trigger_threshold`, `acceptance_floor`, cooldowns, and annoyance weights live in `config/companion.json`. Acceptance is Beta(α, β) with prior α=β=1; replies add success weight, active ignores add 0.3 failure weight, and explicit negative feedback adds 2. Two active ignores halve the trigger rate for seven days; three negative reactions persistently disable it until the student re-enables it. Away-from-PC observations create `nudge_unseen`, not a negative statistic.

`OpenLoopTracker` syncs only confirmed/consented deadline nodes and open tasks with due timestamps. It emits at most one nudge per scheduled step (3 days, 1 day, morning-of); missed windows are skipped, and morning-of uses a confident rhythm window or the configured 09:00 default. Existing due reminders are not duplicated. The deterministic dialog is installed only after the nudge is visible; yes hands one pre-written user request to normal chat, while later/stop remain model-free.

`RhythmTracker` is opt-in and OFF by default. Its rows contain only `(dow, hour, app_category, minutes, samples, updated_ts)`; `rhythm_days` tracks distinct active days for confidence gating. A 14-day half-life is applied lazily. Rhythm affects timing only and is never added to the chat prompt. Patterns are read-only, resettable, and display no guess when confidence is insufficient.

`FeedbackLogger` stores event type, trigger, human-readable reason, nudge ID, and minimal JSON metadata—never nudge bodies or window titles. `PrivacyService` supports rhythm/history/all-proactive deletion, topic/fact cascades, 90-day event retention, and keyword-free consent-ledger audit entries. The persistent `ProactiveRateLimiter` remains the shared source of hashed title-trigger rate state; its daily counter is refreshed before every policy evaluation and can be cleared when the student deletes nudge history.

## Verification boundaries

The earlier Phase 2 sandbox suite reported 361 passed, 0 skipped, 0 failed; the 2026-10-29 stabilization build later reported 369. `scripts/sim_proactive.py` simulates four synthetic students for 30 days with a fake clock and no model/network/thread use. Warm-cache policy timing is measured with `time.perf_counter`; this is a sandbox benchmark, not a Windows target-PC guarantee. The 2026-10-30 companion conversation and background-review changes have not been validated yet. Real Windows/Tkinter/tray/Ollama validation remains a manual step. Phase 3 has not been implemented.
