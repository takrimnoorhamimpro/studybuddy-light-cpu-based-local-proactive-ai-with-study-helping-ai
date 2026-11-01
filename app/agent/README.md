# Milo tool-calling agent

Study Buddy uses Ollama's native chat tool-calling protocol with request-scoped tool selection. See [`../../CPU_OPTIMIZED_ARCHITECTURE.md`](../../CPU_OPTIMIZED_ARCHITECTURE.md) for the CPU profile, benchmark flow and design rationale.

## Modules

- `tool_registry.py`: allow-listed tool definitions, optional schema filtering and argument validation.
- `tool_orchestrator.py`: intent routing, bounded model/tool loop, verified single-action replies and execution telemetry.
- `builtin_tools.py`: adapters for local memory, profile, tasks, reminders, timers, camera status, desktop context and capability inspection.

## Runtime boundaries

The model cannot execute Python, SQL, shell commands or arbitrary paths. Only registered handlers can run. Camera activation remains behind the explicit-consent UI flow; the camera status tool returns only active/disabled state and local expression-feature text, never raw frames. Desktop context reports coarse foreground-app and idle-time values, not screen pixels or page contents. Voice capture, backup restore and other direct UI workflows stay behind their existing controls.

## Tool loop safeguards

- Ordinary chat receives no tool schemas; recognized requests receive only a relevant subset.
- Arguments are validated against the registered JSON schemas.
- Common, single-action utility results can be formatted without another model inference. Memory search, introspection and multi-tool requests may need a final model pass.
- Rounds and calls are bounded. Action success is reported only after a confirming tool result.
- Telemetry omits arguments and returned private content. Ambiguous task references do not modify multiple tasks.
- Memory search is limited to this Study Buddy profile's local database; it does not access other applications' chats.
