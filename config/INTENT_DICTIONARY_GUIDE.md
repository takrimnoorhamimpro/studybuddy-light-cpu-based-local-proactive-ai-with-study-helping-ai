# Editing Milo's intent dictionary

The user-editable vocabulary and dialog text are in **`config/intent_dictionary.json`**. In normal use, you should edit that file rather than `app/chat_core.py` or `app/intent_router.py`.

## What lives in the JSON file?

- `yes_phrases`, `no_phrases`, `cancel_phrases`, `skip_phrases`: accepted English replies to an active dialog.
- `global_exclusions`: educational or negated phrases that should normally go to chat instead of opening an action workflow.
- `intents[].keyword_groups`: every group must match for that intent to be a candidate; synonyms inside one group are alternatives.
- `intents[].exclude_phrases`: additional exceptions for one intent.
- `intents[].direct_patterns`: regex examples of a complete direct command. Those commands bypass confirmation and continue through the normal command/tool route.
- `confirm_prompt`, `details_prompt`, `retry_prompt`: Milo's wording for the interaction.
- `time_patterns` and `time_leading_phrases`: configurable phrases used to detect reminder times.

## How the groups work

Example for adding tasks:

```json
"keyword_groups": [
  ["add", "create", "make", "put", "save", "jot down"],
  ["task", "tasks", "todo", "to-do", "task list"]
]
```

At least one term from the first group **and** at least one term from the second group must appear. `Add task: Finish assignment` is a direct command and skips the confirmation prompt. `I need to add a task` is incomplete, so Milo asks whether that is what you mean. On YES, it asks for a title; on NO, it sends the conversation to the LLM without exposing action tools for that turn.

## Adding or removing vocabulary

1. Make a backup of `intent_dictionary.json`.
2. Add synonyms to the existing arrays or add an intent entry following the same structure.
3. Keep `global_exclusions` and `exclude_phrases` for educational/negated phrases to avoid false positives.
4. Validate that the file is still valid JSON. Restart Study Buddy to reload the dictionary.
5. Run `python -m unittest discover -s app -p "test_*.py"` from the project folder.

Use simple synonyms in `keyword_groups`. Only edit `direct_patterns` if you need to change which complete requests bypass confirmation. Regex patterns must be valid Python regular expressions encoded as JSON strings (so each backslash is written as `\\`).

The generic matching/state machine is implemented in **`app/intent_router.py`**. Confirmed task/reminder/timer actions and existing validated local handlers are coordinated in **`app/chat_core.py`**.
