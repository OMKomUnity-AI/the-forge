#!/usr/bin/env python3
"""prompt-enricher — UserPromptSubmit hook that enriches non-trivial prompts
with a one-sentence LLM (Haiku) reformulation, injected via additionalContext.

Hybrid architecture (see README, "The engineering story"):
1. 0-LLM gate: reuses prompt-router.classify() -- if the prompt is already
   trivial/bypass, we return immediately, ZERO LLM call (no cost, no latency).
2. If non-trivial: one isolated Haiku call (inline --agents + --allowedTools ""
   + disableAllHooks, maxTurns:1, effort:low, tools:[]) produces a one-sentence
   reformulation, in the SAME language as the prompt.
3. This hook NEVER BLOCKS: it only enriches via additionalContext. It has no
   second, LLM-based line of defense (to avoid non-determinism) -- keep any
   destructive-command guard as a separate 0-LLM hook.

Fail-safe: any error/timeout -> total silence, exit 0, prompt unchanged.
"""
import sys
import os
import json
import time
import datetime
import subprocess
import importlib.util

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

# Haiku model used for the reformulation. Override without editing the code (see README):
#   export FORGE_ENRICHER_MODEL="claude-haiku-4-5-..."
ENRICHER_MODEL = os.environ.get("FORGE_ENRICHER_MODEL", "claude-haiku-4-5-20251001")

PROMPT_BODY = (
    "You are an ultra-fast clarification middleware. One output per call, never a dialogue.\n"
    "Single task: REFORMULATION -- rewrite the user's message as one enriched sentence that makes "
    "the intent explicit (real goal, implicit scope, unstated constraint). If the message is already "
    "trivial/complete, copy it verbatim, invent nothing.\n"
    "Reply in the SAME language as the user's message (French in -> French out, English in -> English out).\n"
    "Hard rules:\n"
    "- NEVER use a tool. Read NO file. Decide only from the message text.\n"
    "- Ask NO question. No dialogue, no long explanation.\n"
    "Answer on ONE single line, strict format:\n"
    "REFORMULATION: <enriched sentence>\n"
    "Nothing else. No preamble, no conclusion."
)


def _load_classify():
    spec = importlib.util.spec_from_file_location(
        "prompt_router", os.path.join(HOOKS_DIR, "prompt-router.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify


def _agents_json():
    return json.dumps({
        "prompt-enricher-runtime": {
            "description": "Ultra-fast enriched reformulation.",
            "prompt": PROMPT_BODY,
            "tools": [],
            "model": ENRICHER_MODEL,
            "effort": "low",
            "maxTurns": 1,
        }
    })


def _trace_enrich(rec):
    """Trace a Haiku call (cost/latency) into .claude/logs/enricher-trace.jsonl.

    Covers the blind spot: this call runs with disableAllHooks:true, so a session
    logger would not see it. Absolutely fail-safe; NEVER writes to stdout (this
    hook's stdout is injected into the model's context)."""
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(HOOKS_DIR))
        logdir = os.path.join(root, ".claude", "logs")
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "enricher-trace.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def enrich(prompt_text, timeout=25):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    t0 = time.monotonic()
    trace = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": ENRICHER_MODEL,
        "prompt_chars": len(prompt_text),
    }
    try:
        proc = subprocess.run(
            ["claude", "-p", f"User message: {prompt_text}",
             "--agents", _agents_json(),
             "--agent", "prompt-enricher-runtime",
             "--allowedTools", "",
             "--settings", '{"disableAllHooks": true}',
             "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        outer = json.loads(proc.stdout)
        text = (outer.get("result") or "").strip()
        trace["latency_ms"] = round((time.monotonic() - t0) * 1000)
        trace["ok"] = text.startswith("REFORMULATION:")
        trace["result_chars"] = len(text)
        # Cost/usage metrics if the CLI provides them (defensive: only if present).
        if isinstance(outer, dict):
            for k in ("total_cost_usd", "duration_ms", "num_turns", "is_error"):
                if k in outer:
                    trace[k] = outer[k]
        _trace_enrich(trace)
        if not text.startswith("REFORMULATION:"):
            return None
        return text[len("REFORMULATION:"):].strip()
    except Exception as e:
        trace["latency_ms"] = round((time.monotonic() - t0) * 1000)
        trace["ok"] = False
        trace["error"] = type(e).__name__
        _trace_enrich(trace)
        raise


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        prompt = (data.get("prompt", "") or "").strip()
    except Exception:
        return 0

    if not prompt:
        return 0

    pink, reset = "\x1b[38;5;213m", "\x1b[0m"

    try:
        classify = _load_classify()
        if not classify(prompt):
            sys.stdout.write(json.dumps({
                "systemMessage": f"{pink}🕉️ prompt-enricher: trivial, 0-LLM gate -> no Haiku call{reset}",
            }))
            return 0  # trivial/bypass -> no LLM call
    except Exception:
        return 0

    try:
        reformulation = enrich(prompt)
        if not reformulation or reformulation == prompt:
            sys.stdout.write(json.dumps({
                "systemMessage": f"{pink}🕉️ prompt-enricher: reformulation identical to prompt, nothing injected{reset}",
            }))
            return 0
        out = {
            "systemMessage": f"{pink}🕉️ Reforge (Haiku): {reformulation}{reset}",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "<prompt_context>\n"
                    f"Enriched reforge (Haiku, non-blocking): {reformulation}\n"
                    "</prompt_context>"
                ),
            }
        }
        sys.stdout.write(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
