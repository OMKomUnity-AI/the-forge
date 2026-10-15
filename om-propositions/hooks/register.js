// om-propositions — les propositions de sessions suivantes laissees en fin de /close,
// categorisees et priorisees par Haiku, dans un pane.
//
// Pourquoi un mod a part d'om-orchestration : l'orchestration suit ce qui TOURNE, ce pane
// montre ce qui RESTE A LANCER. Deux questions, deux rythmes : l'un se relit toutes les deux
// secondes, l'autre a l'ouverture.
//
// Extraction deterministe (propositions.py, 0 LLM), classement par UN appel Haiku pour toute
// la liste, mis en cache dans $.store sous l'empreinte des ids : rouvrir le pane ne refacture
// rien tant qu'aucune proposition n'est apparue ou n'a disparu.


const PANE = 'om-propositions'
const COMMANDES = ['next-sessions', 'propositions']
const JOURS = '3'
const MODELE = 'haiku'
const CATEGORIES = ['Production & risques', 'Outillage & gates', 'Contenu & SEO', 'Mesure & donnees',
  'Process & orchestration', 'Commercial & clients']

const C = { paper: '#F7F5F0', muted: '#E6E9F2', magenta: '#EF4E9B', teal: '#52C9B4', ciel: '#4AA8D8', or: '#EFC137', saumon: '#E85269' }
const COULEUR_P = { 1: C.saumon, 2: C.or, 3: C.teal }

let items = []          // propositions extraites
let classement = {}     // id -> { categorie, priorite, raison, doublon_de }
let statut = ''         // ce que le pane dit en pied : en cours, erreur, cache
let ouvert = null       // id de la proposition depliee
const VEILLE_MS = 30000  // relecture des transcripts : 0,8 s de CPU, donc 30 s suffit a paraitre « en direct »

/** Premier objet JSON de la reponse : Haiku entoure parfois sa sortie d'un bloc ```json. */
function json(texte) {
  const d = texte.indexOf('{')
  const f = texte.lastIndexOf('}')
  return d >= 0 && f > d ? JSON.parse(texte.slice(d, f + 1)) : null
}

function prompt(liste, reference = []) {
  const lignes = liste.map((i) => JSON.stringify({
    id: i.id, titre: i.titre, projet: i.projet, session: i.session, date: i.ts.slice(0, 10),
    prompt: i.prompt.slice(0, 300),
  }))
  return 'Voici ' + liste.length + ' propositions de sessions de travail, laissees par des sessions en fin de /close.\n\n'
    + lignes.join('\n') + '\n\n'
    + 'Pour CHAQUE id, donne :\n'
    + '- categorie : exactement une de ' + JSON.stringify(CATEGORIES) + '\n'
    + '- priorite : 1 si elle debloque autre chose ou couvre un risque en production ou chez un client, '
    + '2 si elle apporte un gain net mesurable, 3 sinon (confort, suivi, nettoyage)\n'
    + '- raison : 12 mots maximum, en francais, ce qui justifie la priorite\n'
    + "- doublon_de : l'id d'une proposition (de cette liste ou de la reference) qui dit la meme chose (meme travail, meme projet), sinon null\n\n"
    + (reference.length ? 'Reference, deja classees (ne pas les reclasser) :\n' + reference.join('\n') + '\n\n' : '')
    + 'Reponds UNIQUEMENT par un objet JSON {"<id>": {"categorie": ..., "priorite": ..., "raison": ..., "doublon_de": ...}, ...}, sans texte autour.'
}

// Classement INCREMENTAL : seules les propositions jamais classees partent chez Haiku, avec la
// liste des deja classees en reference pour reperer un doublon. Un /close de plus dans n'importe
// quelle session coute un petit appel, pas le reclassement de tout l'historique.
const CLE = 'classement:' + MODELE
let occupe = false

async function charger($, forcer = false) {
  if (occupe) return 0
  occupe = true
  try {
    const r = await $.process.run(['python3', $.plugin.root + '/propositions.py', '--jours', JOURS], { timeoutMs: 120000 })
    if (r.exitCode !== 0) { statut = 'propositions.py a echoue : ' + (r.stderr || '').slice(0, 160); return 0 }
    items = JSON.parse(r.stdout).items
    const connu = forcer ? {} : ((await $.store.get(CLE)) || {})
    const manquants = items.filter((i) => !connu[i.id])
    classement = connu
    if (manquants.length === 0) { statut = items.length + ' propositions, toutes classees (' + MODELE + ')'; return 0 }
    statut = 'classement par ' + MODELE + ' de ' + manquants.length + ' proposition(s)…'
    $.ui.invalidate('ui.render')
    const reference = items.filter((i) => connu[i.id]).map((i) => i.id + ' : ' + i.titre + ' (' + i.projet + ')')
    const rep = await $.model.complete({
      model: MODELE, prompt: prompt(manquants, reference), maxTokens: 16000,
      system: 'Tu tries des propositions de travail pour un consultant SEO/GEO et ses outils. Tu reponds en JSON strict.',
    })
    if (!rep.isAnswered) { statut = 'classement indisponible (' + rep.reason + ') : ' + manquants.length + ' non classee(s)'; return manquants.length }
    let neuf = {}
    try { neuf = json(rep.text) || {} } catch { statut = 'reponse ' + MODELE + ' illisible'; return manquants.length }
    classement = { ...connu, ...neuf }
    await $.store.set(CLE, classement)
    statut = manquants.length + ' classee(s) par ' + MODELE + (rep.usage ? ', ' + rep.usage.output_tokens + ' tokens' : '')
    return manquants.length
  } finally {
    occupe = false
  }
}

function commande(i) {
  return i.mode ? 'make ' + i.mode + ' P="' + i.prompt.replace(/"/g, '\\"') + '"' : i.prompt
}

async function ouvrir($) {
  await $.ui.open({ id: PANE, title: 'Propositions', closeOnEscape: true })
  if (items.length === 0) await charger($)
  $.ui.invalidate('ui.render')
  return { text: items.length + ' propositions sur ' + JOURS + ' jours — ' + statut }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    for (const name of COMMANDES) await $.command.register({ name, description: 'Propositions de sessions suivantes des /close, categorisees et priorisees' })
    // Veille permanente, pane ouvert ou non : tout /close d'une session locale (n'importe quel
    // projet) ecrit son bloc dans son transcript, que ce tour relit. Le premier passage pose la
    // base sans toast ; les suivants annoncent ce qui est apparu.
    let base = null
    $.clock.every(VEILLE_MS, async () => {
      await charger($)
      const ids = new Set(items.map((i) => i.id))
      if (base) {
        const nouveaux = items.filter((i) => !base.has(i.id))
        if (nouveaux.length) $.ui.toast(nouveaux.length + ' nouvelle(s) proposition(s) de /close — ' + nouveaux[0].session + ' : ' + nouveaux[0].titre.slice(0, 50))
      }
      base = ids
      $.ui.invalidate('ui.render')
    })
    return next(e)
  })

  on('command.run', { command: 'next-sessions' }, ouvrir)
  on('command.run', { command: 'propositions' }, ouvrir)

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const ligne = (children) => Box({ flexDirection: 'row', children })
    const txt = (s, props = {}) => Text({ ...props, children: [s] })

    // Doublons masques sous leur original : une proposition faite par deux sessions est un
    // signal (on l'affiche « +1 »), pas deux lignes a lire.
    const doublons = {}
    for (const i of items) {
      const d = classement[i.id] && classement[i.id].doublon_de
      if (d && d !== i.id && items.some((x) => x.id === d)) (doublons[d] = doublons[d] || []).push(i)
    }
    const visibles = items.filter((i) => !(classement[i.id] && classement[i.id].doublon_de && doublons[classement[i.id].doublon_de]))
    const cat = (i) => (classement[i.id] && classement[i.id].categorie) || 'Non classees'
    const prio = (i) => Number((classement[i.id] && classement[i.id].priorite) || 9)
    const groupes = {}
    for (const i of visibles) (groupes[cat(i)] = groupes[cat(i)] || []).push(i)
    const ordre = Object.keys(groupes).sort((a, b) =>
      Math.min(...groupes[a].map(prio)) - Math.min(...groupes[b].map(prio)) || groupes[b].length - groupes[a].length)
    const nP1 = visibles.filter((i) => prio(i) === 1).length

    const entete = ligne([
      txt('OM', { bold: true, color: C.paper }), txt('Kom', { bold: true, color: C.magenta }),
      txt('Unity', { bold: true, color: C.teal }), txt('  propositions', { dimColor: true }),
    ])
    const compteurs = txt(visibles.length + ' a lancer · ' + nP1 + ' P1 · ' + (items.length - visibles.length)
      + ' doublons replies · ' + JOURS + ' jours', { dimColor: true })

    const carte = (i) => {
      const k = classement[i.id] || {}
      const p = prio(i)
      const plus = (doublons[i.id] || []).map((d) => d.session)
      return Box({
        flexDirection: 'column',
        children: [
          ligne([
            txt((p < 9 ? 'P' + p : '··') + ' ', { color: COULEUR_P[p] || C.muted, bold: true }),
            Button({ key: 'v-' + i.id, label: i.titre.slice(0, 70), onPress: () => { ouvert = ouvert === i.id ? null : i.id; $.ui.invalidate('ui.render') } }),
          ]),
          txt('   ' + i.projet + ' · ' + i.session + ' · ' + (i.mode ? 'make ' + i.mode : 'sans prompt') + ' · ' + i.ts.slice(5, 10)
            + (plus.length ? '  +' + plus.length + ' (' + plus.join(', ') + ')' : ''), { dimColor: true }),
          ...(k.raison ? [txt('   ' + k.raison, { color: COULEUR_P[p] || C.muted })] : []),
          ...(ouvert === i.id ? [
            txt('   ' + i.prompt.slice(0, 600)),
            ligne([Button({ key: 'c-' + i.id, label: 'Copier la commande', variant: 'primary',
              onPress: (press) => { void $.ui.copy({ text: commande(i), surface: press.surface }) } })]),
          ] : []),
        ],
      })
    }

    return Box({
      flexDirection: 'column',
      children: [
        entete,
        compteurs,
        ...ordre.flatMap((c) => [
          txt(' '),
          txt(c.toUpperCase() + '  (' + groupes[c].length + ')', { bold: true, color: C.ciel }),
          ...groupes[c].sort((a, b) => prio(a) - prio(b) || b.ts.localeCompare(a.ts)).map(carte),
        ]),
        txt(' '),
        ligne([
          Button({ key: 'reclasser', label: 'Re-classer (' + MODELE + ')', onPress: () => { void charger($, true).then(() => $.ui.invalidate('ui.render')) } }),
          Button({ key: 'rafraichir', label: 'Rafraichir', onPress: () => { void charger($).then(() => $.ui.invalidate('ui.render')) } }),
        ]),
        txt(statut, { dimColor: true }),
      ],
    })
  })
}
