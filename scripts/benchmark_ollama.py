r"""Benchmark installed Ollama models for Study Buddy without executing real tools.

Example (Windows):
    py -3.12 scripts\benchmark_ollama.py --runs 3

The tool benchmark only asks Ollama to propose an add_task function call; it never
registers the function with a real handler and therefore never changes the database.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

ADD_TASK_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "add_task",
        "description": "Add a task to a study task list.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    },
}]


def ns_to_ms(value):
    try:
        return float(value or 0) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def request_chat(base_url, model, messages, options, keep_alive):
    started = time.perf_counter()
    response = requests.post(
        base_url.rstrip("/") + "/api/chat",
        json={
            "model": model,
            "messages": messages,
            "tools": ADD_TASK_SCHEMA if "task benchmark" in str(messages[-1].get("content", "")).lower() else [],
            "stream": False,
            "keep_alive": keep_alive,
            "options": options,
        },
        timeout=240,
    )
    response.raise_for_status()
    data = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    generated = int(data.get("eval_count") or 0)
    eval_ms = ns_to_ms(data.get("eval_duration"))
    message = data.get("message") or {}
    return {
        "elapsed_ms": elapsed_ms,
        "load_ms": ns_to_ms(data.get("load_duration")),
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "prompt_ms": ns_to_ms(data.get("prompt_eval_duration")),
        "generated_tokens": generated,
        "generation_ms": eval_ms,
        "tokens_per_second": generated / (eval_ms / 1000) if eval_ms > 0 else 0,
        "has_tool_call": bool(message.get("tool_calls")),
        "content": str(message.get("content") or "").strip()[:100],
    }


def median(values):
    return statistics.median(values) if values else 0.0


def run_model(base_url, model, runs, options, keep_alive):
    print(f"\n=== {model} | num_thread={options.get('num_thread', 'Ollama default')} ===")
    regular = [{"role": "user", "content": "Reply with exactly: OK."}]
    tool = [{"role": "user", "content": "Please add a task benchmark task for this model test."}]
    try:
        warm = request_chat(base_url, model, regular, options, keep_alive)
    except requests.RequestException as exc:
        print(f"SKIPPED: {exc}")
        return
    except (ValueError, KeyError) as exc:
        print(f"SKIPPED: unexpected Ollama response: {exc}")
        return
    print(f"Cold/warm-up request: {warm['elapsed_ms']:.0f} ms; model load: {warm['load_ms']:.0f} ms")

    chat_rows = []
    tool_rows = []
    for _ in range(runs):
        try:
            chat_rows.append(request_chat(base_url, model, regular, options, keep_alive))
            tool_rows.append(request_chat(base_url, model, tool, options, keep_alive))
        except requests.RequestException as exc:
            print(f"Request failed: {exc}")
            return
    print("Warm chat:  median total {:.0f} ms | prompt {:.0f} ms | {:.2f} tok/s".format(
        median([r["elapsed_ms"] for r in chat_rows]),
        median([r["prompt_ms"] for r in chat_rows]),
        median([r["tokens_per_second"] for r in chat_rows]),
    ))
    print("Tool call:  median total {:.0f} ms | prompt {:.0f} ms | {:.2f} tok/s | tool-call rate {}/{}".format(
        median([r["elapsed_ms"] for r in tool_rows]),
        median([r["prompt_ms"] for r in tool_rows]),
        median([r["tokens_per_second"] for r in tool_rows]),
        sum(1 for r in tool_rows if r["has_tool_call"]), len(tool_rows),
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="Ollama URL (defaults to config/settings.json)")
    parser.add_argument("--models", nargs="+", default=None,
                        help="Installed model tags to compare")
    parser.add_argument("--runs", type=int, default=3, help="Warm samples per workload (1-10)")
    parser.add_argument("--threads", nargs="+", type=int, default=None,
                        help="Optionally compare CPU thread counts, e.g. --threads 2 4 6")
    args = parser.parse_args()

    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    base_url = args.url or settings.get("ollama_url", "http://localhost:11434")
    models = args.models or [settings.get("model_chat", "llama3.2:3b")]
    runs = max(1, min(10, args.runs))
    base_options = dict(settings.get("ollama_options") or {})
    base_options.update({"num_gpu": 0, "num_predict": min(64, int(base_options.get("num_predict", 96)))})
    threads_to_test = args.threads or [int(base_options.get("num_thread", 4))]
    threads_to_test = [max(1, min(threads, 32)) for threads in threads_to_test]
    keep_alive = settings.get("model_keep_alive_mode", "30 minutes")
    if str(keep_alive).lower() == "unlimited":
        keep_alive = -1
    elif str(keep_alive).lower() in ("1 hour", "1 hour (60 minutes)"):
        keep_alive = "1h"
    else:
        keep_alive = "30m"

    print("Study Buddy / Ollama CPU benchmark")
    print(f"URL: {base_url} | runs/configuration: {runs} | threads: {threads_to_test}")
    for model in models:
        for threads in threads_to_test:
            options = dict(base_options)
            options["num_thread"] = threads
            run_model(base_url, model, runs, options, keep_alive)
    print("\nCompare warm median latency and tool-call rate. The default checks the configured primary model only; use --models to compare another installed model manually. Run when the PC is idle; results depend on CPU, RAM pressure, Ollama version and other background work.")


if __name__ == "__main__":
    main()
