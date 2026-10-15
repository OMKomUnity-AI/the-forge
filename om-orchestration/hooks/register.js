// om-orchestration — un pane lateral qui suit l'etat de sessions Claude Code orchestrees.
//
// Pourquoi un mod et pas un fichier : une statusline tient sur une ligne, un Markdown ouvert
// dans l'IDE vit hors de Claude Code. Un mod dessine A COTE du transcript, dans la session ou
// l'on travaille — c'est le seul endroit ou l'etat est sous les yeux au moment ou l'on decide.
//
// Source de verite : .claude/orchestration/events.jsonl, un journal APPEND-ONLY ecrit par
// toutes les sessions (voir scripts/orch.py). Ce mod ne fait que LIRE. Il n'ecrit jamais dans
// le journal, pour deux raisons : plusieurs sessions ecrivent en concurrence, et un afficheur
// qui modifie ce qu'il affiche finit toujours par mentir sur ce qu'il a lui-meme change.


const PANE = 'om-orchestration'
const JOURNAL = '.claude/orchestration/events.jsonl'
const RAFRAICHISSEMENT_MS = 2000

// Charte OMKomUnity (frontend/src/styles/design-system.css). Les terminaux modernes rendent
// l'hexadecimal ; ceux qui ne le font pas retombent sur leur palette la plus proche, ce qui
// reste lisible — c'est pourquoi aucune information ne repose sur la couleur SEULE.
const C = {
  paper: '#F7F5F0',
  muted: '#E6E9F2',
  magenta: '#EF4E9B',
  teal: '#52C9B4',
  ciel: '#4AA8D8',
  or: '#EFC137',
  saumon: '#E85269',
}

const COULEUR = {
  'start': C.ciel, 'progress': C.ciel,
  'review-request': C.or,
  'blocked': C.saumon, 'review-reject': C.saumon,
  'done': C.teal, 'review-ok': C.teal,
  'planned': C.muted, 'note': C.muted,
}
const PUCE = {
  'start': '>', 'progress': '>', 'review-request': '?',
  'blocked': '!', 'review-reject': 'x', 'done': 'v', 'review-ok': 'v',
  'planned': '-', 'note': '.',
}

/** Dernier evenement par (session, tache), plus l'heure de debut de la tache.
 *  Le DERNIER evenement fait foi : sans cela un `done` tardif serait masque par un
 *  `blocked` plus ancien, et le pane mentirait dans le sens le plus couteux. */
function etat(evenements) {
  const courant = new Map()
  const debut = new Map()
  for (const ev of evenements) {
    const cle = ev.session + '\u0000' + ev.task
    courant.set(cle, ev)
    if (!debut.has(cle) || ev.kind === 'start') debut.set(cle, ev.ts)
  }
  return [...courant.entries()].map(([cle, ev]) => ({ ev, debut: debut.get(cle) }))
}

function duree(iso) {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 1) return '<1m'
  if (m < 60) return m + 'm'
  return Math.floor(m / 60) + 'h' + String(m % 60).padStart(2, '0')
}

/** Modele de chaque session : le DERNIER declare (champ `model`, orch.py log --model), '' sinon.
 *  Meme regle que modeles()/alerte_modele() dans orch.py — les deux rendus ne divergent pas. */
function modeles(evenements) {
  const out = new Map()
  for (const ev of evenements) {
    if (!out.has(ev.session)) out.set(ev.session, '')
    if (ev.model) out.set(ev.session, ev.model)
  }
  return out
}

function alerteModele(session, model) {
  if (!model) return 'modele non declare'
  if (/opus/i.test(model) && !/^ORCH/i.test(session)) return 'executant en Opus'
  return ''
}

// Dernier instantane d'orch.py (journal, contexte par session, questions). Le pane ne lit plus
// le journal lui-meme : un chemin relatif suivait le cwd de la session et l'affichage tombait
// sur « Aucun journal lisible » des qu'elle changeait de dossier (2026-10-15). orch.py retrouve
// le journal courant par un pointeur hors depot (~/.claude/orchestration/journal-courant.json).
let snap = null
let erreur = ''

async function rafraichir($) {
  try {
    const r = await $.process.run(['python3', $.plugin.root + '/orch.py', 'snapshot'], { timeoutMs: 10000 })
    if (r.exitCode !== 0) { erreur = (r.stderr || 'orch.py snapshot a echoue').slice(0, 200); return }
    snap = JSON.parse(r.stdout)
    erreur = ''
  } catch (err) {
    erreur = String(err).slice(0, 200)
  }
}

/** Une reponse part par deux voies : un `answer` au journal (la question sort du pied de pane,
 *  pour toutes les sessions qui le lisent) et un prompt dans CETTE session, l'orchestratrice,
 *  qui la transmet a la session qui a pose la question. */
async function repondre($, q, reponse) {
  if (!snap || !reponse.trim()) return
  await $.process.run(
    ['python3', $.plugin.root + '/orch.py', 'log', '--session', 'PROPRIETAIRE', '--task', q.task,
     '--kind', 'answer', '--ref', q.id, '--msg', reponse.trim()],
    { env: { ORCH_DIR: snap.journal.replace(/\/events\.jsonl$/, ''), ORCH_OUT: snap.out } })
  await rafraichir($)
  $.ui.invalidate('ui.render')
  void $.prompt.submit({
    text: 'Reponse du proprietaire a ' + q.session + ' (' + q.task + ') — « ' + q.msg + ' » : '
      + reponse.trim() + '. Transmets-la a la session concernee.',
  })
}

/** Ouvre un onglet Terminal sur une tache planifiee (orch.py lancer : lanceur bash, cd dans le
 *  depot, claude --model <modele> avec le prompt d'amorcage). Le pane ne sait rien du shell. */
async function lancerTache($, ev) {
  if (!snap) return
  const r = await $.process.run(
    ['python3', $.plugin.root + '/orch.py', 'lancer', '--session', ev.session, '--task', ev.task],
    { env: { ORCH_DIR: snap.journal.replace(/\/events\.jsonl$/, ''), ORCH_OUT: snap.out } })
  $.ui.toast(r.exitCode === 0 ? ev.session + ' lancee (' + (ev.model || 'modele par defaut') + ')'
    : 'lancement impossible : ' + (r.stderr || r.stdout).slice(0, 120))
  await rafraichir($)
  $.ui.invalidate('ui.render')
}

/** Reponses donnees dans Google Chat (via n8n) : orch.py chat-pull les ecrit au journal, le pane
 *  les soumet a l'orchestratrice comme un clic local. Rien ne se passe si Chat n'est pas configure. */
const RELEVE_CHAT_MS = 30000
// Seule la session qui a ouvert /orch (l'orchestratrice) releve Chat. Le mod se charge dans TOUTES
// les sessions (CLAUDE_CODE_PLUGIN_DIRS) : sans ce garde, la premiere session venue — une
// executante — prenait la reponse et se la soumettait (2026-10-15, reponse « Oui, validee »
// arrivee chez G1). Variable de module : un rechargement du mod demande de rouvrir /orch.
let orchestratrice = false
async function releverChat($) {
  if (!snap || !orchestratrice) return
  try {
    const r = await $.process.run(['python3', $.plugin.root + '/orch.py', 'chat-pull'],
      { env: { ORCH_DIR: snap.journal.replace(/\/events\.jsonl$/, ''), ORCH_OUT: snap.out }, timeoutMs: 15000 })
    if (r.exitCode !== 0) return
    for (const a of JSON.parse(r.stdout || '[]')) {
      $.ui.toast('Reponse depuis Google Chat : ' + a.session + ' ' + a.task)
      void $.prompt.submit({
        text: 'Reponse du proprietaire (depuis Google Chat) a ' + a.session + ' (' + a.task + ') — « '
          + a.msg + ' » : ' + a.answer + '. Transmets-la a la session concernee.',
      })
    }
  } catch { /* Chat indisponible : le pane continue sans */ }
}

function barre(pct, largeur = 20) {
  const n = Math.max(0, Math.min(largeur, Math.round((pct / 100) * largeur)))
  return '█'.repeat(n) + '░'.repeat(largeur - n)
}

function kilo(n) {
  return n >= 1000 ? Math.round(n / 1000) + 'k' : String(n)
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'orch', description: "Ouvrir le pane d'orchestration OMKomUnity" })
    // Le journal est ecrit par d'AUTRES sessions : aucun evenement local ne previent de son
    // changement, donc on relit sur une horloge plutot que sur un declencheur.
    $.clock.every(RAFRAICHISSEMENT_MS, async () => { await rafraichir($); $.ui.invalidate('ui.render') })
    $.clock.every(RELEVE_CHAT_MS, async () => { await releverChat($); await rafraichir($); $.ui.invalidate('ui.render') })
    return next(e)
  })

  // `focus` n'accepte que `true` (ou rien) : `focus: false` faisait lever $.ui.open, le hook
  // echouait et le moteur repondait « no command.run hook answered it ». Constate le 2026-10-15.
  on('command.run', { command: 'orch' }, async ($) => {
    orchestratrice = true
    await rafraichir($)
    // Ouvrir le pane releve aussi les reponses donnees dans Chat entre-temps — une seconde plus
    // tard : prompt.submit depuis un command.run attendrait le tour que ce hook tient (interblocage).
    $.clock.after(1000, () => { void releverChat($) })
    await $.ui.open({ id: PANE, title: 'Orchestration', closeOnEscape: true })
    return { text: "Pane d'orchestration ouvert (" + (snap ? snap.journal : erreur || JOURNAL) + ')' }
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button, Input } = $.ui.resolve(e)
    const evenements = snap && snap.existe ? snap.evenements : null

    const ligne = (children) => Box({ flexDirection: 'row', children })
    const txt = (s, props = {}) => Text({ ...props, children: [s] })

    // En-tete : le wordmark, tricolore comme sur le site.
    const entete = ligne([
      txt('OM', { bold: true, color: C.paper }),
      txt('Kom', { bold: true, color: C.magenta }),
      txt('Unity', { bold: true, color: C.teal }),
      txt('  orchestration', { dimColor: true }),
    ])

    if (evenements === null) {
      return Box({
        flexDirection: 'column',
        children: [entete, txt(' '), txt('Aucun journal lisible.', { color: C.or }),
                   txt(snap ? snap.journal : erreur || 'orch.py snapshot pas encore lu', { dimColor: true }),
                   txt('Une session doit poser un evenement avec orch.py log.', { dimColor: true })],
      })
    }

    const st = etat(evenements)
    const par = (...kinds) => st.filter((v) => kinds.includes(v.ev.kind))
    const enCours = par('start', 'progress')
    const attente = par('review-request')
    const bloques = par('blocked')
    const rejetes = par('review-reject')
    const finis = par('done', 'review-ok')
    const planifies = par('planned')

    const compteurs = ligne([
      txt(String(enCours.length) + ' en cours', { color: C.ciel, bold: true }),
      txt('  ' + attente.length + ' review', { color: C.or, bold: attente.length > 0 }),
      txt('  ' + bloques.length + ' bloque', { color: C.saumon, bold: bloques.length > 0 }),
      txt('  ' + finis.length + ' fini', { color: C.teal }),
      txt('  ' + planifies.length + ' a lancer', { dimColor: true }),
    ])

    // Une tache sur deux lignes : l'identite d'abord, le detail ensuite. Le detail est tronque
    // a 64 caracteres — un pane large reste etroit, et une ligne qui deborde casse la colonne.
    const tache = (v, couleur) => Box({
      flexDirection: 'column',
      children: [
        ligne([
          txt(PUCE[v.ev.kind] + ' ', { color: couleur, bold: true }),
          txt(v.ev.task, { bold: true, color: couleur }),
          txt(' ' + v.ev.session, { dimColor: true }),
          txt('  ' + duree(v.debut), { dimColor: true }),
        ]),
        txt('   ' + String(v.ev.msg || '').slice(0, 64), { dimColor: true }),
        // Une tache planifiee avec son depot et son prompt se lance d'un clic.
        ...(v.ev.kind === 'planned' && v.ev.prompt && v.ev.cwd ? [ligne([Button({
          key: 'lancer-' + v.ev.session + '-' + v.ev.task,
          label: 'Lancer ' + v.ev.session + (v.ev.model ? ' (' + v.ev.model + ')' : ''),
          variant: 'primary',
          onPress: () => { void lancerTache($, v.ev) },
        })])] : []),
      ],
    })

    const section = (titre, items, couleur) =>
      items.length === 0 ? [] : [
        txt(' '),
        txt(titre.toUpperCase() + '  (' + items.length + ')', { color: couleur, bold: true }),
        ...items.sort((a, b) => a.debut.localeCompare(b.debut)).map((v) => tache(v, couleur)),
      ]

    // Une ligne par session : son modele, et l'alerte si elle n'en a pas declare ou si un
    // executant tourne en Opus. Sans cette ligne, le cout d'une orchestration reste invisible.
    // Sessions terminees (toutes taches done/review-ok, aucune question) repliees en une ligne :
    // sans ca la liste reprend tout l'historique du journal. Le proprietaire n'est pas une session.
    const terminees = new Set((snap && snap.terminees) || [])
    const lancees = new Set((snap && snap.lancees) || [])
    const sessions = [...modeles(evenements).entries()]
      .filter(([s]) => s !== 'PROPRIETAIRE' && !terminees.has(s))
      .sort(([a], [b]) => a.localeCompare(b))
    const blocSessions = sessions.length === 0 && terminees.size === 0 ? [] : [
      txt(' '),
      txt('SESSIONS & CONTEXTE  (' + sessions.length + ' actives)', { dimColor: true, bold: true }),
      ...sessions.map(([s, m]) => {
        const a = alerteModele(s, m)
        const cx = (snap && snap.contextes[s]) || null
        // Couleur du remplissage : la compaction approche vers 80 %, c'est la que la session
        // commence a perdre ce qu'elle sait. Fenetre supposee 200k, 1M des que ca deborde (*).
        const coul = !cx ? C.muted : cx.pct >= 80 ? C.saumon : cx.pct >= 50 ? C.or : C.teal
        return Box({
          flexDirection: 'column',
          children: [
            ligne([
              txt((a ? '! ' : '  ') + s, { color: a ? C.saumon : C.paper }),
              txt('  ' + (m || '?'), { color: a ? C.saumon : C.teal, bold: true }),
              ...(a ? [txt('  ' + a, { color: C.saumon })] : []),
            ]),
            cx
              ? ligne([
                  txt('  ' + barre(cx.pct) + ' ', { color: coul }),
                  txt(cx.pct + '% ', { color: coul, bold: true }),
                  txt(kilo(cx.tokens) + '/' + kilo(cx.fenetre) + (cx.estime ? '*' : ''), { dimColor: true }),
                  txt(cx.compactions ? '  ' + cx.compactions + ' compact' : '', { color: C.or }),
                  txt('  ' + duree(cx.maj), { dimColor: true }),
                ])
              : txt(lancees.has(s) ? '  contexte inconnu (aucun transcript lie)' : '  pas encore lancee', { dimColor: true }),
          ],
        })
      }),
      ...(terminees.size ? [txt('  ' + terminees.size + ' terminee(s) : ' + [...terminees].join(', '), { dimColor: true, wrap: 'wrap' })] : []),
    ]

    // Pied de pane : les questions en attente, une par bloc, ses reponses en boutons et un
    // champ libre pour une autre reponse — la forme d'une question posee dans le transcript.
    const qs = (snap && snap.questions) || []
    const blocQuestions = qs.length === 0 ? [] : [
      txt(' '),
      txt('QUESTIONS  (' + qs.length + ')', { color: C.or, bold: true }),
      ...qs.map((q, i) => Box({
        flexDirection: 'column',
        children: [
          ligne([
            txt('? ', { color: C.or, bold: true }),
            txt(q.session + ' ' + q.task, { color: C.or }),
            txt('  ' + duree(q.ts), { dimColor: true }),
          ]),
          // wrap : sans lui le terminal tronque a la largeur du pane (question illisible, 2026-10-15)
          txt('  ' + q.msg, { wrap: 'wrap' }),
          // Une reponse par ligne : cote a cote, les libelles longs etaient coupes eux aussi.
          Box({ flexDirection: 'column', children: (q.options || []).map((o, j) => Button({
            key: 'q' + i + '-o' + j,
            label: (j + 1) + '. ' + o,
            ...(j === 0 ? { variant: 'primary' } : {}),
            onPress: () => { void repondre($, q, o) },
          })) }),
          Input({
            key: 'q' + i + '-autre',
            placeholder: 'Autre reponse…',
            submitLabel: 'Envoyer',
            onSubmit: (v) => { void repondre($, q, v) },
          }),
        ],
      })),
    ]

    const journal = evenements.slice(-6).reverse().map((ev) =>
      ligne([
        txt(new Date(ev.ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) + ' ', { dimColor: true }),
        txt(ev.session, { color: COULEUR[ev.kind] || C.muted }),
        txt(' ' + ev.task + ' ' + ev.kind, { dimColor: true }),
      ]))

    return Box({
      flexDirection: 'column',
      children: [
        entete,
        compteurs,
        ...blocSessions,
        ...section('bloque - action requise', bloques, C.saumon),
        ...section('review en attente', attente, C.or),
        ...section('en cours', enCours, C.ciel),
        ...section('renvoye', rejetes, C.saumon),
        ...section('a lancer', planifies, C.muted),
        txt(' '),
        txt('JOURNAL', { dimColor: true, bold: true }),
        ...journal,
        txt(' '),
        txt(evenements.length + ' evenements - maj ' + (RAFRAICHISSEMENT_MS / 1000) + 's  · * fenetre estimee (statusline absente)', { dimColor: true }),
        ...blocQuestions,
      ],
    })
  })
}
