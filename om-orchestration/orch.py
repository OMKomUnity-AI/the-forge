#!/usr/bin/env python3
"""
orch.py — moniteur d'orchestration multi-sessions, pérenne et sans serveur.

Chaque session (orchestratrice ou exécutante) appelle `log` ; le rendu Markdown est
réécrit à CHAQUE appel. Le fichier de sortie s'ouvre en onglet épinglé dans l'IDE :
l'éditeur le rafraîchit seul, il n'y a donc aucun process à maintenir, rien qui meurt
avec un terminal, et l'état survit à un redémarrage comme à un /compact.

Journal APPEND-ONLY (events.jsonl) : plusieurs sessions écrivent en concurrence, donc
on n'édite jamais une ligne existante — on empile, et l'état se recalcule à la lecture.
C'est la seule forme sûre ici : deux sessions qui réécriraient le même fichier d'état
se perdraient mutuellement des événements.

Emplacements (surchargeables) :
  état  : $ORCH_DIR            (défaut <repo>/.claude/orchestration)
  sortie: $ORCH_OUT            (défaut <repo>/ORCHESTRATION.md)
  repo  : $CLAUDE_PROJECT_DIR  (défaut : cwd)

Usage :
  orch.py log --session sonnet-maillage --task "#1" --kind start --msg "liens vers /comprendre" --model sonnet
  orch.py log --session sonnet-maillage --task "#1" --kind review-request --msg "diff pret" --ref 3 fichiers
  orch.py log --session ORCHESTRATRICE --task "#1" --kind review-ok --msg "6 ancres distinctes, verifie"
  orch.py render            # recalcule le Markdown sans ajouter d'evenement
  orch.py status            # resume sur stdout (pour une statusline ou un coup d'oeil)

Codes : 0 = ok, 1 = erreur d'usage.
"""
import argparse
import json
import os
import re
import shlex
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("planned", "start", "progress", "review-request", "review-ok", "review-reject", "done", "blocked", "note",
         "question", "answer")
# question/answer ne changent JAMAIS l'etat d'une tache : une session qui pose une question
# reste « en cours ». Une question est en attente tant qu'aucun `answer` ne porte son id en ref.
HORS_ETAT = ("question", "answer")
# Pointeur vers le journal courant, hors de tout depot : le pane le lit quel que soit le
# repertoire ou tourne la session qui l'affiche. Sans lui, un chemin relatif suivait le cwd
# et le pane affichait « Aucun journal lisible » des que la session changeait de dossier
# (constate le 2026-10-15).
POINTEUR = Path.home() / ".claude" / "orchestration" / "journal-courant.json"
PROJETS = Path.home() / ".claude" / "projects"
# Un seul etat courant par (session, tache) : le DERNIER evenement gagne. L'ordre ci-dessous
# ne sert qu'a l'affichage, jamais a decider — sinon un `done` tardif serait masque par un
# `blocked` plus ancien.
PUCE = {"planned": "⚪", "start": "🟢", "progress": "🟢", "review-request": "🟡", "review-ok": "✅",
        "review-reject": "❌", "done": "✅", "blocked": "🔴", "note": "·", "question": "❓", "answer": "💬"}


def racine() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())


def chemin_etat() -> Path:
    d = Path(os.environ.get("ORCH_DIR") or racine() / ".claude" / "orchestration")
    d.mkdir(parents=True, exist_ok=True)
    return d / "events.jsonl"


def chemin_sortie() -> Path:
    return Path(os.environ.get("ORCH_OUT") or racine() / "ORCHESTRATION.md")


def lire() -> list:
    f = chemin_etat()
    if not f.exists():
        return []
    evenements = []
    for ligne in f.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            evenements.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue  # ligne tronquee par une ecriture concurrente : on saute, on ne casse pas
    return evenements


def ajouter(session: str, task: str, kind: str, msg: str, ref: str = "", model: str = "",
            options: list = None, transcript: str = None, cwd: str = "", prompt: str = "") -> dict:
    ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "session": session, "task": task, "kind": kind, "msg": msg, "ref": ref}
    if model:
        ev["model"] = model
    if options:
        ev["options"] = options
    if kind in ("question", "planned"):
        # Id propre a la question : `session|ts` collisionnait quand une session pose plusieurs
        # questions dans la meme seconde, et UNE reponse les retirait toutes (2026-10-15).
        ev["id"] = uuid.uuid4().hex[:10]
    if transcript:
        ev["transcript"] = transcript
    if cwd:
        ev["cwd"] = cwd
    if prompt:
        ev["prompt"] = prompt
    with chemin_etat().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def modeles(evenements: list) -> dict:
    """Modele de chaque session = le DERNIER qu'elle a declare (--model), "" si jamais declare.
    Declare une fois (au `planned` ou au `start`), il vaut pour toutes ses taches suivantes."""
    out = {}
    for ev in evenements:
        if ev["kind"] == "answer":
            continue  # PROPRIETAIRE repond depuis le pane : ce n'est pas une session a surveiller
        out.setdefault(ev["session"], "")
        if ev.get("model"):
            out[ev["session"]] = ev["model"]
    return out


def alerte_modele(session: str, model: str) -> str:
    """Les sous-sessions portent un modele explicite, jamais Opus (CLAUDE.md global, § Subagents).
    L'orchestratrice, elle, peut etre Opus depuis la levee du 2026-10-15."""
    if not model:
        return "modèle non déclaré"
    if "opus" in model.lower() and not session.upper().startswith("ORCH"):
        return "exécutant en Opus"
    return ""


def horodatage(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def duree(depuis: str) -> str:
    delta = datetime.now(timezone.utc) - horodatage(depuis)
    m = int(delta.total_seconds() // 60)
    if m < 1:
        return "<1 min"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d}"


def ecrire_pointeur() -> None:
    POINTEUR.parent.mkdir(parents=True, exist_ok=True)
    POINTEUR.write_text(json.dumps({"ORCH_DIR": str(chemin_etat().parent),
                                    "ORCH_OUT": str(chemin_sortie())}), encoding="utf-8")


def lire_pointeur() -> None:
    """Sans ORCH_DIR explicite, reprend le dernier journal ecrit (pour le pane, qui ne sait
    pas d'ou les sessions ecrivent)."""
    if os.environ.get("ORCH_DIR") or not POINTEUR.exists():
        return
    try:
        os.environ.update(json.loads(POINTEUR.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass


def trouver_transcript(session: str) -> str:
    """Le transcript de la session qui appelle `log` : le plus recemment modifie (2 min) dont
    la fin contient `--session <nom>`. L'appel Bash est ecrit au transcript AVANT de tourner,
    c'est ce qui rend la recherche possible. ORCH_TRANSCRIPT force le choix."""
    if os.environ.get("ORCH_TRANSCRIPT"):
        return os.environ["ORCH_TRANSCRIPT"]
    aiguille = f"--session {session}".encode()
    maintenant = datetime.now().timestamp()
    candidats = []
    for f in PROJETS.glob("*/*.jsonl"):
        try:
            st = f.stat()
            if maintenant - st.st_mtime > 120:
                continue
            with f.open("rb") as fh:
                fh.seek(max(0, st.st_size - 200_000))
                if aiguille in fh.read():
                    candidats.append((st.st_mtime, str(f)))
        except OSError:
            continue
    return max(candidats)[1] if candidats else ""


STATUSLINE = Path.home() / ".claude" / "orchestration" / "contexte"
CACHE_COMPACT = Path.home() / ".claude" / "orchestration" / "compactions-cache.json"


def compactions(f: Path) -> int:
    """Compactions sur TOUT le transcript, lu en increment depuis le dernier offset (cache) :
    un transcript fait des centaines de Mo, le relire a chaque rafraichissement du pane est exclu."""
    try:
        cache = json.loads(CACHE_COMPACT.read_text()) if CACHE_COMPACT.exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    offset, n = cache.get(str(f), [0, 0])
    taille = f.stat().st_size
    if taille < offset:
        offset, n = 0, 0  # fichier reecrit : on repart de zero
    if taille > offset:
        with f.open("rb") as fh:
            fh.seek(offset)
            bloc = fh.read()
        coupe = bloc.rfind(b"\n") + 1  # ne compter que des lignes completes
        n += bloc[:coupe].count(b'"compact_boundary"')
        offset += coupe
        cache[str(f)] = [offset, n]
        try:
            CACHE_COMPACT.parent.mkdir(parents=True, exist_ok=True)
            CACHE_COMPACT.write_text(json.dumps(cache))
        except OSError:
            pass
    return n


def contexte_transcript(chemin: str) -> dict:
    """Remplissage de la fenetre de la session de ce transcript.
    Source 1 : ce que la statusline de la session a ecrit (taille de fenetre exacte, 200k ou 1M).
    Source 2, repli : le dernier message assistant du transcript, fenetre alors ESTIMEE."""
    f = Path(chemin)
    if not chemin or not f.exists():
        return {}
    st = f.stat()
    base = {"compactions": compactions(f),
            "maj": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")}
    sl = STATUSLINE / (f.stem + ".json")
    if sl.exists():
        try:
            d = json.loads(sl.read_text(encoding="utf-8"))
            if d.get("window"):
                tokens = int(d.get("tokens") or 0)
                pct = d.get("pct")
                return {**base, "tokens": tokens, "fenetre": int(d["window"]), "modele_reel": d.get("model", ""),
                        "pct": round(pct) if pct is not None else round(100 * tokens / d["window"]), "estime": False}
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    with f.open("rb") as fh:
        fh.seek(max(0, st.st_size - 2_000_000))
        lignes = fh.read().decode("utf-8", "replace").splitlines()
    for l in reversed(lignes):
        if '"usage"' not in l:
            continue
        try:
            o = json.loads(l)
        except json.JSONDecodeError:
            continue
        m = o.get("message") or {}
        u = m.get("usage") or {}
        if o.get("type") != "assistant" or not u or o.get("isSidechain"):
            continue
        tokens = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                  + u.get("cache_read_input_tokens", 0))
        fenetre = 200_000 if tokens <= 200_000 else 1_000_000
        return {**base, "tokens": tokens, "fenetre": fenetre, "pct": round(100 * tokens / fenetre),
                "modele_reel": m.get("model", ""), "estime": True}
    return {}


def contextes(evenements: list) -> dict:
    """Session -> remplissage, via le dernier transcript qu'elle a lie a un evenement."""
    lien, proprio = {}, {}
    for ev in evenements:
        t = ev.get("transcript")
        if not t:
            continue
        # Un transcript appartient a la PREMIERE session qui l'a lie. Avant le garde de `log`,
        # l'orchestratrice qui loguait pour un sous-agent lui pretait son propre contexte.
        proprio.setdefault(t, ev["session"])
        if proprio[t] == ev["session"]:
            lien[ev["session"]] = t
    return {s: contexte_transcript(t) for s, t in lien.items()}


def sessions_terminees(evenements: list) -> list:
    """Sessions dont toutes les taches sont done/review-ok et sans question en attente : le pane
    les replie en une ligne, sinon la liste grossit de tout l'historique du journal."""
    st = etat(evenements)
    attente = {q["session"] for q in questions_en_attente(evenements)}
    par = {}
    for (s, _), v in st.items():
        par.setdefault(s, []).append(v["ev"]["kind"])
    return sorted(s for s, k in par.items() if s not in attente and all(x in ("done", "review-ok") for x in k))


def vivantes() -> list:
    """Toutes les sessions Claude Code vivantes (~/.claude/sessions), journal ou non, avec un
    drapeau `close` : vrai si le dernier /omk:close a ete suivi de son bloc « Prochaines sessions
    recommandees » ET qu'aucun message utilisateur n'est arrive depuis. Resultat mis en cache par
    (taille, mtime) du transcript : le pane rappelle snapshot toutes les 2 s, et certains
    transcripts depassent 20 Mo."""
    racine = Path.home() / ".claude"
    cache_f = racine / "orchestration" / "vivantes-cache.json"
    try:
        cache = json.loads(cache_f.read_text())
    except Exception:
        cache = {}
    neuf = {}
    for f in sorted((racine / "sessions").glob("*.json")):
        try:
            d = json.loads(f.read_text())
            sid = d["sessionId"]
        except Exception:
            continue
        tr = next(iter((racine / "projects").glob(f"*/{sid}.jsonl")), None)
        info = {"close": False, "dernier": "", "maj": d.get("updatedAt")}
        if tr:
            st = tr.stat()
            cle = f"{st.st_size}:{int(st.st_mtime)}"
            if cache.get(sid, {}).get("cle") == cle:
                info = cache[sid]["info"]
            else:
                i_close = i_bloc = i_user = -1
                dernier = ""
                for n, ligne in enumerate(tr.open(errors="ignore")):
                    if "omk:close" in ligne and '"role":"user"' in ligne.replace(" ", ""):
                        i_close = n
                    try:
                        m = json.loads(ligne).get("message") or {}
                    except Exception:
                        continue
                    c = m.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                    if not isinstance(c, str) or not c.strip():
                        continue
                    if m.get("role") == "assistant":
                        dernier = c.strip().replace("\n", " ")[:160]
                        if "Prochaines sessions recommand" in c:
                            i_bloc = n
                    elif m.get("role") == "user" and not c.lstrip().startswith(("<", "Base directory for this skill", "This session is being continued")):
                        i_user = n
                info = {"close": i_bloc >= 0 and i_bloc > i_user,
                        "dernier": dernier, "maj": d.get("updatedAt")}
                neuf[sid] = {"cle": cle, "info": info}
                continue
            neuf[sid] = cache[sid]
        neuf.setdefault(sid, {"cle": "", "info": info})
    try:
        cache_f.write_text(json.dumps({**cache, **neuf}))
    except Exception:
        pass
    res = []
    for f in sorted((racine / "sessions").glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        info = (neuf.get(d.get("sessionId")) or {}).get("info", {})
        res.append({"nom": d.get("name", d.get("sessionId", "")[:8]), "id": d.get("sessionId", ""),
                    "kind": d.get("kind", ""), "statut": d.get("status", ""),
                    "cwd": Path(d.get("cwd", "")).name, "maj": d.get("updatedAt"),
                    "close": bool(info.get("close")), "dernier": info.get("dernier", "")})
    return res


def lancees(evenements: list) -> set:
    """Sessions qui ont au moins un evenement au-dela du `planned` pose par l'orchestratrice."""
    return {ev["session"] for ev in evenements if ev["kind"] not in ("planned", "answer")}


def questions_en_attente(evenements: list) -> list:
    """Questions sans `answer` dont la ref est leur id (`id` propre, ou `session|ts` pour les
    questions posees avant son introduction)."""
    repondues = {ev.get("ref") for ev in evenements if ev["kind"] == "answer"}
    qs = [{**ev, "id": ev.get("id") or f"{ev['session']}|{ev['ts']}"}
          for ev in evenements if ev["kind"] == "question"]
    return [q for q in qs if q["id"] not in repondues]


LANCEMENTS = Path.home() / ".claude" / "orchestration" / "lancements"
# Sessions lancees autonomes : `auto` laisse passer les actions sures sans demander et ne
# soumet au proprietaire que les actions risquees (decision du proprietaire, 2026-10-15).
# Les hooks du depot cible (git-safety, etc.) restent actifs dans tous les cas.
MODE_PERMISSION = os.environ.get("ORCH_PERMISSION_MODE", "auto")


def script_lancement(ev: dict) -> Path:
    """Ecrit le lanceur d'une tache planifiee : un script bash (le Terminal ouvre le shell de
    l'utilisateur, fish ici, ou `"$(cat …)"` n'a pas la meme syntaxe) qui se place dans le
    depot et demarre `claude --model <modele>` avec le prompt d'amorcage en premier message."""
    # Le journal est ecrit par toute session : chaque valeur qui finit dans un script shell est
    # validee puis quotee (shlex), jamais json.dumps (en guillemets doubles, bash executerait
    # encore $(...) et les backticks). Le nom de session sert aussi de nom de fichier.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ev["session"]):
        raise SystemExit(f"nom de session refuse pour un lancement : {ev['session']!r}")
    if ev.get("model") and not re.fullmatch(r"[a-z0-9.\-\[\]]+", ev["model"]):
        raise SystemExit(f"modele refuse pour un lancement : {ev['model']!r}")
    LANCEMENTS.mkdir(parents=True, exist_ok=True)
    base = LANCEMENTS / ev["session"]
    base.with_suffix(".txt").write_text(ev["prompt"], encoding="utf-8")
    modele = (f" --model {shlex.quote(ev['model'])}" if ev.get("model") else "") \
        + f" --permission-mode {shlex.quote(MODE_PERMISSION)}"
    sh = base.with_suffix(".sh")
    sh.write_text("#!/bin/bash\n"
                  f"cd {shlex.quote(ev['cwd'])} || exit 1\n"
                  f"exec claude{modele} \"$(cat {shlex.quote(str(base.with_suffix('.txt')))})\"\n", encoding="utf-8")
    sh.chmod(0o755)
    return sh


def lancer(session: str, task: str, essai: bool = False, ident: str = "") -> str:
    """Ouvre un onglet Terminal sur une tache `planned` de (session, task) : celle d'id `ident` si
    donne (le pane passe l'id de l'evenement dont il a AFFICHE le prompt : un `planned` ecrit entre
    l'affichage et la confirmation ne peut pas s'y substituer ; le ts, a la seconde, n'est pas
    unique), sinon la plus recente."""
    evs = [e for e in lire() if e["kind"] == "planned" and e["session"] == session
           and e["task"] == task and e.get("prompt") and e.get("cwd") and (not ident or e.get("id") == ident)]
    if not evs:
        raise SystemExit(f"aucune tache planifiee lancable pour {session} {task} (--cwd et --prompt-file requis)")
    sh = script_lancement(evs[-1])
    if not essai:
        import subprocess
        commande = f"bash {shlex.quote(str(sh))}".replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script "{commande}"',
                        "-e", 'tell application "Terminal" to activate'], check=True)
        ajouter(session, task, "note", "lancee depuis /orch", model=evs[-1].get("model", ""))
    return str(sh)


def changer_modele(session: str, task: str, model: str, ident: str = "") -> dict:
    """Repose le dernier `planned` lancable de (session, task) avec un autre modele, avant son
    lancement. Le journal est append-only : on ne reecrit pas l'evenement, on en pose un nouveau
    qui recopie son depot et son prompt tels quels — `lancer` prend le plus recent, et l'ancien
    reste lisible. Le pane s'en sert pour arbitrer haiku/sonnet/opus au moment du lancement."""
    evs = [e for e in lire() if e["kind"] == "planned" and e["session"] == session
           and e["task"] == task and e.get("prompt") and e.get("cwd") and (not ident or e.get("id") == ident)]
    if not evs:
        raise SystemExit(f"aucune tache planifiee pour {session} {task}")
    p = evs[-1]
    return ajouter(session, task, "planned", p.get("msg", ""), model=model,
                   cwd=p.get("cwd", ""), prompt=p.get("prompt", ""))


# Questions <-> Google Chat (spec : docs/superpowers/specs/2026-10-15-orch-questions-google-chat.md).
# n8n porte la carte Chat et recoit les clics ; ici on n'envoie et ne relit que par son webhook,
# authentifie par un secret partage (~/.claude/orchestration/n8n.json, 600). Tout est best-effort :
# Chat indisponible ne doit jamais bloquer un `log` ni le pane.
N8N = Path.home() / ".claude" / "orchestration" / "n8n.json"
# Le secret vit seul dans son fichier (600), jamais dans n8n.json : un fichier affiche une fois
# dans une session y reste trace a chaque modification (incident du 2026-10-15).
SECRET = Path.home() / ".claude" / "orchestration" / "orch-secret"


def chat_post(action: str, payload: dict):
    """POST {action, ...payload} vers le webhook orch-api ; None si non configure ou en echec."""
    try:
        cfg = json.loads(N8N.read_text(encoding="utf-8"))
        cfg["secret"] = SECRET.read_text(encoding="utf-8").strip() if SECRET.exists() else ""
        if not cfg.get("url") or not cfg["secret"]:
            return None
        import urllib.request
        req = urllib.request.Request(
            cfg["url"].rstrip("/") + "/webhook/orch-api",
            data=json.dumps({"action": action, **payload}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "x-orch-secret": cfg["secret"]}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            corps = r.read().decode() or "{}"
            return json.loads(corps)
    except Exception:
        return None


def chat_pull() -> list:
    """Reponses donnees dans Chat depuis le dernier passage : ecrites au journal (une fois, et
    seulement si la question attend encore), renvoyees pour que le pane les soumette."""
    rep = chat_post("pull", {}) or {}
    attente = {q["id"]: q for q in questions_en_attente(lire())}
    recues = []
    for a in rep.get("answers", []):
        q = attente.get(a.get("qid"))
        if not q or not a.get("answer"):
            continue  # deja repondue (pane ou pull precedent) : rien a faire
        ajouter("PROPRIETAIRE", q["task"], "answer", str(a["answer"]), ref=q["id"])
        recues.append({"qid": q["id"], "session": q["session"], "task": q["task"],
                       "msg": q["msg"], "answer": str(a["answer"])})
    return recues


def etat(evenements: list) -> dict:
    """Dernier evenement par (session, tache), plus la date de DEBUT de cette tache."""
    courant = {}
    debut = {}
    for ev in evenements:
        if ev["kind"] in HORS_ETAT:
            continue
        cle = (ev["session"], ev["task"])
        courant[cle] = ev
        debut.setdefault(cle, ev["ts"])
        if ev["kind"] == "start":
            debut[cle] = ev["ts"]  # une relance repart du nouveau start
    return {cle: {"ev": ev, "debut": debut[cle]} for cle, ev in courant.items()}


# Palette OMKomUnity — relevee dans frontend/src/styles/design-system.css du site (2026-10-15).
# Dupliquee ici volontairement : ce script vit dans le boilerplate et doit tourner dans
# n'importe quel depot, sans lire le CSS d'un projet particulier. Si la charte bouge, c'est
# cette constante qu'on met a jour.
CHARTE = {
    "nuit": "#080D26",      # fond dominant
    "paper": "#F7F5F0",     # texte clair
    "muted": "#E6E9F2",     # texte secondaire
    "magenta": "#EF4E9B",   # signature / CTA
    "violet": "#A855F7",    # signature IA
    "indigo": "#818CF8",    # liant froid
    "ciel": "#4AA8D8",
    "teal": "#52C9B4",      # froid dominant
    "or": "#EFC137",
    "saumon": "#E85269",
}
# Une couleur par etat — reprise telle quelle dans l'en-tete SVG et dans les puces.
COULEUR_ETAT = {
    "en cours": CHARTE["ciel"],
    "review": CHARTE["or"],
    "bloque": CHARTE["saumon"],
    "termine": CHARTE["teal"],
    "a lancer": CHARTE["muted"],
}


def _xml(t: str) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def svg_entete(compteurs: list) -> str:
    """Bandeau de l'en-tete, aux couleurs du site. Regenere a chaque rendu.

    SVG LOCAL et autonome : pas de badge distant (shields.io et consorts exigent le
    reseau a chaque affichage et fuitent l'etat du projet vers un tiers). Les polices
    sont declarees avec repli systeme — Poppins et JetBrains Mono ne sont pas garanties
    hors navigateur, le bandeau doit rester lisible sans elles.
    """
    L, H = 920, 132
    x = 470           # debut de la zone des compteurs
    pas = 92
    pastilles = []
    for i, (valeur, libelle, cle) in enumerate(compteurs):
        cx = x + i * pas
        couleur = COULEUR_ETAT[cle]
        creux = cle == "a lancer" or valeur == 0
        pastilles.append(f'''
  <circle cx="{cx}" cy="56" r="21" fill="{couleur}" fill-opacity="{0.14 if creux else 0.20}"
          stroke="{couleur}" stroke-opacity="{0.45 if creux else 1}" stroke-width="1.5"/>
  <text x="{cx}" y="63" text-anchor="middle" font-family="Poppins, Helvetica Neue, Arial, sans-serif"
        font-size="19" font-weight="700" fill="{couleur}" fill-opacity="{0.55 if creux else 1}">{valeur}</text>
  <text x="{cx}" y="95" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, monospace"
        font-size="8.5" letter-spacing="1.2" fill="{CHARTE['muted']}" fill-opacity="0.6">{_xml(libelle.upper())}</text>''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{L}" height="{H}" viewBox="0 0 {L} {H}" role="img">
  <defs>
    <linearGradient id="filet" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{CHARTE['magenta']}"/>
      <stop offset="45%" stop-color="{CHARTE['violet']}"/>
      <stop offset="100%" stop-color="{CHARTE['ciel']}"/>
    </linearGradient>
    <linearGradient id="fond" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{CHARTE['nuit']}"/>
      <stop offset="100%" stop-color="#121a3d"/>
    </linearGradient>
  </defs>
  <rect width="{L}" height="{H}" rx="14" fill="url(#fond)"/>
  <rect x="0" y="0" width="{L}" height="3" rx="1.5" fill="url(#filet)"/>

  <text x="34" y="48" font-family="Poppins, Helvetica Neue, Arial, sans-serif" font-size="21" font-weight="700">
    <tspan fill="{CHARTE['paper']}">OM</tspan><tspan fill="{CHARTE['magenta']}">Kom</tspan><tspan fill="{CHARTE['teal']}">Unity</tspan>
  </text>
  <text x="34" y="76" font-family="Poppins, Helvetica Neue, Arial, sans-serif" font-size="15" font-weight="600"
        fill="{CHARTE['paper']}" fill-opacity="0.92">Orchestration multi-sessions</text>
  <text x="34" y="99" font-family="JetBrains Mono, ui-monospace, monospace" font-size="9.5" letter-spacing="1.1"
        fill="{CHARTE['muted']}" fill-opacity="0.55">{_xml(datetime.now().strftime("%d/%m/%Y · %H:%M"))}</text>

  <line x1="440" y1="26" x2="440" y2="106" stroke="{CHARTE['paper']}" stroke-opacity="0.09" stroke-width="1"/>
{''.join(pastilles)}
</svg>
'''


def dashboard_html() -> str:
    """Panneau HTML autonome, aux couleurs du site — le « mod d'interface ».

    A ouvrir dans une fenetre de navigateur calee a droite de l'ecran. Autonome et
    hors-ligne : styles en ligne, aucune police ni ressource distante (une police
    Google ferait fuiter l'ouverture du panneau vers un tiers, et le rendrait blanc
    sans reseau). Se rafraichit seul toutes les 10 s via <meta http-equiv="refresh">,
    ce qui evite un process de surveillance a maintenir.
    """
    evenements = lire()
    st = etat(evenements)
    grp = lambda *k: [v for v in st.values() if v["ev"]["kind"] in k]
    en_cours, attente = grp("start", "progress"), grp("review-request")
    bloques, rejetes = grp("blocked"), grp("review-reject")
    finis, planifies = grp("done", "review-ok"), grp("planned")
    C = CHARTE

    def carte(v, couleur):
        e = v["ev"]
        ref = f'<span class="ref">{_xml(e["ref"])}</span>' if e.get("ref") else ""
        return (f'<div class="c" style="border-left-color:{couleur}">'
                f'<div class="t">{_xml(e["task"])}<span class="s">{_xml(e["session"])}</span>'
                f'<span class="d">{duree(v["debut"])}</span></div>'
                f'<div class="m">{_xml(e["msg"])}{ref}</div></div>')

    def section(titre, items, couleur):
        if not items:
            return ""
        corps = "".join(carte(v, couleur) for v in sorted(items, key=lambda v: v["debut"]))
        return (f'<h2 style="color:{couleur}">{titre}<span class="n">{len(items)}</span></h2>{corps}')

    compteurs = "".join(
        f'<div class="k"><div class="v" style="color:{COULEUR_ETAT[c]};'        f'border-color:{COULEUR_ETAT[c]}">{n}</div><div class="l">{l}</div></div>'
        for n, l, c in [(len(en_cours), "en cours", "en cours"), (len(attente), "review", "review"),
                        (len(bloques), "bloqué", "bloque"), (len(finis), "terminé", "termine"),
                        (len(planifies), "à lancer", "a lancer")])

    journal = "".join(
        f'<li><span class="h">{horodatage(e["ts"]).astimezone().strftime("%H:%M")}</span>'
        f'<b style="color:{COULEUR_ETAT.get({"start":"en cours","progress":"en cours","review-request":"review","blocked":"bloque","done":"termine","review-ok":"termine","review-reject":"bloque"}.get(e["kind"], "a lancer"))}">'
        f'{_xml(e["session"])}</b> {_xml(e["task"])} · {_xml(e["kind"])} — {_xml(e["msg"])}</li>'
        for e in reversed(evenements[-18:]))

    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Orchestration — OMKomUnity</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:{C["nuit"]};color:{C["paper"]};font:14px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif;padding:18px;min-height:100vh}}
header{{background:linear-gradient(135deg,{C["nuit"]},#141c42);border-radius:14px;padding:18px 20px;position:relative;overflow:hidden;margin-bottom:18px}}
header::before{{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:linear-gradient(90deg,{C["magenta"]},{C["violet"]} 45%,{C["ciel"]})}}
.wm{{font-size:19px;font-weight:700;letter-spacing:-.2px}}
.wm i{{font-style:normal}}
h1{{font-size:14px;font-weight:600;opacity:.9;margin-top:3px}}
.ts{{font:10px/1.4 ui-monospace,monospace;color:{C["muted"]};opacity:.5;letter-spacing:.9px;margin-top:5px}}
.kpi{{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}}
.k{{text-align:center;flex:1;min-width:64px}}
.v{{font-size:19px;font-weight:700;border:1.5px solid;border-radius:50%;width:44px;height:44px;
   display:flex;align-items:center;justify-content:center;margin:0 auto;background:rgba(255,255,255,.04)}}
.l{{font:8.5px/1.4 ui-monospace,monospace;letter-spacing:1px;text-transform:uppercase;color:{C["muted"]};opacity:.6;margin-top:6px}}
h2{{font-size:11px;letter-spacing:1.4px;text-transform:uppercase;margin:20px 0 9px;display:flex;align-items:center;gap:8px}}
.n{{background:rgba(255,255,255,.08);border-radius:9px;padding:1px 7px;font-size:10px;color:{C["muted"]}}}
.c{{background:rgba(255,255,255,.035);border-left:3px solid;border-radius:0 9px 9px 0;padding:9px 12px;margin-bottom:7px}}
.t{{font-weight:600;font-size:13px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
.s{{font:9.5px/1 ui-monospace,monospace;color:{C["ciel"]};opacity:.85}}
.d{{margin-left:auto;font:9.5px/1 ui-monospace,monospace;color:{C["muted"]};opacity:.45}}
.m{{font-size:12px;color:{C["muted"]};opacity:.72;margin-top:3px}}
.ref{{font:9.5px/1 ui-monospace,monospace;color:{C["or"]};opacity:.8;margin-left:7px}}
ul{{list-style:none;border-top:1px solid rgba(255,255,255,.07);padding-top:9px}}
li{{font-size:11.5px;color:{C["muted"]};opacity:.62;padding:2.5px 0}}
.h{{font:9.5px/1 ui-monospace,monospace;opacity:.5;margin-right:7px}}
footer{{margin-top:18px;font:9.5px/1.5 ui-monospace,monospace;color:{C["muted"]};opacity:.35}}
</style></head><body>
<header>
  <div class="wm"><i style="color:{C["paper"]}">OM</i><i style="color:{C["magenta"]}">Kom</i><i style="color:{C["teal"]}">Unity</i></div>
  <h1>Orchestration multi-sessions</h1>
  <div class="ts">{datetime.now().strftime("%d/%m/%Y · %H:%M")}</div>
  <div class="kpi">{compteurs}</div>
</header>
{section("Bloqué — action requise", bloques, C["saumon"])}
{section("Review en attente", attente, C["or"])}
{section("En cours", en_cours, C["ciel"])}
{section("Renvoyé en correction", rejetes, C["saumon"])}
{section("Terminé", finis, C["teal"])}
{section("À lancer", planifies, C["muted"])}
<h2 style="color:{C["muted"]}">Journal</h2><ul>{journal}</ul>
<footer>{len(evenements)} événement(s) · rafraîchissement automatique toutes les 10 s</footer>
</body></html>'''


def rendre() -> str:
    evenements = lire()
    st = etat(evenements)
    maintenant = datetime.now().strftime("%d/%m %H:%M")

    en_cours = [v for v in st.values() if v["ev"]["kind"] in ("start", "progress")]
    attente = [v for v in st.values() if v["ev"]["kind"] == "review-request"]
    bloques = [v for v in st.values() if v["ev"]["kind"] == "blocked"]
    rejetes = [v for v in st.values() if v["ev"]["kind"] == "review-reject"]
    finis = [v for v in st.values() if v["ev"]["kind"] in ("done", "review-ok")]
    planifies = [v for v in st.values() if v["ev"]["kind"] == "planned"]

    compteurs = [(len(en_cours), "en cours", "en cours"), (len(attente), "review", "review"),
                 (len(bloques), "bloque", "bloque"), (len(finis), "termine", "termine"),
                 (len(planifies), "a lancer", "a lancer")]
    svg = chemin_etat().parent / "entete.svg"
    try:
        svg.write_text(svg_entete(compteurs), encoding="utf-8")
        entete = f"![Orchestration]({os.path.relpath(svg, chemin_sortie().parent)})"
    except Exception:
        entete = f"# Orchestration — {maintenant}"  # repli : jamais de rendu vide pour un SVG

    L = [entete, ""]
    L.append(f"**{len(en_cours)} en cours · {len(attente)} en attente de review · "
             f"{len(bloques)} bloquée(s) · {len(finis)} terminée(s) · {len(planifies)} à lancer**")
    L.append("")

    qs = questions_en_attente(lire())
    if qs:
        L += ["## ❓ Questions en attente", ""]
        for q in qs:
            opts = " · ".join(f"**{i}.** {o}" for i, o in enumerate(q.get("options", []), 1))
            L.append(f"- `{q['session']}` {q['task']} — {q['msg']}" + (f"  \n  {opts}" if opts else ""))
        L.append("")

    if bloques:
        L += ["## 🔴 Bloqué — action requise", ""]
        for v in sorted(bloques, key=lambda v: v["ev"]["ts"]):
            e = v["ev"]
            L.append(f"- **{e['task']}** · `{e['session']}` · {duree(v['debut'])} — {e['msg']}")
        L.append("")

    if attente:
        L += ["## 🟡 Review orchestratrice en attente", ""]
        for v in sorted(attente, key=lambda v: v["ev"]["ts"]):
            e = v["ev"]
            ref = f" · {e['ref']}" if e.get("ref") else ""
            L.append(f"- [ ] **{e['task']}** · `{e['session']}` · depuis {duree(e['ts'])} — {e['msg']}{ref}")
        L.append("")

    sessions = modeles(lire())
    if sessions:
        L += ["## Sessions", "", "| Session | Modèle | Alerte |", "|---|---|---|"]
        for s, m in sorted(sessions.items()):
            a = alerte_modele(s, m)
            L.append(f"| `{s}` | {m or '—'} | {'⚠️ ' + a if a else ''} |")
        L.append("")

    L += ["## En cours", ""]
    if en_cours:
        L += ["| Session | Tâche | Depuis | État |", "|---|---|---|---|"]
        for v in sorted(en_cours, key=lambda v: v["debut"]):
            e = v["ev"]
            L.append(f"| `{e['session']}` | {e['task']} — {e['msg']} | {duree(v['debut'])} | {PUCE[e['kind']]} |")
    else:
        L.append("_Aucune tâche en cours._")
    L.append("")

    if rejetes:
        L += ["## ❌ Renvoyé en correction", ""]
        for v in sorted(rejetes, key=lambda v: v["ev"]["ts"], reverse=True):
            e = v["ev"]
            L.append(f"- **{e['task']}** · `{e['session']}` — {e['msg']}")
        L.append("")

    if finis:
        L += ["## ✅ Terminé", ""]
        for v in sorted(finis, key=lambda v: v["ev"]["ts"], reverse=True):
            e = v["ev"]
            ref = f" · `{e['ref']}`" if e.get("ref") else ""
            L.append(f"- **{e['task']}** · `{e['session']}` — {e['msg']}{ref}")
        L.append("")

    if planifies:
        L += ["## ⚪ À lancer", ""]
        for v in sorted(planifies, key=lambda v: (v["ev"]["session"], v["ev"]["task"])):
            e = v["ev"]
            ref = f" · {e['ref']}" if e.get("ref") else ""
            L.append(f"- **{e['task']}** · `{e['session']}` — {e['msg']}{ref}")
        L.append("")

    L += ["## Journal", "", "_20 derniers événements, du plus récent au plus ancien._", ""]
    for ev in reversed(evenements[-20:]):
        heure = horodatage(ev["ts"]).astimezone().strftime("%H:%M")
        ref = f" · {ev['ref']}" if ev.get("ref") else ""
        L.append(f"- `{heure}` {PUCE.get(ev['kind'], '·')} **{ev['session']}** · {ev['task']} · "
                 f"{ev['kind']} — {ev['msg']}{ref}")
    if not evenements:
        L.append("_Aucun événement._")

    L += ["", "---", "",
          f"_{len(evenements)} événement(s) · source `{chemin_etat()}` · "
          "réécrit à chaque `orch.py log`._"]
    return "\n".join(L) + "\n"


def ecrire_rendu() -> Path:
    """Ecrit les DEUX sorties : le Markdown (onglet IDE) et le panneau HTML (navigateur).

    Les deux lisent le meme journal, donc ne peuvent pas diverger. On ecrit toujours les
    deux : laisser l'un des deux perimer serait pire que ne pas l'avoir — c'est la panne
    silencieuse d'un tableau de bord qui coute le plus cher.
    """
    out = chemin_sortie()
    out.write_text(rendre(), encoding="utf-8")
    try:
        (chemin_etat().parent / "dashboard.html").write_text(dashboard_html(), encoding="utf-8")
    except Exception:
        pass  # le Markdown reste la sortie de reference ; un panneau absent ne bloque rien
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("log", help="ajoute un evenement et reecrit le rendu")
    pl.add_argument("--session", required=True)
    pl.add_argument("--task", required=True)
    pl.add_argument("--kind", required=True, choices=KINDS)
    pl.add_argument("--msg", required=True)
    pl.add_argument("--ref", default="", help="commit, chemin, nombre de fichiers...")
    pl.add_argument("--cwd", default="", help="kind planned : depot ou lancer la session")
    pl.add_argument("--prompt-file", default="", help="kind planned : fichier du prompt d'amorcage")
    pl.add_argument("--options", default="", help="kind question : reponses proposees, separees par |")
    pl.add_argument("--model", default="", help="modele de la session : haiku par defaut (mission "
                    "cadree), sonnet si elle demande de l'arbitrage, jamais opus pour un executant ; "
                    "a declarer au premier evenement, il vaut ensuite pour toute la session")

    sub.add_parser("render", help="reecrit le rendu sans ajouter d'evenement")
    sub.add_parser("status", help="resume court sur stdout")
    pla = sub.add_parser("lancer", help="ouvre un onglet Terminal sur une tache planifiee")
    pla.add_argument("--session", required=True)
    pla.add_argument("--task", required=True)
    pla.add_argument("--essai", action="store_true", help="ecrit le lanceur sans ouvrir le Terminal")
    pla.add_argument("--id", default="", help="lance exactement l'evenement planned de cet id")
    pmo = sub.add_parser("modele", help="repose une tache planifiee avec un autre modele")
    pmo.add_argument("--session", required=True)
    pmo.add_argument("--task", required=True)
    pmo.add_argument("--model", required=True)
    pmo.add_argument("--id", default="", help="repose exactement l'evenement planned de cet id")
    sub.add_parser("chat-pull", help="recupere les reponses donnees dans Google Chat (JSON)")
    sub.add_parser("snapshot", help="JSON pour le pane : journal, contexte par session, questions")

    a = p.parse_args()
    if a.cmd in ("snapshot", "status", "render", "lancer", "chat-pull", "modele"):
        lire_pointeur()
    if a.cmd == "chat-pull":
        print(json.dumps(chat_pull(), ensure_ascii=False))
        return 0
    if a.cmd == "lancer":
        print(lancer(a.session, a.task, a.essai, a.id))
        return 0
    if a.cmd == "modele":
        ev = changer_modele(a.session, a.task, a.model, a.id)
        ecrire_rendu()
        print(f"[orch] {a.session} · {a.task} · planned → {a.model} (id {ev.get('id')})")
        return 0
    if a.cmd == "snapshot":
        ev = lire()
        print(json.dumps({"journal": str(chemin_etat()), "out": str(chemin_sortie()),
                          "existe": chemin_etat().exists(), "evenements": ev,
                          "contextes": contextes(ev), "questions": questions_en_attente(ev),
                          "terminees": sessions_terminees(ev), "lancees": sorted(lancees(ev)),
                          "vivantes": vivantes()},
                         ensure_ascii=False))
        return 0
    if a.cmd == "log":
        # `planned` est pose par l'orchestratrice POUR une autre session, `answer` par le pane :
        # ni l'un ni l'autre ne doit lier le transcript de l'appelant a la session nommee.
        lie = a.kind not in ("planned", "answer")
        if lie:
            tr = trouver_transcript(a.session)
            deja = {e["transcript"]: e["session"] for e in lire() if e.get("transcript")}
            lie = tr if tr and deja.get(tr, a.session) == a.session else None
        ev = ajouter(a.session, a.task, a.kind, a.msg, a.ref, a.model,
                [o.strip() for o in a.options.split("|") if o.strip()],
                lie or None,
                a.cwd, Path(a.prompt_file).read_text(encoding="utf-8") if a.prompt_file else "")
        ecrire_pointeur()
        if a.kind == "question":
            chat_post("question", {"qid": ev.get("id"), "session": a.session, "task": a.task,
                                   "msg": a.msg, "options": ev.get("options", [])})
        elif a.kind == "answer" and a.ref:
            chat_post("answered_local", {"qid": a.ref, "answer": a.msg})
        print(f"[orch] {a.session} · {a.task} · {a.kind} → {ecrire_rendu()}")
    elif a.cmd == "render":
        print(f"[orch] rendu → {ecrire_rendu()}")
    else:
        # Nomme TOUJOURS le journal lu. Un « 0 en cours » muet ne distingue pas « rien a
        # faire » de « mauvais depot » : lancee depuis un autre repertoire sans
        # CLAUDE_PROJECT_DIR, la commande lit un journal vide et affiche des zeros
        # parfaitement credibles. Constate en conditions reelles le 2026-10-15.
        journal = chemin_etat()
        if not journal.exists():
            print(f"AUCUN JOURNAL a {journal}", file=sys.stderr)
            print("Lance la commande depuis le depot orchestre, ou passe "
                  "CLAUDE_PROJECT_DIR=<depot>.", file=sys.stderr)
            return 1
        st = etat(lire())
        n = lambda *k: sum(1 for v in st.values() if v["ev"]["kind"] in k)
        print(f"{n('start', 'progress')} en cours · {n('review-request')} review · "
              f"{n('blocked')} bloqué · {n('done', 'review-ok')} fini   [{journal}]")
    return 0


def _autotest() -> None:
    """Self-check : `python3 orch.py --autotest` dans un dossier temporaire."""
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    os.environ["ORCH_DIR"] = str(Path(tmp) / "etat")
    os.environ["ORCH_OUT"] = str(Path(tmp) / "OUT.md")
    try:
        ajouter("s1", "#1", "start", "demarre")
        ajouter("s2", "#2", "start", "demarre")
        ajouter("s1", "#1", "review-request", "diff pret", "3 fichiers")
        ajouter("s1", "#1", "review-ok", "valide", "abc123")
        ajouter("s2", "#2", "blocked", "manque une decision")
        md = rendre()
        # s1/#1 est passe en review-ok, s2/#2 en blocked : plus rien « en cours ».
        assert "0 en cours · 0 en attente de review · 1 bloquée(s) · 1 terminée(s) · 0 à lancer" in md, md[:400]
        assert "🔴 Bloqué" in md and "✅ Terminé" in md
        assert "🟡 Review orchestratrice en attente" not in md, "review resolue ne doit plus attendre"
        # le DERNIER evenement gagne, meme si un kind "plus grave" le precede
        ajouter("s2", "#2", "done", "debloque et fini")
        assert "0 bloquée(s)" in rendre()
        # une ligne corrompue ne casse pas la lecture
        with chemin_etat().open("a", encoding="utf-8") as fh:
            fh.write("{tronque\n")
        assert len(lire()) == 6
        ajouter("s3", "#3", "planned", "pas encore lancee")
        assert "⚪ À lancer" in rendre() and "1 à lancer" in rendre()
        # modele par session : declare une fois, porte ensuite ; absent ou Opus executant = alerte
        ajouter("s4", "#4", "planned", "maillage", model="sonnet")
        ajouter("s4", "#4", "start", "go")
        ajouter("s5", "#5", "planned", "gros lot", model="opus")
        ajouter("ORCHESTRATRICE", "#0", "note", "pilote", model="opus")
        m = modeles(lire())
        assert m["s4"] == "sonnet" and m["s1"] == "", m
        assert alerte_modele("s4", m["s4"]) == ""
        assert alerte_modele("s1", "") == "modèle non déclaré"
        assert alerte_modele("s5", "opus") == "exécutant en Opus"
        assert alerte_modele("ORCHESTRATRICE", "opus") == ""
        assert "| `s4` | sonnet |" in rendre() and "⚠️ exécutant en Opus" in rendre()
        # questions : ne touchent pas l'etat de la tache, en attente jusqu'a un answer qui les cite
        q = ajouter("s4", "#4", "question", "Lever le verrou ?", options=["oui", "non"])
        assert etat(lire())[("s4", "#4")]["ev"]["kind"] == "start", "une question ne change pas l'etat"
        assert [x["msg"] for x in questions_en_attente(lire())] == ["Lever le verrou ?"]
        assert "❓ Questions en attente" in rendre() and "**2.** non" in rendre()
        q2 = ajouter("s4", "#4", "question", "Et le second ?", options=["a", "b"])
        assert q2["ts"][:16] == q["ts"][:16] and q2["id"] != q["id"], "deux questions, deux ids"
        ajouter("PROPRIETAIRE", "#4", "answer", "oui", ref=q["id"])
        assert [x["msg"] for x in questions_en_attente(lire())] == ["Et le second ?"], \
            "repondre a une question ne retire pas sa voisine de la meme seconde"
        ajouter("PROPRIETAIRE", "#4", "answer", "a", ref=q2["id"])
        assert questions_en_attente(lire()) == []
        # contexte : lu dans le dernier message assistant porteur d'usage
        tr = Path(tmp) / "t.jsonl"
        tr.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5-5",
                      "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 9000,
                                "cache_read_input_tokens": 40000}}}) + "\n"
                      + json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")
        ajouter("s4", "#4", "progress", "suite", transcript=str(tr))
        # lancement : le lanceur cible le depot et le modele, et porte le prompt intact
        global LANCEMENTS
        LANCEMENTS = Path(tmp) / "lanc"
        ajouter("s6", "#6", "planned", "a lancer", model="haiku", cwd="/tmp/depot x", prompt='Tu es "s6".')
        # TOCTOU : un planned ecrit apres l'affichage (meme seconde) ne remplace pas celui affiche
        ev6 = ajouter("s6", "#6", "planned", "a lancer", model="haiku", cwd="/tmp/depot x", prompt='Tu es "s6".')
        ajouter("s6", "#6", "planned", "substitue", model="haiku", cwd="/tmp/depot x", prompt="PIEGE")
        sh = Path(lancer("s6", "#6", essai=True, ident=ev6["id"])).read_text()
        assert "cd '/tmp/depot x'" in sh and "exec claude --model haiku --permission-mode auto" in sh, sh
        # injection : un cwd piege reste une chaine, un nom de session ou un modele piege est refuse
        ajouter("s7", "#7", "planned", "x", model="haiku", cwd='/tmp/$(touch pwn)', prompt="p")
        assert "cd '/tmp/$(touch pwn)'" in Path(lancer("s7", "#7", essai=True)).read_text()
        for sess, mod in (("../evil", "haiku"), ("s8", "haiku; rm -rf ~")):
            ajouter(sess, "#8", "planned", "x", model=mod, cwd="/tmp", prompt="p")
            try:
                lancer(sess, "#8", essai=True)
                raise AssertionError(f"lancement accepte : {sess!r} {mod!r}")
            except SystemExit:
                pass
        assert (Path(tmp) / "lanc" / "s6.txt").read_text() == 'Tu es "s6".'
        # changer de modele avant lancement : un evenement de plus (append-only), depot et prompt
        # recopies tels quels, et `lancer` prend bien le plus recent
        ajouter("s10", "#10", "planned", "a lancer", model="haiku", cwd="/tmp/depot", prompt="Tu es s10.")
        avant = len(lire())
        neuf = changer_modele("s10", "#10", "sonnet")
        assert len(lire()) == avant + 1, "le journal doit rester append-only"
        assert neuf["model"] == "sonnet" and neuf["prompt"] == "Tu es s10." and neuf["cwd"] == "/tmp/depot", neuf
        assert "exec claude --model sonnet" in Path(lancer("s10", "#10", essai=True)).read_text(), \
            "lancer doit prendre le planned repose"
        try:
            changer_modele("inconnue", "#0", "sonnet")
            raise AssertionError("changer_modele accepte une tache inexistante")
        except SystemExit:
            pass
        # Chat : une reponse recue s'ecrit une fois, et jamais pour une question deja repondue
        global chat_post
        q9 = ajouter("s9", "#9", "question", "Via Chat ?", options=["oui", "non"])
        vrai_post = chat_post
        chat_post = lambda action, payload: {"answers": [{"qid": q9["id"], "answer": "oui"},
                                                         {"qid": "inconnu", "answer": "x"}]}
        recues = chat_pull()
        assert [r["answer"] for r in recues] == ["oui"] and recues[0]["session"] == "s9", recues
        assert chat_pull() == [], "une reponse deja ecrite ne revient pas"
        chat_post = vrai_post
        global STATUSLINE, CACHE_COMPACT
        STATUSLINE, CACHE_COMPACT = Path(tmp) / "sl", Path(tmp) / "cc.json"
        c = contextes(lire())["s4"]
        assert c["tokens"] == 50000 and c["pct"] == 25 and c["compactions"] == 1 and c["estime"], c
        # la statusline de la session fait foi : fenetre exacte, plus d'estimation
        STATUSLINE.mkdir()
        (STATUSLINE / "t.json").write_text(json.dumps({"window": 1000000, "pct": 5, "tokens": 50000, "model": "m[1m]"}))
        c = contextes(lire())["s4"]
        assert c["fenetre"] == 1000000 and c["pct"] == 5 and not c["estime"], c
        # compactions comptees en increment : une compaction de plus, une de plus
        with tr.open("a") as fh:
            fh.write(json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")
        assert contextes(lire())["s4"]["compactions"] == 2
        # le proprietaire n'est pas une session ; une session finie se replie
        assert "PROPRIETAIRE" not in modeles(lire())
        assert "s1" in sessions_terminees(lire()) and "s4" not in sessions_terminees(lire())
        md = rendre()
        assert md.startswith("![Orchestration]("), md[:80]
        svg = (chemin_etat().parent / "entete.svg").read_text(encoding="utf-8")
        assert svg.startswith("<svg") and CHARTE["magenta"] in svg and "OM" in svg
        assert svg.count("<circle") == 5, "cinq compteurs attendus dans l'en-tete"
        ecrire_rendu()
        html = (chemin_etat().parent / "dashboard.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>") and CHARTE["magenta"] in html
        assert "http-equiv=\"refresh\"" in html, "le panneau doit se rafraichir seul"
        assert "https://" not in html.split("<style>")[0], "aucune ressource distante"
        ecrire_rendu()
        assert Path(os.environ["ORCH_OUT"]).exists()
        # status sur un depot sans journal doit ECHOUER bruyamment, pas afficher des zeros
        import io, contextlib
        os.environ["ORCH_DIR"] = str(Path(tmp) / "vide")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sys.argv = ["orch.py", "status"]
            code = main()
        assert code == 1 and "AUCUN JOURNAL" in err.getvalue(), (code, err.getvalue())
        print("autotest orch.py : OK (11 evenements, 1 ligne corrompue ignoree, "
              "status sans journal = exit 1)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        _autotest()
    else:
        sys.exit(main())
