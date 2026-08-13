#!/usr/bin/env python3
"""prompt-enricher — UserPromptSubmit hook, enrichit les prompts non-triviaux
via une reformulation LLM (Haiku) injectee en additionalContext.

Architecture hybride (decision utilisateur, cf.
docs/superpowers/specs/2026-08-08-eval-hook-clarifier.md) :
1. Gate 0-LLM : reutilise prompt-router.classify() -- si le prompt est deja
   trivial/bypass, on sort immediatement, ZERO appel LLM (cout/latence nuls).
2. Si non-trivial : un appel Haiku isole (mecanisme valide 21/21 sans timeout,
   --agents inline + --allowedTools "" + disableAllHooks, maxTurns:1,
   effort:low, tools:[]) produit une reformulation en une phrase.
3. Ce hook NE BLOQUE JAMAIS (decision utilisateur explicite) : il ne fait
   qu'enrichir via additionalContext. Le blocage sur risque destructif reste
   gere par git-safety.py (0-LLM, deja fiable) -- pas de seconde ligne de
   defense LLM ici, pour eviter le non-determinisme observe en V6/V7.

Fail-safe : toute erreur/timeout -> silence total, exit 0, prompt inchange.
"""
import sys
import os
import json
import time
import datetime
import subprocess
import importlib.util

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

# Modele Haiku de la reformulation. Surchargeable sans editer le code (voir README) :
#   export FORGE_ENRICHER_MODEL="claude-haiku-4-5-..."
ENRICHER_MODEL = os.environ.get("FORGE_ENRICHER_MODEL", "claude-haiku-4-5-20251001")

PROMPT_BODY = (
    "Tu es un middleware de clarification ultra-rapide. Une seule sortie par appel, jamais de dialogue.\n"
    "Tache unique : REFORMULATION -- reecris le message utilisateur en une phrase enrichie qui rend "
    "l'intention explicite (objectif reel, portee implicite, contrainte sous-entendue). Si le message "
    "est deja trivial/complet, recopie-le tel quel, n'invente rien.\n"
    "Interdictions absolues :\n"
    "- N'utilise JAMAIS d'outil. Ne consulte AUCUN fichier. Decide uniquement a partir du texte du message.\n"
    "- Ne pose AUCUNE question. Pas de dialogue, pas d'explication longue.\n"
    "Reponds en UNE seule ligne, format strict :\n"
    "REFORMULATION: <phrase enrichie>\n"
    "Rien d'autre. Pas de preambule, pas de conclusion."
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
            "description": "Reformulation enrichie ultra-rapide.",
            "prompt": PROMPT_BODY,
            "tools": [],
            "model": ENRICHER_MODEL,
            "effort": "low",
            "maxTurns": 1,
        }
    })


def _trace_enrich(rec):
    """Trace un appel Haiku (coût/latence) dans .claude/logs/enricher-trace.jsonl.

    Comble l'angle mort : cet appel tourne avec `disableAllHooks:true`, donc session-logger
    ne le voit pas. Fail-safe absolu ; n'écrit JAMAIS sur stdout (le stdout de ce hook est
    injecté dans le contexte du modèle)."""
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
            ["claude", "-p", f"Message utilisateur: {prompt_text}",
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
        # Métriques de coût/usage si le CLI les fournit (défensif : seulement si présentes).
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
                "systemMessage": f"{pink}🕉️ prompt-enricher : trivial, gate 0-LLM -> aucun appel Haiku{reset}",
            }))
            return 0  # trivial/bypass -> aucun appel LLM
    except Exception:
        return 0

    try:
        reformulation = enrich(prompt)
        if not reformulation or reformulation == prompt:
            sys.stdout.write(json.dumps({
                "systemMessage": f"{pink}🕉️ prompt-enricher : reformulation identique au prompt, rien injecte{reset}",
            }))
            return 0
        out = {
            "systemMessage": f"{pink}🕉️ Reformulation (Haiku) : {reformulation}{reset}",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "<prompt_context>\n"
                    f"Reformulation enrichie (Haiku, non bloquant) : {reformulation}\n"
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
