# Study Buddy CPU Optimized v9 — Architecture

## Core principles

1. One primary reasoning model: `llama3.2:3b` through Ollama.
2. Let the LLM choose among native tools when a request has a genuine tool route; Python owns execution, validation and truth.
3. Don't give ordinary chat every tool schema. Don't run a separate embedding model on the normal critical path.
4. Keep persistent state in SQLite, but don't dump old turns/facts into every prompt.
5. Proactive behavior is based on fresh desktop evidence, not on past conversations or on previous assistant guesses.
6. Prefer measurable runtime state over illustrative claims in the Intelligence Center.

## Normal chat vs tool request

`handle_message` first handles safety, active dialog states, authoritative desktop/name/greeting queries, and explicit local command paths. If the request is normal conversation, the request-scoped tool router returns no schemas and the primary model gets a single regular generation. The current system uses `num_ctx=3072`, `num_predict=64`, `num_thread=4` as a starting profile and keeps the model warm with Ollama keep-alive. CPU timing varies with the specific 8th-gen i5 variant, thermal limits, background applications and context length.

For likely tool requests, `config/intent_dictionary.json` contains `tool_route_hints` and `tool_route_fallback_patterns`. Explicit phrase/group matches choose a compact set of tool schemas. BM25 is a fallback for clearly operational wording. The LLM still decides which tool to call and creates arguments. The Python executor validates tool names and arguments, executes the allow-listed function and returns the actual result. Successful common operations can produce a deterministic concise reply without a second paraphrase generation.

## Dialog workflows

The vocabulary is editable in `config/intent_dictionary.json`; generic matching/state-machine logic lives in `app/intent_router.py`. An unambiguous direct command skips confirmation. A likely but incomplete action asks for YES/NO; YES collects required details, NO sends the original request to ordinary chat with tools disabled for that turn, and CANCEL clears the workflow. Open-task selection uses real database task IDs, not model-invented titles. A pending reminder detail flow expires after five minutes and cannot absorb later small talk indefinitely.

## Memory policy

A new app session starts without restoring old conversational turns. This avoids irrelevant past content affecting ordinary replies. SQLite still stores turns, facts and notes for explicit memory/history requests. Structured task counts and completed-task answers query the task table directly. Operational outputs are kept out of reusable small-talk history. The `get_user_profile` tool exposes user-owned fields only; the assistant name is not a user fact.

For one specific profile repair, startup scans a bounded set of retained user turns for an explicit `I am X, not Y` correction where `Y` exactly matches the currently stored user name. It only updates the name field; it does not restore any transcript into the model prompt.

## Proactive engine

The background generator receives one fresh, privacy-filtered desktop context snapshot containing coarse app/category/title/idle metadata. It does **not** receive old chats, the previous assistant guess, reminders, tasks or notes. Titles without sufficiently specific topic evidence are skipped. A generated message is dropped if its topic claims cannot be grounded in the title. The message is not appended to normal chat history or persistent turns. The persistent rate limiter stores title/message hashes and timestamps rather than raw title or generated text. Proactive inference yields to user chat and respects quiet hours, stability and cooldown settings.

This is deliberately less “personalized” than past-chat-based prompting, but it avoids the feedback loop where an earlier guess is treated as evidence for a later guess. Personal history remains available when the user explicitly asks for it.

## Desktop answers

Questions such as “What is the title of my current tab?” use the desktop provider's live privacy-filtered observation. If the Study Buddy window is foreground, the explicit desktop query may cite a recent in-memory observation of the last external window and state its age. It does not read actual browser page content, video pixels or screen images. If the context provider fails, the response says that the title could not be verified rather than guessing from chat history.

## Intelligence Center

`app/intelligence/dashboard.py` contains one canonical `ARCHITECTURE_NODES` graph used for drawing and node details. It visualizes the single primary model, intent/dialog layer, validated tool executor, SQLite state, desktop signals, proactive engine and optional camera/voice IO. The caption reads the active model name, routing mode, last actually routed tools, last tool-call/round counters and actual Ollama request timing where available. Before any inference, it displays “not yet measured.”

## Safety and known limits

- Hazardous ingestion reminders are rejected in both conversational and tool pathways. This is a narrow reminder guard, not a general content classifier.
- User-owned tasks, reminders and notes still require database/handler success before the assistant claims completion.
- Normal questions still require one Llama 3.2 3B generation and can be slow on CPU. `num_predict=64` limits output work but may shorten answers; increase it if needed.
- Live Windows GUI and actual Ollama throughput must be benchmarked on the target machine. Automated tests alone do not prove real-device latency or desktop-capture correctness.
