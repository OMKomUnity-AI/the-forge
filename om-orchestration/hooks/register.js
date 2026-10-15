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

/** Lit le journal. Une ligne tronquee par une ecriture concurrente est SAUTEE, jamais fatale :
 *  le pane doit survivre a un demi-ecrit d'une autre session. */
async function lire($) {
  try {
    if (!(await $.fs.exists(JOURNAL))) return null
    const brut = await $.fs.read(JOURNAL)
    const out = []
    for (const ligne of brut.split('\n')) {
      const t = ligne.trim()
      if (!t) continue
      try { out.push(JSON.parse(t)) } catch { /* ligne partielle, on passe */ }
    }
    return out
  } catch {
    return null
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'orch', description: "Ouvrir le pane d'orchestration OMKomUnity" })
    // Le journal est ecrit par d'AUTRES sessions : aucun evenement local ne previent de son
    // changement, donc on relit sur une horloge plutot que sur un declencheur.
    $.clock.every(RAFRAICHISSEMENT_MS, () => $.ui.invalidate('ui.render'))
    return next(e)
  })

  on('command.run', { command: 'orch' }, async ($) => {
    await $.ui.open({ id: PANE, title: 'Orchestration', focus: false, closeOnEscape: true })
    return {}
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const evenements = await lire($)

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
                   txt(JOURNAL, { dimColor: true }),
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
      ],
    })

    const section = (titre, items, couleur) =>
      items.length === 0 ? [] : [
        txt(' '),
        txt(titre.toUpperCase() + '  (' + items.length + ')', { color: couleur, bold: true }),
        ...items.sort((a, b) => a.debut.localeCompare(b.debut)).map((v) => tache(v, couleur)),
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
        ...section('bloque - action requise', bloques, C.saumon),
        ...section('review en attente', attente, C.or),
        ...section('en cours', enCours, C.ciel),
        ...section('renvoye', rejetes, C.saumon),
        ...section('a lancer', planifies, C.muted),
        txt(' '),
        txt('JOURNAL', { dimColor: true, bold: true }),
        ...journal,
        txt(' '),
        txt(evenements.length + ' evenements - maj ' + (RAFRAICHISSEMENT_MS / 1000) + 's', { dimColor: true }),
      ],
    })
  })
}
