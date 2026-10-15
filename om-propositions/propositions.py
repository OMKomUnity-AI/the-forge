#!/usr/bin/env python3
"""
propositions.py — les propositions de sessions suivantes laissees par les fins de /close.

Parcourt les transcripts Claude Code (~/.claude/projects/*/*.jsonl) modifies depuis --jours,
repere dans les messages assistant chaque bloc « Prochaines sessions … », et le decoupe en
propositions : un titre (**#n — titre** ou ### titre) et, s'il y en a un, le prompt
`make <mode> P="..."` qui le suit. Sortie JSON sur stdout, pour le mod om-propositions.

Une meme proposition repetee (bloc reaffiche, session relancee) n'est gardee qu'une fois,
la plus recente. Les doublons ENTRE sessions sont gardes : c'est au classement de les voir.

Usage : propositions.py [--jours 7] [--racine ~/.claude/projects]
        propositions.py --autotest
"""
import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BLOC = re.compile(r"^#+\s*Prochaines?\s+sessions?[^\n]*\n(.*)", re.S | re.I | re.M)
TITRE = re.compile(r"^(?:\*\*#?\d+\s*[—–-]\s*(?P<a>.+?)\*\*|#{3,4}\s+(?:#?\d+\s*[—–.)-]\s*)?(?P<b>.+?))\s*$", re.M)
PROMPT = re.compile(r'make (\w+) P="((?:[^"\\]|\\.)*)"', re.S)
EXECUTANTE = re.compile(r"session ex[eé]cutante (S\d+-[\w-]+)")


def projet(dossier: str) -> str:
    return dossier.split("-DEV-")[-1].strip("-") or dossier


def textes(o: dict) -> str:
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")


def decouper(bloc: str) -> list:
    titres = list(TITRE.finditer(bloc))
    out = []
    for i, m in enumerate(titres):
        morceau = bloc[m.end(): titres[i + 1].start() if i + 1 < len(titres) else len(bloc)]
        p = PROMPT.search(morceau)
        out.append({"titre": (m.group("a") or m.group("b")).strip(" *`"),
                    "mode": p.group(1) if p else "",
                    "prompt": p.group(2).replace('\\"', '"') if p else morceau.strip()[:600]})
    return out


def extraire(fichier: Path) -> list:
    session, items = None, []
    with fichier.open(encoding="utf-8", errors="replace") as fh:
        for ligne in fh:
            if 'Prochaine' not in ligne and session is not None:
                continue  # filtre bon marche : la plupart des lignes ne portent rien d'utile
            try:
                o = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            t = textes(o)
            if session is None and o.get("type") == "user":
                m = EXECUTANTE.search(t)
                session = m.group(1) if m else ""
            if o.get("type") != "assistant":
                continue
            m = BLOC.search(t)
            if m:
                for it in decouper(m.group(1)):
                    items.append({**it, "ts": o.get("timestamp", "")})
    nom = session or fichier.stem[:8]
    return [{**it, "session": nom, "projet": projet(fichier.parent.name), "transcript": str(fichier)}
            for it in items]


def collecter(racine: Path, jours: float) -> list:
    limite = time.time() - jours * 86400
    vus = {}
    for f in racine.glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime < limite:
                continue
        except OSError:
            continue
        for it in extraire(f):
            cle = hashlib.sha1((it["session"] + "\0" + (it["prompt"] or it["titre"])).encode()).hexdigest()[:12]
            if cle not in vus or it["ts"] > vus[cle]["ts"]:
                vus[cle] = {**it, "id": cle}
    return sorted(vus.values(), key=lambda it: it["ts"], reverse=True)


def _autotest() -> None:
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "-Users-x----DEV-Projet-Test"
    tmp.mkdir(parents=True)
    bloc = ("## Done\n\n## Prochaines sessions recommandées\n\n**#1 — Seuil éditorial**\n```\n"
            'make fast P="Projet, check-orphans.mjs. Ajouter un \\"seuil\\"."\n```\n\n'
            "**#2 — Brancher GA4**\n```\nmake think P=\"Projet. GA4.\"\n```\n")
    lignes = [{"type": "user", "message": {"content": "Tu es la session executante S1-maillage de..."}},
              {"type": "assistant", "timestamp": "2026-10-15T07:00:00Z", "message": {"content": [{"type": "text", "text": bloc}]}},
              {"type": "assistant", "timestamp": "2026-10-15T08:00:00Z", "message": {"content": [{"type": "text", "text": bloc}]}}]
    (tmp / "abc.jsonl").write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lignes))
    items = collecter(tmp.parent, 1)
    assert sorted(i["titre"] for i in items) == ["Brancher GA4", "Seuil éditorial"], items
    assert {i["mode"] for i in items} == {"fast", "think"}
    assert all(i["session"] == "S1-maillage" and i["projet"] == "Projet-Test" for i in items), items
    assert all(i["ts"] == "2026-10-15T08:00:00Z" for i in items), "le bloc reaffiche garde la plus recente"
    assert any('"seuil"' in i["prompt"] for i in items), "guillemets echappes restitues"
    print("autotest propositions.py : OK (2 propositions, bloc repete dedoublonne)")


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        _autotest()
        sys.exit(0)
    a = argparse.ArgumentParser()
    a.add_argument("--jours", type=float, default=7)
    a.add_argument("--racine", default=str(Path.home() / ".claude" / "projects"))
    a = a.parse_args()
    print(json.dumps({"genere": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jours": a.jours,
                      "items": collecter(Path(a.racine), a.jours)}, ensure_ascii=False))
