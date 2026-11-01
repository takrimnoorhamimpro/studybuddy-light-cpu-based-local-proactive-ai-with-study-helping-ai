"""Safe defaults and validation for Phase 1 companion tunables."""
import json, os

DEFAULTS = {
    "persona_budget_tokens": 200, "max_injected_items": 4,
    "relevance_min_overlap": 0.12, "always_on_preference_items": 2,
    "consent_idle_seconds": 120, "consent_prompt_cooldown_seconds": 1800,
    "consent_daily_cap": 3, "candidate_queue_max": 5,
    "ttl_days_by_kind": {"deadline":45,"area":45,"goal":90,"schedule":90,"preference":None,"interest":None},
    "rejected_cooldown_days": 90,
    "response_modes": {"quick":64,"chat":110,"deep":170},
    "prompt_token_limit": 1800,
    "initiative_quiet_default": "23:00-07:00", "loop_escalation_days": [3, 1, 0],
    "loop_morning_default": "09:00", "trigger_base_value": {"open_loop_due": 1.0, "usual_study_start": 0.7, "session_return": 0.35, "wind_down": 0.2, "title_clue": 1.0},
    "trigger_threshold": 0.05, "acceptance_floor": 0.15, "annoyance_per_nudge": 0.08, "annoyance_per_ignore": 0.12, "trigger_cooldown_minutes": {"open_loop_due":0,"usual_study_start":1380,"session_return":1380,"wind_down":1380,"title_clue":0}, "ignore_window_minutes": 15,
    "reply_window_minutes": 10, "events_retention_days": 90, "rhythm_half_life_days": 14,
    "rhythm_min_days": 5, "rhythm_min_minutes": 30, "daily_cap_default": 5,
}

def _bounded_int(value, default, low, high):
    try: return max(low,min(high,int(value)))
    except (TypeError,ValueError,OverflowError): return default

def _bounded_float(value, default, low, high):
    try: return max(low,min(high,float(value)))
    except (TypeError,ValueError,OverflowError): return default

def load_config(path):
    config=dict(DEFAULTS)
    config["ttl_days_by_kind"]=dict(DEFAULTS["ttl_days_by_kind"])
    config["response_modes"]=dict(DEFAULTS["response_modes"])
    try:
        with open(path,encoding="utf-8") as handle:
            loaded=json.load(handle)
        if isinstance(loaded,dict):
            config.update({k:v for k,v in loaded.items() if k in DEFAULTS})
            config["ttl_days_by_kind"]=dict(DEFAULTS["ttl_days_by_kind"])
            config["response_modes"]=dict(DEFAULTS["response_modes"])
            for key,default in DEFAULTS["ttl_days_by_kind"].items():
                value=(loaded.get("ttl_days_by_kind") or {}).get(key,default) if isinstance(loaded.get("ttl_days_by_kind",{}),dict) else default
                if value is None: config["ttl_days_by_kind"][key]=None
                else: config["ttl_days_by_kind"][key]=_bounded_int(value,default or 45,1,3650)
            for key,default in DEFAULTS["response_modes"].items():
                config["response_modes"][key]=_bounded_int((loaded.get("response_modes") or {}).get(key,default) if isinstance(loaded.get("response_modes",{}),dict) else default,default,16,512)
    except (OSError,ValueError,TypeError):
        pass
    config["persona_budget_tokens"]=_bounded_int(config.get("persona_budget_tokens"),200,0,500)
    config["max_injected_items"]=_bounded_int(config.get("max_injected_items"),4,0,12)
    try: config["relevance_min_overlap"]=max(0.01,min(1.0,float(config.get("relevance_min_overlap",0.12))))
    except (TypeError,ValueError): config["relevance_min_overlap"]=0.12
    config["always_on_preference_items"]=_bounded_int(config.get("always_on_preference_items"),2,0,2)
    config["consent_idle_seconds"]=_bounded_int(config.get("consent_idle_seconds"),120,120,86400)
    config["consent_prompt_cooldown_seconds"]=_bounded_int(config.get("consent_prompt_cooldown_seconds"),1800,1800,604800)
    config["consent_daily_cap"]=_bounded_int(config.get("consent_daily_cap"),3,1,3)
    config["candidate_queue_max"]=_bounded_int(config.get("candidate_queue_max"),5,1,5)
    config["rejected_cooldown_days"]=_bounded_int(config.get("rejected_cooldown_days"),90,1,3650)
    config["prompt_token_limit"]=_bounded_int(config.get("prompt_token_limit"),1800,256,1800)
    config["trigger_threshold"]=_bounded_float(config.get("trigger_threshold"),0.05,0.0,10.0)
    config["acceptance_floor"]=_bounded_float(config.get("acceptance_floor"),0.15,0.01,1.0)
    for key,default,low,high in (("events_retention_days",90,1,3650),("rhythm_half_life_days",14,1,365),("rhythm_min_days",5,5,60),("rhythm_min_minutes",30,1,10000),("daily_cap_default",5,0,5),("ignore_window_minutes",15,1,120),("reply_window_minutes",10,1,120)):
        config[key]=_bounded_int(config.get(key),default,low,high)
    if not isinstance(config.get("loop_escalation_days"),list): config["loop_escalation_days"]=[3,1,0]
    else: config["loop_escalation_days"]=sorted({max(0,min(365,int(x))) for x in config["loop_escalation_days"] if str(x).lstrip('-').isdigit()},reverse=True) or [3,1,0]
    if not isinstance(config.get("trigger_cooldown_minutes"),dict): config["trigger_cooldown_minutes"]={}
    if not isinstance(config.get("trigger_base_value"),dict): config["trigger_base_value"]={}
    for key,default in DEFAULTS["trigger_cooldown_minutes"].items():
        source=config.get("trigger_cooldown_minutes",{})
        config["trigger_cooldown_minutes"][key]=_bounded_int(source.get(key,default),default,0,10080)
    for key,default in DEFAULTS["trigger_base_value"].items():
        source=config.get("trigger_base_value",{})
        config["trigger_base_value"][key]=_bounded_float(source.get(key,default),default,0.0,10.0)
    config["annoyance_per_nudge"]=_bounded_float(config.get("annoyance_per_nudge"),0.08,0.0,10.0)
    config["annoyance_per_ignore"]=_bounded_float(config.get("annoyance_per_ignore"),0.12,0.0,10.0)
    try:
        start,end=config["initiative_quiet_default"].split("-",1)
        import datetime
        datetime.datetime.strptime(start,"%H:%M"); datetime.datetime.strptime(end,"%H:%M")
    except Exception: config["initiative_quiet_default"]="23:00-07:00"
    try:
        import datetime
        datetime.datetime.strptime(str(config.get("loop_morning_default","09:00")),"%H:%M")
    except Exception: config["loop_morning_default"]="09:00"
    return config
