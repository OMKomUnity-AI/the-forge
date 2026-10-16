// om-ci — la CI GitHub de tous les depots ouverts, en direct, a cote du transcript.
//
// Pourquoi un mod et pas `gh run watch` : watch suit UN run dans UN terminal, et s'arrete
// quand il finit. Ici la question n'est jamais « ce run avance-t-il » mais « qu'est-ce qui est
// rouge, sur quel depot, et a quelle etape » — sur tous les depots ou l'on travaille, pendant
// qu'on travaille. Le pane repond sans quitter la session.
//
// Ce qu'il affiche vient de ci.py (0 LLM) : les runs par depot, et pour les runs en cours ou en
// echec les jobs avec l'ETAPE qui a lache. « failure » tout seul n'apprend rien ; « Build ·
// etape 4 "pnpm test" » se corrige. Les depots sont deduits des sessions ouvertes : aucune
// configuration a tenir a jour.
//
// Rien de ce pane n'a besoin d'un clavier : l'app mobile ne dessine ni Input ni Select (type
// `Elements`, claude-code), donc tout est au bouton, utilisable depuis le telephone.


const PANE = 'om-ci'
const COMMANDES = ['ci']
// 30 s : la CI se mesure en minutes, et chaque tour coute un appel gh par depot (quota 5000/h).
const VEILLE_MS = 30000

const C = { paper: '#F7F5F0', muted: '#E6E9F2', magenta: '#EF4E9B', teal: '#52C9B4', ciel: '#4AA8D8', or: '#EFC137', saumon: '#E85269' }

let snap = null        // derniere sortie de ci.py
let message = ''       // ce que la derniere action a donne, en pied de pane
let ouvert = null       // run deplie (logs affiches)
let aConfirmer = null   // run dont on a demande la relance, en attente du second clic
let logs = {}           // id de run -> dernieres lignes de son log d'echec

function duree(s) {
  if (!s && s !== 0) return ''
  if (s < 60) return s + 's'
  const m = Math.floor(s / 60)
  return m < 60 ? m + 'm' + String(s % 60).padStart(2, '0') : Math.floor(m / 60) + 'h' + String(m % 60).padStart(2, '0')
}

function estEchec(r) {
  return ['failure', 'timed_out', 'startup_failure'].includes(r.conclusion)
}

async function charger($) {
  const r = await $.process.run(['python3', $.plugin.root + '/ci.py'], { timeoutMs: 60000 })
  if (r.exitCode !== 0) { message = 'ci.py a echoue : ' + (r.stderr || '').slice(0, 160); return }
  try { snap = JSON.parse(r.stdout) } catch { message = 'sortie de ci.py illisible' }
}

/** Relance : elle consomme des minutes CI et peut redeployer. Deux temps, comme le lancement
 *  d'une session dans /orch — le second clic seul agit. */
async function relancer($, r, seulementEchoues) {
  const args = ['gh', 'run', 'rerun', String(r.id), '-R', r.repo, ...(seulementEchoues ? ['--failed'] : [])]
  const x = await $.process.run(args, { timeoutMs: 30000 })
  message = x.exitCode === 0 ? r.workflow + ' relance' + (seulementEchoues ? ' (jobs en echec)' : '') + ' sur ' + r.repo
    : 'relance refusee : ' + (x.stderr || x.stdout).slice(0, 140)
  await charger($)
  $.ui.invalidate('ui.render')
}

/** Les dernieres lignes du log des jobs en echec, dans le pane : c'est la ligne d'erreur qu'on
 *  cherche, pas la page GitHub. Tronquees court — un log de CI deborde n'importe quelle colonne. */
async function voirLogs($, r) {
  const x = await $.process.run(['gh', 'run', 'view', String(r.id), '-R', r.repo, '--log-failed'], { timeoutMs: 60000 })
  const brut = (x.stdout || x.stderr || '').split('\n').filter((l) => l.trim())
  logs[r.id] = brut.slice(-14).map((l) => l.slice(-200))
  if (!logs[r.id].length) logs[r.id] = ['aucune ligne de log rendue par gh (job encore en cours ?)']
  message = 'log de ' + r.workflow + ' : ' + logs[r.id].length + ' ligne(s)'
  $.ui.invalidate('ui.render')
}

/** Ouvre le run dans le navigateur : il reste des cas ou seule la page GitHub repond (matrice,
 *  artefacts, re-run d'un job precis). */
async function ouvrirWeb($, r) {
  const x = await $.process.run(['gh', 'run', 'view', String(r.id), '-R', r.repo, '--web'], { timeoutMs: 20000 })
  message = x.exitCode === 0 ? 'ouvert dans le navigateur' : 'ouverture impossible : ' + (x.stderr || '').slice(0, 120)
  $.ui.invalidate('ui.render')
}

async function ouvrirPane($) {
  await $.ui.open({ id: PANE, title: 'CI GitHub', closeOnEscape: true })
  if (!snap) await charger($)
  $.ui.invalidate('ui.render')
  const n = snap ? snap.runs.length : 0
  return { text: n + ' runs sur ' + (snap ? snap.depots.length : 0) + ' depot(s) — ' + (message || 'CI lue par gh') }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    for (const name of COMMANDES) await $.command.register({ name, description: 'CI GitHub en direct : runs, jobs, etape en echec' })
    // Veille permanente : un echec qui tombe pendant qu'on code doit se voir sans ouvrir le pane.
    // Indicateur de statut plutot qu'un toast (une fenetre flottante masque ce qu'on lit).
    $.clock.every(VEILLE_MS, async () => {
      await charger($)
      if (snap) {
        const rouges = snap.runs.filter(estEchec).length
        const cours = snap.runs.filter((r) => r.statut !== 'completed').length
        $.ui.status(rouges || cours ? 'CI ' + (rouges ? rouges + ' rouge' : '') + (rouges && cours ? ' · ' : '') + (cours ? cours + ' en cours' : '') + ' · /ci' : undefined)
      }
      $.ui.invalidate('ui.render')
    })
    return next(e)
  })

  // Matcher litteral et non la variable de boucle : le moteur lit les declencheurs sans
  // executer le module, et `{ command: name }` lui rendait un matcher inconnu (command=?).
  on('command.run', { command: 'ci' }, ouvrirPane)

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const ligne = (children) => Box({ flexDirection: 'row', children })
    const txt = (s, props = {}) => Text({ ...props, children: [s] })

    const entete = ligne([
      txt('OM', { bold: true, color: C.paper }), txt('Kom', { bold: true, color: C.magenta }),
      txt('Unity', { bold: true, color: C.teal }), txt('  ci', { dimColor: true }),
    ])

    if (!snap) {
      return Box({ flexDirection: 'column', children: [entete, txt(' '),
        txt('CI pas encore lue.', { color: C.or }), txt(message || 'ci.py tourne au prochain tour (30 s)', { dimColor: true })] })
    }

    const runs = snap.runs
    const cours = runs.filter((r) => r.statut !== 'completed')
    const rouges = runs.filter(estEchec)
    const verts = runs.filter((r) => r.conclusion === 'success')

    const compteurs = ligne([
      txt(cours.length + ' en cours', { color: C.ciel, bold: cours.length > 0 }),
      txt('  ' + rouges.length + ' rouge', { color: C.saumon, bold: rouges.length > 0 }),
      txt('  ' + verts.length + ' vert', { color: C.teal }),
      txt('  ' + snap.depots.length + ' depot(s)', { dimColor: true }),
    ])

    // Un run sur trois lignes : identite, ou ca en est, et l'etape qui lache. L'etape est la
    // seule chose qui distingue « c'est rouge » de « voila quoi reparer ».
    const carte = (r, couleur) => {
      const jobs = r.jobs || []
      // ci.py rend l'etape deja redigee (« Tests unitaires (etape 7) ») : on l'affiche telle
      // quelle. Les jobs sans etape notable (succes, skipped) ne prennent pas de ligne.
      const etapes = jobs.map((j) => j.etape_echec ? j.nom + ' · ' + j.etape_echec
        : j.etape_en_cours ? j.nom + ' · ' + j.etape_en_cours + '…' : '').filter(Boolean)
      const deplie = ouvert === r.id
      return Box({
        flexDirection: 'column',
        children: [
          ligne([
            txt((estEchec(r) ? 'x ' : r.statut !== 'completed' ? '> ' : 'v '), { color: couleur, bold: true }),
            Button({ key: 'v-' + r.id, label: r.workflow.slice(0, 44), onPress: () => { ouvert = deplie ? null : r.id; $.ui.invalidate('ui.render') } }),
            txt('  ' + duree(r.duree_s), { dimColor: true }),
          ]),
          txt('   ' + r.repo + ' · ' + r.branche + ' · ' + r.evenement + (r.acteur ? ' · ' + r.acteur : ''), { dimColor: true }),
          txt('   ' + String(r.titre || '').slice(0, 70), { dimColor: true }),
          ...etapes.slice(0, 4).map((s) => txt('   ' + s, { color: couleur })),
          ...(deplie ? [
            ...(logs[r.id] || []).map((l) => txt('   ' + l, { dimColor: true })),
            ligne([
              ...(estEchec(r) ? [Button({ key: 'l-' + r.id, label: 'Voir le log', onPress: () => { void voirLogs($, r) } })] : []),
              Button({ key: 'w-' + r.id, label: 'Ouvrir sur GitHub', onPress: () => { void ouvrirWeb($, r) } }),
            ]),
            // Relance en deux temps : elle consomme des minutes CI et peut redeployer.
            ...(aConfirmer === r.id ? [
              txt('   Relancer ' + r.workflow + ' sur ' + r.repo + ' ?', { color: C.or }),
              ligne([
                Button({ key: 'rf-' + r.id, label: 'Jobs en echec', variant: 'primary', onPress: () => { aConfirmer = null; void relancer($, r, true) } }),
                Button({ key: 'ra-' + r.id, label: 'Tout le run', onPress: () => { aConfirmer = null; void relancer($, r, false) } }),
                Button({ key: 'rx-' + r.id, label: 'Annuler', onPress: () => { aConfirmer = null; $.ui.invalidate('ui.render') } }),
              ]),
            ] : [ligne([Button({ key: 'r-' + r.id, label: 'Relancer…', onPress: () => { aConfirmer = r.id; $.ui.invalidate('ui.render') } })])]),
          ] : []),
        ],
      })
    }

    const section = (titre, items, couleur, max) =>
      items.length === 0 ? [] : [
        txt(' '),
        txt(titre.toUpperCase() + '  (' + items.length + ')', { color: couleur, bold: true }),
        ...items.slice(0, max).map((r) => carte(r, couleur)),
      ]

    return Box({
      flexDirection: 'column',
      children: [
        entete,
        compteurs,
        ...section('en cours', cours, C.ciel, 6),
        ...section('rouge - a reparer', rouges, C.saumon, 6),
        ...section('vert', verts, C.teal, 4),
        ...(snap.erreurs && snap.erreurs.length ? [
          txt(' '),
          txt('DEPOTS INJOIGNABLES  (' + snap.erreurs.length + ')', { color: C.or, bold: true }),
          // Nomme le depot ET la raison : un pane qui cache un depot muet ferait croire au vert.
          ...snap.erreurs.map((x) => txt('  ' + (x.depot || x.repo || '?') + ' : ' + String(x.message || x).slice(0, 80), { dimColor: true })),
        ] : []),
        txt(' '),
        ligne([Button({ key: 'rafraichir', label: 'Rafraichir', onPress: () => { void charger($).then(() => $.ui.invalidate('ui.render')) } })]),
        txt(runs.length + ' runs · lu ' + String(snap.genere || '').slice(11, 16) + ' UTC · maj ' + (VEILLE_MS / 1000) + 's', { dimColor: true }),
        ...(message ? [txt(message, { color: C.teal })] : []),
      ],
    })
  })
}
