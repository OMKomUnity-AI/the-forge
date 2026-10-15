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
  orch.py log --session sonnet-maillage --task "#1" --kind start --msg "liens vers /comprendre"
  orch.py log --session sonnet-maillage --task "#1" --kind review-request --msg "diff pret" --ref 3 fichiers
  orch.py log --session ORCHESTRATRICE --task "#1" --kind review-ok --msg "6 ancres distinctes, verifie"
  orch.py render            # recalcule le Markdown sans ajouter d'evenement
  orch.py status            # resume sur stdout (pour une statusline ou un coup d'oeil)

Codes : 0 = ok, 1 = erreur d'usage.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("planned", "start", "progress", "review-request", "review-ok", "review-reject", "done", "blocked", "note")
# Un seul etat courant par (session, tache) : le DERNIER evenement gagne. L'ordre ci-dessous
# ne sert qu'a l'affichage, jamais a decider — sinon un `done` tardif serait masque par un
# `blocked` plus ancien.
PUCE = {"planned": "⚪", "start": "🟢", "progress": "🟢", "review-request": "🟡", "review-ok": "✅",
        "review-reject": "❌", "done": "✅", "blocked": "🔴", "note": "·"}


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


def ajouter(session: str, task: str, kind: str, msg: str, ref: str = "") -> dict:
    ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "session": session, "task": task, "kind": kind, "msg": msg, "ref": ref}
    with chemin_etat().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


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


def etat(evenements: list) -> dict:
    """Dernier evenement par (session, tache), plus la date de DEBUT de cette tache."""
    courant = {}
    debut = {}
    for ev in evenements:
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

    sub.add_parser("render", help="reecrit le rendu sans ajouter d'evenement")
    sub.add_parser("status", help="resume court sur stdout")

    a = p.parse_args()
    if a.cmd == "log":
        ajouter(a.session, a.task, a.kind, a.msg, a.ref)
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
        print("autotest orch.py : OK (7 evenements, 1 ligne corrompue ignoree, "
              "status sans journal = exit 1)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        _autotest()
    else:
        sys.exit(main())
