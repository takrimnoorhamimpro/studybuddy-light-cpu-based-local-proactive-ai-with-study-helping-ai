# v9 Patch Notes — Chat Routing, Context Isolation, UX

## Latency and tool routing

- Default CPU output limit reduced to `num_predict=64`; `num_ctx=3072`, `num_thread=4`, model keep-alive and one primary `llama3.2:3b` model remain.
- Tool routing defaults to editable dictionary hints with BM25 explicit-operation fallback rather than exposing all 14 schemas to every turn.
- Ordinary messages receive `tools=[]` and one normal LLM generation; no extra embedding request runs on the ordinary chat path.
- Common greetings remain local fast paths.

## Conversation and state correctness

- Previous-session transcript is not restored into active prompt history. Old turns remain in SQLite for explicit search only.
- Saved notes/facts are no longer searched and injected for general small talk.
- Completed tasks and task counts query structured database state.
- Operational results and proactive messages are excluded from reusable small-talk history.
- Pending reminder follow-up now gets a timestamp from the tool path and expires after five minutes; stale/malformed pending state is cleared before processing a new message.
- Hazardous ingestion reminders are rejected before scheduling, and the active dialog is cleared on rejection.
- A wrong saved user name is repaired only from a bounded, explicit `I am X, not Y` correction when `Y` matches the currently saved profile. The transcript itself is not restored into the prompt.
- User profile introspection no longer reports the assistant's own name as a fact about the user. Static greeting templates use the configured assistant name; capability prompts no longer hardcode “Milo.”
- If an old profile was corrupted, the startup migration checks only the newest explicit `I am X, not Y` correction and repairs the profile only when `Y` exactly matches the old saved name. It does not restore that old transcript into active context.

## Proactive and desktop context

- Proactive messages receive only the fresh, filtered window title and coarse desktop metadata—not old chats, old assistant guesses, notes, tasks or reminders.
- Generic titles are skipped and unsupported topic claims are dropped; proactive messages are not added to persistent turns or reusable core history.
- Direct desktop questions use live/cached external-window metadata with a stated age; they don't ask the LLM to infer current activity from past chats.
- Persistent cooldown state stores hashes and timestamps, not raw title or generated proactive text.

## Intelligence Center

- Graph descriptions now state the actual memory/proactive boundary.
- Caption says “editable dictionary + BM25 fallback” when configured and shows actual last route/round/tool counters.
- Last request latency is shown from Ollama's latest measured request if an average is not available; counters remain “not yet measured” before the first run.
- Caption wraps to fit the window.

## Validation

- `python -m unittest discover -s app -p 'test_*.py' -q`
- **248 tests passed** in the build environment (some existing tests emit SQLite `ResourceWarning`s for in-memory test connections). No live Windows GUI/Ollama benchmark was run here.
