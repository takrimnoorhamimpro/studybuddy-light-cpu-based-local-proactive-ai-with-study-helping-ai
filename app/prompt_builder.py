"""
prompt_builder.py  -  The "assembly line" that builds what the model reads.

Order (top = strongest):
  1. Safety core (fixed)       2. Language rule (fixed)
  3. Character sheet           4. Strictness (how firmly to disagree)
  5. Owner facts               6. Retrieved memories
  7. Situation line            8. Recent turns, then the new message

Keep it SHORT. Small models get confused by long prompts.
"""

from identity import name_line  # noqa: E402

STRICTNESS_TEXT = {
    "low": "When you disagree, say it gently: 'I see it differently, but it's your call.'",
    "medium": "When you disagree, say so clearly and give your reason, but stay kind.",
    "high": "When you disagree, be firm. Say it once more if needed. Do not pretend to agree.",
}

CONVO_RULES = (
    "CHAT: Warm, concise, and led by the user's conversation. "
    "Use the visible dialogue as continuous context: resolve brief follow-ups from the preceding turns, "
    "and when asked about something you just said, explain the evidence or reasoning behind that message. "
    "Do not answer such a follow-up with a generic disclaimer or act as if your previous message was not said. "
    "Use only the conversation and retrieved records as evidence about the user; do not invent their interests, favorites, history, or experiences. "
    "If the evidence is missing, say you do not know yet. Keep advice consistent with the platform or situation the user already specified. "
    "Never claim unprovided actions or observations. "
    "Treat window titles as uncertain clues. Respect boundaries."
)

EXTRA_CARE_TEXT = (
    "EXTRA CARE: the user may be going through something hard. Listen first. "
    "Reply in at most 3 short sentences. No lists, no numbered tips, no advice. "
    "Do not agree with any wish to die or to hurt themselves. Be warm and ask "
    "one gentle question. Remind them that real people who care about them can help."
)


def _character_text(character):
    traits = [str(value).strip() for value in character.get("traits", [])[:5] if str(value).strip()]
    aliases = {
        "respect for boundaries": "respect boundaries",
        "real friendships matter more than chatting with me": "real-life friendships",
        "reasoned disagreement": "independent opinions",
    }
    values = [aliases.get(str(value).strip().casefold(), str(value).strip())
              for value in character.get("values", [])[:4] if str(value).strip()]
    if not traits and not values:
        return ""
    return "PERSONALITY: " + ", ".join(traits) + ("; values: " + ", ".join(values) if values else "")


def build_system_prompt(safety_core, character, strictness="medium",
                        owner_facts=None, retrieved_notes=None,
                        situation_line="", extra_care=False,
                        user_name=None, buddy_name=None, persona_context="", response_mode="chat"):
    parts = [safety_core, _character_text(character), CONVO_RULES]
    if strictness != "medium":
        parts.append(STRICTNESS_TEXT.get(strictness, STRICTNESS_TEXT["medium"]))

    names = name_line(user_name, buddy_name)
    if names:
        parts.append(names)
    if owner_facts:
        parts.append("Things you know about the user (these are about THEM, not you; "
                     "in these notes 'I' and 'my' mean the USER):\n- " + "\n- ".join(owner_facts))
    if retrieved_notes:
        parts.append("Relevant memories (about the user):\n- " + "\n- ".join(retrieved_notes))
    if persona_context:
        parts.append(str(persona_context).strip())
    if response_mode == "deep":
        parts.append("Complete the current thought clearly in a few short paragraphs, still concise. If the preceding assistant answer was unfinished, continue it instead of restarting.")
    if situation_line:
        parts.append("Situation: " + situation_line)
    if extra_care:
        parts.append(EXTRA_CARE_TEXT)
    return "\n\n".join(parts)


def build_messages(system_prompt, recent_turns, user_text, max_turns=6):
    """recent_turns = list of {"role": "user"/"assistant", "content": "..."}.
    Only the last max_turns*2 messages are kept."""
    keep = recent_turns[-(max_turns * 2):] if max_turns > 0 else []
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(keep)
    messages.append({"role": "user", "content": user_text})
    return messages
