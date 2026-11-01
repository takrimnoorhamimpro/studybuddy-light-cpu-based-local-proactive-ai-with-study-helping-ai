# Changelog

## Companion conversation update — 2026-10-30

- Replaced the multi-part generated opener with one short English day question. It uses no model call, so the chat is ready sooner and has no availability yes/no step.
- Conversation guidance follows ordinary chat without a forced closing message. After eight user turns, ordinary chat continues without the My World guidance window. My World gaps are optional context for a relevant follow-up, never a checklist.
- Reworked My World into everyday context, interests, goals and progress, values and meaning, people and belonging, good moments, difficult moments, support preferences, and a legacy life-moments area. The reviewer saves only evidence-backed notes and does not infer clinical or fixed personality traits.
- Memory review waits for 90 seconds without chat or model activity, then reviews up to 60 retained messages together. New typing or chat cancels the model request without advancing the review cursor; PC inactivity is not required. A pass may add up to five notes.
- Relationship memory consent, sensitive-content filters, evidence checks, and stop/skip boundaries remain in place. The memory reviewer re-checks consent after its model call before saving.
- Not yet validated on the full Windows GUI or with the local Ollama model after these changes.

## v11 Phase 2 stabilization — 2026-10-29

- Startup now previews one unasked personal topic in the availability invitation; one clear “yes” starts the conversation without a second permission prompt.
- Busy replies such as “no,” “skip,” “I’m focused,” and “don’t disturb me” now resolve to the availability/pause flow. Explicit timed pauses still resume early on the user's next message.
- Busy/context wording no longer starts a pause automatically. A busy reply offers an optional duration choice; a bare pause request asks how long before activating. The choice expires after two minutes without starting a pause. An active pause is confirmed visibly, and any user message or app restart resumes check-ins. Legacy persistent pauses are cleared at launch.
- Negative feedback no longer silently mutes or disables proactive triggers; it is recorded for ranking only. On launch, legacy automatic mutes/pauses and disables without a Settings audit entry are cleared; explicitly disabled triggers remain off.
- Deferring a personal getting-to-know-you question no longer disables separate window-title check-ins for the rest of the app session; explicit proactive pauses still gate those check-ins.
- Title-based check-ins keep using the fresh, sanitized title as the model's only desktop evidence. Delivery is discarded if the foreground window changes, the user starts typing, quiet hours begin, or the context becomes private/idle while the model is responding.
- Current-window questions now send the fresh snapshot to the chat model and skip the tool orchestrator when no additional tool is needed. Title-grounded answers are checked for unsupported claims and repaired from the supplied evidence.
- Proactive title generation now uses a compact, persona-aware prompt and returns only a short activity guess. The app validates that guess against the title and adds the availability/pause wording itself, avoiding unsupported claims and keeping the 3B prompt small.
- Window title parsing now recognizes Unicode titles and ignores common empty-tab/editor titles. A pending title is limited to one relevant reply and expires after five minutes; unrelated requests do not inherit it.
- Idle My World review now requests schema-constrained JSON from Ollama when supported, validates a label against its quoted user evidence, rejects known filler/meta-chat, and requires concrete significance before saving a life moment. Legacy filler episodes are marked expired rather than deleted.
- Added explicit uncertainty and evidence-grounding rules to the main chat prompt, especially for window titles and factual claims that no local tool verified.
- Validation after these stabilization edits: **369 tests passed, 0 skipped, 0 failed** under Python 3.12; syntax compilation and the 30-day proactive simulation also pass. A local `llama3.2:3b` title-check-in smoke call completed in 12.6 seconds warm on this PC. Full GUI/tray interaction still needs a manual run.

## v11 Phase 0 — 2026-10-27

- Added backup-first, versioned companion database migrations (`PRAGMA user_version`).
- Added deterministic consent onboarding states with English-only dialog vocabulary.
- Added claim/ack follow-up delivery, stale-claim recovery, pending-kind deduplication, and unknown-kind isolation.
- Added `PrivacyService` topic-forget cascade across conversation and companion records.
- Made lifecycle personality-version paths profile-aware.
- Added Phase 0 regression tests and a test runner that explicitly reports GUI skips when Tkinter is unavailable.
- Corrected README/version documentation; no live Windows/Ollama certification claimed.

## v11 Phase 0 review fixes — 2026-10-27

- Onboarding now expires after five minutes of inactivity and releases normal questions/requests to the normal chat pipeline; a second ambiguous reply also releases the dialog.
- Name collection strips common prefixes and accepts only plausible 1–3-word names (maximum 40 characters); a second explicit YES is still required to save.
- Removed `hmm` from shared affirmative phrases so it cannot confirm actions.
- `forget_topic()` now preserves tasks/reminders by default; added read-only `preview_forget()` counts and `include_tasks=True` opt-in. The GUI uses a separate confirmation for task/reminder deletion.
- Made `has_pending` clear expired onboarding state on access, even if no new message arrives.
- Enforced English-only phrase lists and intent keyword groups, with an explicit allow-list regression test.
- Updated existing vocabulary tests to English equivalents; safety-specific language tests were left unchanged.
- Full suite: 275 passed, 0 skipped, 0 failed in the sandbox.


## v11 Phase 1 — Memory That Matters

- Added additive, backup-first companion schema migration v2 and `candidate_queue`.
- Added deterministic confirmed-memory retrieval with relevance ranking, expiry/sensitivity/consent gates, usage tracking, and a bounded PersonaContext.
- Added rule-based English-only candidate extraction with conservative sensitive-content rejection, deterministic date parsing, TTLs, and deduplication.
- Added batched deterministic consent dialogs with YES / selected-item confirmation / NO / defer, idle gates, cooldowns, daily caps, expiry, and rejection fatigue pause.
- Added per-call response modes and `num_predict` overrides without changing global options or enabling GPU use.
- Added deterministic personal-memory transparency replies, a facts-consent setting with ledger entries, and source/status/expiry metadata in My World.
- Added shared prompt-budget estimation and a worst-case prompt-budget script.
- Kept proactive generation memory-free; no additional LLM calls, embeddings, network services, or new threads were added.
- Verification is limited to sandbox tests and compilation; real Windows/Tkinter/Ollama behavior still requires manual testing.

- Final Phase 1 verification: 320 tests passed, 0 skipped, 0 failed in the sandbox; Windows/Tkinter/Ollama remains unverified.


## v11 Phase 2 — A Companion That Reaches Out Well

- Added `OpenLoopTracker`, opt-in title-free `RhythmTracker`, `FeedbackLogger`, deterministic nudge templates, and a unified `InitiativePolicy`; `app/initiative.py` is now the compatibility shim used by the root-level CLI.
- Routed deadline, usual-study-time, session-return, wind-down, and title-clue decisions through one gated policy. The existing title-only LLM path retains its evidence restrictions and remains the only background nudge model call.
- Added claim/display/ack delivery, deterministic `nudge_checkin` and rhythm-consent dialogs, a Why explanation, feedback adaptation, trigger toggles, 0–5 daily caps for non-title initiatives, Patterns, and a last-20 Nudge log. Routine triggers use per-trigger repeat rules; window-title check-ins are deduplicated by stable title per app session and have no blanket hourly cooldown.
- Added schema v3 with additive columns/indexes and a title-free `rhythm_days` support table; migration is backup-first and clears legacy sensitive evidence while retaining labels. Forgetting a fact cascades to its open loop and related event metadata.
- Fixed Phase 1 review findings A1–A8: sensitive evidence rejection/cleanup, instruction-like preference filtering at extraction and render time, second-person false positives, deadline/preposition grammar, natural consent phrases/out-of-range hints, name blocklist, information-question response mode, and current version/test count.
- Added the deterministic 30-day `scripts/sim_proactive.py` harness. No new dependencies, network calls, embeddings, resident models, or Phase 2 LLM calls were added.
- Final Phase 2 sandbox verification: **361 tests passed, 0 skipped, 0 failed**. The Linux sandbox cannot verify real Windows/Tkinter/tray/Ollama behavior; see the README manual-test checklist.
