#!/usr/bin/env python3
"""
propositions.py — les propositions de sessions suivantes laissees par les fins de /close.

Parcourt les transcripts Claude Code (~/.claude/projects/*/*.jsonl) modifies depuis --jours,
repere dans les messages assistant chaque bloc « Prochaines sessions … », et le decoupe en
propositions : un titre (**#n — titre** ou ### titre) et, s'il y en a un, le prompt
`make <mode> P="..."` qui le suit. Fusionné dans un store pérenne (~/.claude/propositions/propositions.jsonl),
puis sortie JSON sur stdout pour le mod om-propositions.

Une même proposition répétée (bloc réaffiche, session relancée) n'est gardée qu'une fois,
la plus récente. Les doublons ENTRE sessions sont gardés : c'est au classement de les voir.
Les propositions du store qui ne sont plus dans le scrape récent restent dans le fichier pérenne.

Usage : propositions.py [--jours 7] [--racine ~/.claude/projects] [--store ~/.claude/propositions/propositions.jsonl]
        propositions.py --sync
        propositions.py --autotest
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STORE_DEFAUT = str(Path.home() / ".claude" / "propositions" / "propositions.jsonl")

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


def lire_store(chemin: str) -> dict:
    """Lit le store JSONL pérenne. Retourne {id: item}. Fichier absent → store vide.
    Les lignes malformées sont ignorées ; une erreur d'accès au fichier lève."""
    store = {}
    p = Path(chemin)
    if not p.exists():
        return store
    # Une ligne illisible est ignoree (elle seule est perdue), mais un fichier present et
    # illisible leve : le prendre pour un store vide ferait ecraser l'archive par le scrape du jour.
    with p.open(encoding="utf-8", errors="replace") as fh:
        for ligne in fh:
            try:
                obj = json.loads(ligne)
                if "id" in obj:
                    store[obj["id"]] = obj
            except json.JSONDecodeError:
                pass
    return store


def fusionner(store: dict, items: list) -> tuple:
    """Fusionne le scrape dans le store. Retourne (store_fusionné, nombre de nouveaux ajoutés).
    Un id déjà présent : garde celui dont le ts est le plus récent.
    Un id absent : ajoute l'item du scrape."""
    nb_nouveaux = 0
    for item in items:
        cle = item["id"]
        if cle not in store:
            store[cle] = item
            nb_nouveaux += 1
        elif item.get("ts", "") > store[cle].get("ts", ""):
            # Le scrape ne connait pas le suivi (copiee/lancee) : on le garde, sinon une proposition
            # relancee reapparaitrait dans la liste a chaque rafraichissement.
            store[cle] = {**{k: store[cle][k] for k in ("copiee", "lancee") if k in store[cle]}, **item}
    return store, nb_nouveaux


def ecrire_store(chemin: str, store: dict) -> None:
    """Écrit le store JSONL de manière atomique.
    Crée le répertoire parent si absent. Trie par ts décroissant."""
    p = Path(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Trier par ts décroissant
    items_tries = sorted(store.values(), key=lambda x: x.get("ts", ""), reverse=True)

    # Écriture atomique : fichier temporaire puis rename
    fd, tmp_path = None, None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(p.parent), text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for item in items_tries:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        os.replace(tmp_path, str(p))
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _norm(t: str) -> str:
    return " ".join((t or "").split())


def premiers_prompts(racine: Path, jours: float) -> dict:
    """transcript -> (premier message utilisateur normalise, ts) des transcripts recents.
    `make <mode> P="..."` passe le prompt tel quel : il EST le premier message de la session lancee."""
    limite = time.time() - jours * 86400
    out = {}
    for f in racine.glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime < limite:
                continue
            with f.open(encoding="utf-8", errors="replace") as fh:
                for n, ligne in enumerate(fh):
                    if n > 40:
                        break
                    try:
                        o = json.loads(ligne)
                    except json.JSONDecodeError:
                        continue
                    t = textes(o).strip()
                    if o.get("type") == "user" and t and not t.startswith("<"):
                        out[str(f)] = (_norm(t), o.get("timestamp", ""))
                        break
        except OSError:
            continue
    return out


def marquer_lancements(store: dict, racine: Path, jours: float) -> int:
    """Pose `lancee` sur chaque proposition dont le prompt ouvre une AUTRE session, plus recente.
    Retourne le nombre de propositions nouvellement marquees."""
    attente = {k: v for k, v in store.items() if not v.get("lancee") and len(_norm(v.get("prompt", ""))) >= 40}
    if not attente:
        return 0
    n = 0
    debuts = premiers_prompts(racine, jours)
    for k, v in attente.items():
        cle = _norm(v["prompt"])[:100]
        for tr, (txt, ts) in debuts.items():
            if tr != v.get("transcript") and ts >= v.get("ts", "") and cle in txt:
                v["lancee"] = {"session": Path(tr).stem[:8], "ts": ts, "transcript": tr}
                n += 1
                break
    return n


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

    # Test 1 : bloc répété → dédoublonné, ts le plus récent
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
    print("✓ Test 1 : bloc répété, dédoublonné par ts (2 propositions)")

    # Test 2 : store pérenne — proposition en store, absente du scrape, persiste
    store_dir = Path(tempfile.mkdtemp()) / "propositions"
    store_path = str(store_dir / "test.jsonl")

    # Créer un store avec une ancienne proposition
    ancienne_prop = {"id": "old123456", "titre": "Vieille idée", "session": "S0-ancien",
                     "prompt": "make old P=\"test\"", "ts": "2026-01-01T00:00:00Z",
                     "projet": "Projet-Test", "mode": "old", "transcript": "/fake"}
    ecrire_store(store_path, {"old123456": ancienne_prop})

    # Scraper le nouveau bloc (ne contient pas la vieille proposition)
    store = lire_store(store_path)
    assert "old123456" in store, "ancien id perdu après lecture du store"
    for it in items:
        it["id"] = hashlib.sha1((it["session"] + "\0" + (it["prompt"] or it["titre"])).encode()).hexdigest()[:12]
    store, nb_nouveaux = fusionner(store, items)
    assert "old123456" in store, "ancien id perdu après fusion"
    assert nb_nouveaux == 2, f"attendu 2 nouveaux, got {nb_nouveaux}"
    ecrire_store(store_path, store)

    # Relire le store : la vieille proposition doit y être
    store_relect = lire_store(store_path)
    assert "old123456" in store_relect, "proposition vieille disparue après écriture/relecture"
    assert store_relect["old123456"]["titre"] == "Vieille idée"
    print("✓ Test 2 : proposition en store, absente du scrape, persiste")

    # Test 3 : même id vu deux fois → une seule ligne, ts le plus récent gagne
    item1 = {"id": "same123456", "titre": "Test", "session": "S1-test", "ts": "2026-10-15T10:00:00Z",
             "prompt": "old", "projet": "Projet-Test", "mode": "fast", "transcript": "/fake"}
    item2 = {"id": "same123456", "titre": "Test", "session": "S1-test", "ts": "2026-10-15T11:00:00Z",
             "prompt": "new", "projet": "Projet-Test", "mode": "fast", "transcript": "/fake"}

    store_dup = {}
    store_dup, _ = fusionner(store_dup, [item1])
    assert store_dup["same123456"]["ts"] == "2026-10-15T10:00:00Z", "first ts not stored"

    store_dup, _ = fusionner(store_dup, [item2])
    assert len(store_dup) == 1, f"expected 1 item, got {len(store_dup)}"
    assert store_dup["same123456"]["ts"] == "2026-10-15T11:00:00Z", "newer ts not kept"
    assert store_dup["same123456"]["prompt"] == "new", "newer prompt not kept"
    print("✓ Test 3 : même id deux fois → une seule ligne, ts le plus récent gagne")

    # Test 4 : store avec une ligne corrompue → les autres lignes sont lues
    corrupt_store_path = str(store_dir / "corrupt.jsonl")
    with open(corrupt_store_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "good1", "titre": "OK"}, ensure_ascii=False) + "\n")
        f.write("{pas du json\n")  # ligne corrompue
        f.write(json.dumps({"id": "good2", "titre": "Aussi OK"}, ensure_ascii=False) + "\n")

    corrupt_store = lire_store(corrupt_store_path)
    assert len(corrupt_store) == 2, f"expected 2 items, got {len(corrupt_store)}"
    assert "good1" in corrupt_store and "good2" in corrupt_store, "bonnes lignes perdues"
    print("✓ Test 4 : store corrompu (1 ligne invalide) → autres lignes lues")

    # Test 5 : stabilité de l'id — sha1 en dur
    session_test = "S1-maillage"
    prompt_test = 'Projet, check-orphans.mjs. Ajouter un "seuil".'
    expected_id = hashlib.sha1((session_test + "\0" + prompt_test).encode()).hexdigest()[:12]
    assert expected_id == "e5462e28f206", f"id non stable, attendu e5462e28f206 got {expected_id}"
    print("✓ Test 5 : id stable (sha1 + troncature à 12 char)")

    print("\nautotest propositions.py : OK (5 cas couverts)")


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        _autotest()
        sys.exit(0)
    a = argparse.ArgumentParser()
    a.add_argument("--jours", type=float, default=7)
    a.add_argument("--racine", default=str(Path.home() / ".claude" / "projects"))
    a.add_argument("--store", default=STORE_DEFAUT)
    a.add_argument("--sync", action="store_true")
    a.add_argument("--marquer", default="", help="id d'une proposition dont la commande vient d'etre copiee")
    a = a.parse_args()

    # Scraper, lire le store, fusionner, écrire
    items_scrape = collecter(Path(a.racine), a.jours)
    store = lire_store(a.store)

    # Ajouter les id au scrape (déjà là, mais on s'assure)
    for item in items_scrape:
        if "id" not in item:
            cle = hashlib.sha1((item["session"] + "\0" + (item["prompt"] or item["titre"])).encode()).hexdigest()[:12]
            item["id"] = cle

    avant = {k: v.get("ts", "") for k, v in store.items()}
    store, nb_nouveaux = fusionner(store, items_scrape)
    suivi = 0
    if a.marquer and a.marquer in store and not store[a.marquer].get("copiee"):
        store[a.marquer]["copiee"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        suivi += 1
    suivi += marquer_lancements(store, Path(a.racine), max(a.jours, 3))
    # ponytail: pas de verrou entre sessions concurrentes. os.replace garde le fichier toujours
    # entier ; une fusion perdue par une ecriture simultanee revient au scrape suivant (30 s).
    # Verrou de fichier si un jour deux sessions ecrivent vraiment en meme temps.
    if nb_nouveaux or suivi or any(avant.get(k) != v.get("ts", "") for k, v in store.items()):
        ecrire_store(a.store, store)

    if a.sync:
        # Mode --sync : une seule ligne lisible
        total = len(store)
        s = "nouvelle" if nb_nouveaux == 1 else "nouvelles"
        print(f"{nb_nouveaux} {s} proposition(s), {total} au total dans {a.store}")
    else:
        # Mode défaut : JSON avec tout le store
        items_tries = sorted(store.values(), key=lambda x: x.get("ts", ""), reverse=True)
        print(json.dumps({"genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "jours": a.jours,
                          "items": items_tries,
                          "store": a.store,
                          "nouveaux": nb_nouveaux}, ensure_ascii=False))
