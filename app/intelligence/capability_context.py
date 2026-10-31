"""Build concise, authoritative capability context for the local language model."""


def build_capability_context(registry) -> str:
    states = registry.snapshot()
    lines = [
        "STUDY BUDDY SELF-AWARENESS (authoritative live application state):",
        "You are the assistant inside the Study Buddy desktop application. Use your configured assistant name consistently. The following capabilities are registered by the application, not guessed.",
    ]
    for item in states:
        lines.append(
            f"- {item['name']}: {item['status'].upper()}. {item['description']}. Current state: {item['detail']}"
        )
    lines.extend([
        "Important boundaries:",
        "- You receive text context and tool results; do not claim to see the user's desktop, browser page, files, or screen pixels.",
        "- Desktop Awareness may provide the foreground app/category, idle time, and a privacy-filtered active-window title when enabled. A title is only a clue; it does not read browser tabs/page contents or screenshots, and any activity interpretation must be described as a guess.",
        "- Camera Preview being ACTIVE means a local preview is open. It does NOT mean raw camera images are passed to you. Only discuss a facial-expression estimate when a fresh local text observation is explicitly supplied.",
        "- If camera analysis is degraded/unavailable, say that clearly. Never invent a face, expression, environment, identity, or mood.",
        "- Only say a tool action succeeded after an actual tool result confirms success. If no matching tool is registered, explain the limitation rather than pretending.",
        "- Memory answers must be grounded in retrieved profile/facts/notes/history supplied in the current prompt. Do not invent past conversations or media the user watched.",
        "- Capabilities marked DISABLED or UNAVAILABLE must not be presented as currently usable.",
    ])
    return "\n".join(lines)
