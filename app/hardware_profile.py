"""
hardware_profile.py  -  Detects this PC's CPU / RAM and tunes Ollama (CPU-only) to match.

Two layers:
  1. detect() + recommend()   - instant, no model call. Picks num_thread and num_ctx
                                 from physical cores and free RAM.
  2. calibrate()              - optional one-time benchmark (button in the chat window).
                                 Tries a few thread counts on the real model and saves the
                                 fastest to config/hardware_tuning.json. Re-used on later
                                 launches while the hardware fingerprint is unchanged.

No new dependency: uses psutil if it happens to be installed, otherwise the OS APIs.
GPU stays disabled (num_gpu=0) exactly like llm.py enforces.
Turn it all off with  "auto_hardware_tuning": false  in config/settings.json.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import time

TUNING_FILE = "hardware_tuning.json"
_CACHE = {}


# ----------------------------------------------------------------- detection
def _gb(n_bytes):
    return round(float(n_bytes) / (1024 ** 3), 2)


def _windows_memory():
    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stat = MEMSTAT()
    stat.dwLength = ctypes.sizeof(MEMSTAT)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return None, None
    return stat.ullTotalPhys, stat.ullAvailPhys


def _windows_physical_cores():
    class SLPI(ctypes.Structure):
        _fields_ = [("ProcessorMask", ctypes.c_size_t), ("Relationship", ctypes.c_ulong),
                    ("Reserved", ctypes.c_ulonglong * 2)]
    kernel = ctypes.windll.kernel32
    length = ctypes.c_ulong(0)
    kernel.GetLogicalProcessorInformation(None, ctypes.byref(length))
    count = length.value // ctypes.sizeof(SLPI)
    if count <= 0:
        return None
    buf = (SLPI * count)()
    if not kernel.GetLogicalProcessorInformation(buf, ctypes.byref(length)):
        return None
    cores = sum(1 for entry in buf if entry.Relationship == 0)  # RelationProcessorCore
    return cores or None


def _windows_cpu_name():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except Exception:
        return ""


def _linux_memory():
    info = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
    return info.get("MemTotal"), info.get("MemAvailable", info.get("MemFree"))


def _linux_cpu():
    name, cores = "", set()
    phys = core = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if key == "model name" and not name:
                    name = value
                elif key == "physical id":
                    phys = value
                elif key == "core id":
                    core = value
                    cores.add((phys, core))
    except OSError:
        pass
    return name, (len(cores) or None)


def _mac_sysctl(key):
    try:
        return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def detect(force=False):
    """Return a dict describing this machine. Cached after the first call."""
    if _CACHE.get("profile") and not force:
        dynamic = _CACHE["profile"]
        try:  # free RAM changes; refresh it cheaply
            dynamic["ram_available_gb"] = _gb(_available_ram_bytes() or 0) or dynamic["ram_available_gb"]
        except Exception:
            pass
        return dynamic
    system = platform.system()
    logical = os.cpu_count() or 1
    physical, name, total, avail = None, "", None, None
    try:
        if system == "Windows":
            total, avail = _windows_memory()
            physical = _windows_physical_cores()
            name = _windows_cpu_name()
        elif system == "Linux":
            total, avail = _linux_memory()
            name, physical = _linux_cpu()
        elif system == "Darwin":
            total = int(_mac_sysctl("hw.memsize") or 0) or None
            physical = int(_mac_sysctl("hw.physicalcpu") or 0) or None
            name = _mac_sysctl("machdep.cpu.brand_string")
    except Exception:
        pass
    try:  # psutil, when present, fills any gap
        import psutil
        memory = psutil.virtual_memory()
        total = total or memory.total
        avail = avail or memory.available
        physical = physical or psutil.cpu_count(logical=False)
    except Exception:
        pass
    if not physical:  # last resort: assume 2-way hyper-threading on even core counts
        physical = max(1, logical // 2) if logical >= 4 and logical % 2 == 0 else logical
    physical = max(1, min(int(physical), logical))
    profile = {
        "os": system,
        "cpu_name": name or platform.processor() or "Unknown CPU",
        "physical_cores": physical,
        "logical_cores": logical,
        "ram_total_gb": _gb(total) if total else 0.0,
        "ram_available_gb": _gb(avail) if avail else 0.0,
    }
    _CACHE["profile"] = profile
    return profile


def _available_ram_bytes():
    system = platform.system()
    if system == "Windows":
        return _windows_memory()[1]
    if system == "Linux":
        return _linux_memory()[1]
    try:
        import psutil
        return psutil.virtual_memory().available
    except Exception:
        return None


def fingerprint(profile):
    return "%s|%s|%s|%s" % (profile.get("cpu_name"), profile.get("physical_cores"),
                            profile.get("logical_cores"), round(profile.get("ram_total_gb", 0)))


# ------------------------------------------------------------ recommendation
def recommend(profile):
    """Heuristic settings from detected hardware (no benchmark needed)."""
    phys = profile["physical_cores"]
    ram_total = profile.get("ram_total_gb", 0.0)
    ram_free = profile.get("ram_available_gb", 0.0)
    # Token generation is memory-bound: extra hyper-threads do not help. Leave one core
    # for the UI / voice on 3+ core CPUs; do not go above 8 threads.
    threads = phys if phys <= 2 else min(phys - 1, 8)
    if ram_total and (ram_total < 8 or phys <= 2):
        tier = "low"
    elif ram_total and (ram_total < 16 or phys <= 4):
        tier = "mid"
    else:
        tier = "high"
    num_ctx = {"low": 2048, "mid": 3072, "high": 4096}[tier]
    if ram_free and ram_free < 2.5:
        num_ctx = 2048  # protect against swapping when memory is tight
    warnings = []
    if ram_free and ram_free < 3.0:
        warnings.append("Only %.1f GB RAM is free; close other apps or use a smaller model." % ram_free)
    if tier == "low":
        warnings.append("This PC is on the low side for a 3B model; 'llama3.2:1b' would be noticeably faster.")
    return {"num_thread": threads, "num_ctx": num_ctx, "tier": tier, "warnings": warnings}


# ---------------------------------------------------------------- tuning file
def _tuning_path(config_dir):
    return os.path.join(config_dir, TUNING_FILE)


def load_tuning(config_dir, profile):
    """Saved calibration result, only if the hardware fingerprint still matches."""
    try:
        with open(_tuning_path(config_dir), encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("fingerprint") == fingerprint(profile) and int(data.get("num_thread", 0)) > 0:
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def tuned_options(base_options, profile, config_dir):
    """Copy of the Ollama options with hardware-matched num_thread / num_ctx. GPU stays off."""
    options = dict(base_options or {})
    rec = recommend(profile)
    saved = load_tuning(config_dir, profile)
    options["num_thread"] = int(saved["num_thread"]) if saved else rec["num_thread"]
    options["num_ctx"] = rec["num_ctx"]
    options["num_gpu"] = 0
    return options


def resolve_options(settings, config_dir):
    """Convenience for callers: returns (options, profile_or_None). Honors the on/off switch."""
    base = (settings or {}).get("ollama_options")
    if not (settings or {}).get("auto_hardware_tuning", True):
        return base, None
    try:
        profile = detect()
        return tuned_options(base, profile, config_dir), profile
    except Exception:
        return base, None


def describe(profile, options=None):
    rec = recommend(profile)
    line = "%s  |  %d cores / %d threads  |  %.1f GB RAM (%.1f free)  |  tier: %s" % (
        profile["cpu_name"], profile["physical_cores"], profile["logical_cores"],
        profile["ram_total_gb"], profile["ram_available_gb"], rec["tier"])
    if options:
        line += "  |  using %s threads, ctx %s" % (options.get("num_thread"), options.get("num_ctx"))
    return line


# ---------------------------------------------------------------- calibration
_CAL_TEXT = ("Study planning helps when it is simple and regular. Pick one subject, set a short timer, "
             "remove distractions, and write down what you learned afterwards. Review it the next day. ") * 6


def _candidate_threads(profile):
    phys, logical = profile["physical_cores"], profile["logical_cores"]
    rec = recommend(profile)["num_thread"]
    values = {rec, phys, max(1, phys - 1), max(2, phys // 2), min(logical, phys + max(1, phys // 2))}
    return sorted(v for v in values if 1 <= v <= logical)[:5]


def calibrate(llm, profile, config_dir, progress=None):
    """Benchmark thread counts on the real model and save the fastest.

    Blocking (about 1-2 minutes). Run it in a worker thread. Needs Ollama running.
    Score = estimated seconds for a typical turn (600-token prompt + 60-token reply).
    """
    def say(message):
        if callable(progress):
            progress(message)

    rec = recommend(profile)
    base = {"num_gpu": 0, "num_ctx": rec["num_ctx"], "num_predict": 40, "temperature": 0}
    results = []
    candidates = _candidate_threads(profile)
    for index, threads in enumerate(candidates, 1):
        say("Auto-tune %d/%d: testing %d threads…" % (index, len(candidates), threads))
        best = None
        for attempt in range(2):  # first run absorbs any model reload; keep the second
            nonce = "%d-%d-%d. " % (threads, attempt, int(time.time() * 1000) % 100000)
            messages = [{"role": "system", "content": nonce + _CAL_TEXT},
                        {"role": "user", "content": "Reply with one short sentence about studying."}]
            llm.chat_completion(messages, tools=None, options=dict(base, num_thread=threads))
            metrics = dict(getattr(llm, "last_metrics", {}) or {})
            best = metrics
        try:
            prompt_tokens = max(1, int(best.get("prompt_tokens") or 0))
            prompt_s = max(0.001, float(best.get("prompt_eval_ms") or 0) / 1000)
            gen_tps = float(best.get("tokens_per_second") or 0)
            if gen_tps <= 0:
                continue
            est = 600 / (prompt_tokens / prompt_s) + 60 / gen_tps
            results.append({"num_thread": threads, "prompt_tps": round(prompt_tokens / prompt_s, 1),
                            "gen_tps": round(gen_tps, 2), "est_turn_s": round(est, 1)})
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if not results:
        raise RuntimeError("Calibration produced no usable measurements.")
    winner = min(results, key=lambda r: r["est_turn_s"])
    heuristic = next((r for r in results if r["num_thread"] == rec["num_thread"]), None)
    # Ignore tiny differences (measurement noise): keep the heuristic unless clearly beaten.
    if heuristic and winner is not heuristic and winner["est_turn_s"] > heuristic["est_turn_s"] * 0.95:
        winner = heuristic
    record = {"fingerprint": fingerprint(profile), "num_thread": winner["num_thread"],
              "num_ctx": rec["num_ctx"], "results": results, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    path = _tuning_path(config_dir)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    os.replace(temporary, path)
    say("Auto-tune done: best is %d threads (~%.1f s per typical reply)." % (winner["num_thread"], winner["est_turn_s"]))
    return record
