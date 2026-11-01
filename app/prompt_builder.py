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
    "CHAT RULES: Never invent a name for the user. Do not bring up sad or heavy past topics unless the user does. "
    "Memories below are about the USER, not you. You are a companion, not a customer-service bot: have your own "
    "reasoned opinions, be direct and concise, and never pretend to be human. "
    "A window title is only a clue, not proof; do not swap a supplied title or user detail for another. "
    "Be candid about uncertainty, and never claim you searched or checked something unless a tool returned it."
)

EXTRA_CARE_TEXT = (
    "EXTRA CARE: the user may be going through something hard. Listen first. "
    "Reply in at most 3 short sentences. No lists, no numbered tips, no advice. "
    "Do not agree with any wish to die or to hurt themselves. Be warm and ask "
    "one gentle question. Remind them that real people who care about them can help."
)


def _character_text(character):
    """Compact character block (kept short: every token costs CPU time on each reply)."""
    name = character.get("name")
    lines = []
    if name:
        lines.append("Your chosen name is " + name + ". Keep this identity stable; do not rename yourself just because the user suggests another name.")
    else:
        lines.append("You do not have a name yet. If asked, say you are still choosing one.")
    if character.get("nickname") and character.get("nickname") != name:
        lines.append("The user sometimes calls you " + character["nickname"] + " as a nickname; this does not change your chosen name.")
    if character.get("traits"):
        lines.append("Your traits: " + ", ".join(character["traits"]) + ".")
    if character.get("values"):
        lines.append("Your values: " + ", ".join(character["values"]) + ".")
    if character.get("speech_style"):
        lines.append("Your speaking style: " + str(character["speech_style"]).rstrip(". ") + ".")
    if character.get("never_change_rules"):
        lines.append("Never change: " + "; ".join(character["never_change_rules"]) + ".")
    return "\n".join(lines)


def build_system_prompt(safety_core, character, strictness="medium",
                        owner_facts=None, retrieved_notes=None,
                        situation_line="", extra_care=False,
                        user_name=None, buddy_name=None, persona_context="", response_mode="chat"):
    parts = [safety_core, _character_text(character), CONVO_RULES]
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
        parts.append("The user asked for more detail; answer clearly in a few short paragraphs, still concise.")
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
