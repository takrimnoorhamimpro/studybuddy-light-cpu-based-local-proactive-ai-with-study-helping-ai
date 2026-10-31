"""Deterministic, neutral nudge wording. No LLM or personal chat text."""
import hashlib
BANNED_PHRASES = ("you haven't", "you still haven't", "don't forget", "last chance", "hurry", "you should have", "i missed you", "where were you")
TEMPLATES = {
 "open_loop_due": {
  3: ("Your {title} is in 3 days. Want to plan some revision? You can say yes, later, or stop reminding me.", "Your {title} is coming up in 3 days. Would a short revision plan help? Say yes, later, or stop reminding me."),
  1: ("Your {title} is tomorrow. Would you like help choosing a manageable next step? Say yes, later, or stop reminding me.", "Your {title} is coming up tomorrow. Want to make a simple plan? Say yes, later, or stop reminding me."),
  0: ("Your {title} is today. Would a quick review plan be useful? Say yes, later, or stop reminding me.", "It's the day of your {title}. I can help make a short checklist if useful. Say yes, later, or stop reminding me."),
 },
 "usual_study_start": {0: ("Are you free for a short study session, or are you busy right now? Either answer is okay.", "Would a short study session fit right now, or should I leave you to it?")},
 "session_return": {0: ("Welcome back. Are you free for a quick chat or study session, or are you busy? Either answer is okay.", "Hi again. Do you have a minute to chat or study together, or should I leave you to it?")},
 "wind_down": {0: ("It's getting late. If you're winding down, consider saving your work and getting some rest.", "A gentle late-night note: a stopping point and some rest can be useful.")},
}
def render_template(trigger, step=0, *, title="your deadline", loop_id=""):
    choices=TEMPLATES.get(trigger, {}).get(int(step), TEMPLATES.get(trigger, {}).get(0, ()))
    if not choices: return None
    index=int(hashlib.sha256(f"{loop_id}|{trigger}|{step}".encode()).hexdigest()[:8],16)%len(choices)
    return choices[index].format(title=str(title or "your deadline")[:80])
