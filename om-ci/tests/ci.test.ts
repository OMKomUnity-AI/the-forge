import { test, expect } from 'claude-code/testing'

// Forme reelle de ci.py, relevee en direct le 2026-10-16 sur omkom/omkomunity-site :
// l'etape en echec arrive deja redigee, et un run `success` ne porte aucun job (plafond d'API).
const CI = {
  genere: '2026-10-16T12:30:00+00:00',
  depots: ['omkom/omkomunity-site', 'omkom/claude-base'],
  runs: [
    {
      repo: 'omkom/omkomunity-site', id: 38895797351, workflow: 'CI', titre: 'fix(veille): corrige les dimensions og',
      branche: 'main', evenement: 'push', acteur: 'omkom', statut: 'in_progress', conclusion: null,
      cree: '2026-10-16T11:44:40Z', demarre: '2026-10-16T11:44:40Z', maj: '2026-10-16T11:49:00Z', duree_s: 293,
      url: 'https://github.com/omkom/omkomunity-site/actions/runs/38895797351',
      jobs: [{ nom: 'Frontend — build', statut: 'in_progress', conclusion: null, duree_s: 120, etape_en_cours: 'Tests e2e' }],
    },
    {
      repo: 'omkom/omkomunity-site', id: 38887267243, workflow: 'CI', titre: 'fix(n8n): corrige le lien 404',
      branche: 'main', evenement: 'push', acteur: 'omkom', statut: 'completed', conclusion: 'failure',
      cree: '2026-10-16T10:26:43Z', demarre: '2026-10-16T10:26:43Z', maj: '2026-10-16T10:29:50Z', duree_s: 193,
      url: 'https://github.com/omkom/omkomunity-site/actions/runs/38887267243',
      jobs: [{ nom: 'Frontend — typecheck, tests, build, e2e', statut: 'completed', conclusion: 'failure', duree_s: 149, etape_echec: 'Tests unitaires (étape 7)' }],
    },
    {
      repo: 'omkom/claude-base', id: 38883278618, workflow: 'Repo — lint', titre: 'chore: menage',
      branche: 'main', evenement: 'schedule', acteur: 'omkom', statut: 'completed', conclusion: 'success',
      cree: '2026-10-16T09:51:08Z', demarre: '2026-10-16T09:51:08Z', maj: '2026-10-16T09:51:20Z', duree_s: 12,
      url: 'https://github.com/omkom/claude-base/actions/runs/38883278618', jobs: [],
    },
  ],
  erreurs: [{ depot: 'OMKomUnity-AI/the-forge', message: 'HTTP 404: Not Found (actions desactivees ?)' }],
}

function faux(on: any, appels: string[][]) {
  on('process.run', async (_: any, e: any) => {
    appels.push([...e.argv])
    const estCi = e.argv.some((a: string) => String(a).endsWith('ci.py'))
    // Le faux log porte une sequence d'echappement : c'est ce qu'un fork hostile peut y mettre.
    const log = 'ligne de log 1\n\u001b[2Jligne de log 2\u0007'
    return { value: { exitCode: 0, stdout: estCi ? JSON.stringify(CI) : log, stderr: '' } } as never
  })
  on('ui.open', async () => ({ value: undefined }) as never)
  on('ui.status', async () => ({ value: undefined }) as never)
}

test('/ci ouvre le pane et compte runs et depots', async ($, on) => {
  faux(on, [])
  const r = await $.command.run({ command: 'ci', args: '' })
  expect(r.text).toContain('3 runs')
  expect(r.text).toContain('2 depot')
})

// L'etape qui lache est la seule donnee qui transforme « c'est rouge » en « voila quoi reparer ».
test('le pane nomme le depot, la branche et l etape en echec', async ($, on) => {
  faux(on, [])
  await $.command.run({ command: 'ci', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-ci', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-ci' })
  expect(await ui.find({ type: 'Text', text: /Tests unitaires \(étape 7\)/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /omkom\/omkomunity-site · main · push/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Tests e2e…/ })).toBeDefined()
  // un depot injoignable est NOMME : le cacher ferait croire au vert
  expect(await ui.find({ type: 'Text', text: /the-forge : HTTP 404/ })).toBeDefined()
  await ui.unmount()
})

// Une relance consomme des minutes CI et peut redeployer : jamais au premier clic.
test('relancer demande une confirmation puis appelle gh run rerun --failed', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels)
  await $.command.run({ command: 'ci', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-ci', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-ci' })
  await ui.press({ key: 'v-38887267243' })                   // deplier le run en echec
  expect(await ui.find({ key: 'rf-38887267243' })).toBeUndefined()
  await ui.press({ key: 'r-38887267243' })                   // demander la relance
  expect(await ui.find({ type: 'Text', text: /Relancer CI sur omkom\/omkomunity-site \?/ })).toBeDefined()
  expect(appels.some(a => a.includes('rerun'))).toBe(false)  // rien n'est parti au premier clic
  await ui.press({ key: 'rf-38887267243' })
  expect(appels.find(a => a.includes('rerun'))).toEqual(['gh', 'run', 'rerun', '38887267243', '-R', 'omkom/omkomunity-site', '--failed'])
  await ui.unmount()
})

test('le log d echec s affiche dans le pane, tronque', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels)
  await $.command.run({ command: 'ci', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-ci', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-ci' })
  await ui.press({ key: 'v-38887267243' })
  await ui.press({ key: 'l-38887267243' })
  expect(appels.find(a => a.includes('--log-failed'))).toContain('38887267243')
  const l = await ui.find({ type: 'Text', text: /ligne de log 2/ })
  expect(l).toBeDefined()
  // une sequence d'echappement venue du log ne doit JAMAIS atteindre le terminal
  expect((l as any).text).not.toContain('\u001b')
  expect((l as any).text).not.toContain('\u0007')
  await ui.unmount()
})

// Ce pane doit etre entierement utilisable depuis le telephone : l'app mobile ne dessine ni
// Input ni Select, donc aucune action ne doit dependre d'un clavier.
test('sur mobile tout le pane est atteignable au bouton', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels)
  await $.command.run({ command: 'ci', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-ci', surface: 'mobile', component: 'Pane', props: {} as never, requestId: 'om-ci' })
  expect(await ui.find({ type: 'Text', text: /Tests unitaires/ })).toBeDefined()
  await ui.press({ key: 'v-38887267243' })
  expect(await ui.find({ key: 'r-38887267243' })).toBeDefined()
  expect(await ui.find({ key: 'w-38887267243' })).toBeDefined()
  await ui.press({ key: 'r-38887267243' })
  await ui.press({ key: 'ra-38887267243' })
  expect(appels.find(a => a.includes('rerun'))).toEqual(['gh', 'run', 'rerun', '38887267243', '-R', 'omkom/omkomunity-site'])
  await ui.unmount()
})
