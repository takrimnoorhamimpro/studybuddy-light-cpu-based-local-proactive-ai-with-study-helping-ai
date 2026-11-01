"""Compatibility shim for the unified companion InitiativePolicy."""
import datetime, time
class InitiativeEngine:
    """Preserve the public CLI API while delegating production decisions to policy."""
    def __init__(self, core, monitor, settings=None, clock=time.time):
        self.core=core; self.monitor=monitor; self.settings=settings or {}; self.clock=clock
        self.last_nudge=0.0; self.last_signature=None; self.last_signature_since=clock(); self.daily_count=0
        self.last_interaction_ts=clock(); self._pending_decision=None
        try: self.last_nudge=float(core.personal_companion.setting("initiative_last_nudge_ts","0") or 0)
        except Exception: self.last_nudge=0.0
        self.day=datetime.date.fromtimestamp(clock()).isoformat(); self.dismiss_until=0.0
        self.policy=getattr(core,'initiative_policy',None)
    def _roll_day(self):
        day=datetime.date.fromtimestamp(self.clock()).isoformat()
        if day!=self.day: self.day=day; self.daily_count=0
    def check_once(self):
        now=self.clock(); self._roll_day()
        if self._pending_decision is not None: return None
        if self.policy is not None:
            snap=self.monitor.snapshot() if self.monitor else {}
            decision=self.policy.evaluate(now=now,ctx={'app_category':'Other'},state={
                'last_interaction_ts':self.last_interaction_ts,'last_nudge_ts':max(self.last_nudge,float(self.core.personal_companion.setting('initiative_last_nudge_ts','0') or 0)),'pause_until':self.dismiss_until,'cli_mode':True,
                'session_return':bool(snap.get('session_return')),'proactive_enabled':self.settings.get('proactive_enabled',True),
                'daily_cap':int(self.settings.get('initiative_daily_cap',3))})
            if not decision or decision.needs_llm: return None
            self._pending_decision=decision
            return decision.text
        # Tiny test-double/legacy compatibility path only. Real ChatCore always has
        # the unified policy when it is constructed with its normal local Memory.
        s=self.monitor.snapshot() if self.monitor else {}
        if not s.get('supported') or s.get('private') or s.get('meeting'):
            self.last_signature=None; return None
        signature=(s.get('app_name'),s.get('category'),tuple(s.get('audio_apps',[])))
        if signature!=self.last_signature:
            self.last_signature=signature; self.last_signature_since=now; return None
        if s.get('app_seconds',0)<int(self.settings.get('proactive_app_seconds',25)): return None
        if now-self.last_signature_since<int(self.settings.get('proactive_stability_seconds',20)): return None
        if now<self.dismiss_until or now-self.last_nudge<int(self.settings.get('proactive_cooldown_seconds',1800)) or self.daily_count>=int(self.settings.get('proactive_daily_limit',3)): return None
        self.last_nudge=now; self.daily_count+=1
        # Deliberately do not feed prior chat history into proactive generations.
        situation=self.monitor.situation_line(s)
        context=("Current locally observed desktop context (limited signal): "+situation+"\n"
                 "This signal identifies only coarse foreground app/category and does not reveal page contents or intent.")
        try:
            if hasattr(self.core,'generate_proactive_message'): reply=self.core.generate_proactive_message(context)
            else: reply=self.core.llm.chat([{"role":"system","content":"Warm, curious companion. English only; be honest about limited evidence."},{"role":"user","content":"Start a brief check-in based only on this limited signal. Do not guess page contents or reuse chat history. Output only the message.\n"+context}]).strip()
        except Exception: return None
        return str(reply).strip() if reply and str(reply).strip() else None
    def acknowledge_shown(self):
        decision=self._pending_decision
        if not decision or self.policy is None: return False
        now=self.clock()
        # CLI caller acknowledges only after the nudge has been printed.
        self.core.history.append({"role":"assistant","content":decision.text})
        if getattr(self.core,"memory",None): self.core.memory.add_turn("assistant",decision.text)
        self.core.last_initiative_nudge_id=decision.nudge_id
        self.core.last_initiative_nudge_text=decision.text
        self.policy.record_shown(decision,now); self.last_nudge=now; self.daily_count+=1
        self.core.personal_companion.set_setting("initiative_last_nudge_ts",str(now))
        if decision.trigger=='open_loop_due' and getattr(self.core,'intent_router',None):
            title=decision.meta.get('title','your deadline')
            self.core.intent_router.start_nudge_checkin(nudge_id=decision.nudge_id,trigger=decision.trigger,
                loop_id=decision.meta.get('loop_id'),suggested_message=f"Help me plan revision for my {title}.")
        self._pending_decision=None
        return True
    def mark_interaction(self): self.last_interaction_ts=self.clock()
    def dismiss(self,minutes=60): self.dismiss_until=self.clock()+max(1,minutes)*60
