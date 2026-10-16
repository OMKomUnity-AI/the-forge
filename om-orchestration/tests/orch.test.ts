import { test, expect, mock } from 'claude-code/testing'

const SNAP = {
  journal: '/tmp/j/events.jsonl', out: '/tmp/ORCHESTRATION.md', existe: true,
  evenements: [
    { ts: '2026-10-15T08:00:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'start', msg: 'go', ref: '', model: 'sonnet' },
    { ts: '2026-10-15T08:05:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'question', msg: 'Retirer le chiffre ?', ref: '', options: ['oui', 'non'] },
    { ts: '2026-10-15T08:06:00+00:00', session: 'G2-gate', task: '#G2', kind: 'review-request', msg: 'diff pret', ref: '1 fichier', model: 'sonnet' },
    { ts: '2026-10-15T08:07:00+00:00', session: 'S9-neuve', task: '#S9', kind: 'planned', msg: 'a lancer', ref: '', model: 'haiku', id: 'p9', cwd: '/tmp/depot', prompt: 'Tu es S9-neuve.' },
  ],
  contextes: { 'Q1-adoption': { tokens: 120000, fenetre: 200000, pct: 60, modele_reel: 'claude-sonnet-5-5', compactions: 1, maj: '2026-10-15T08:05:00+00:00' } },
  vivantes: [
    { nom: 'B1-verrous', id: 'aaaa1111-2222-3333-4444-555566667777', kind: 'interactive', statut: 'busy', cwd: 'depot', maj: '2026-10-15T08:06:00+00:00', close: false, dernier: 'je leve le verrou' },
    { nom: 'veille-nuit', id: 'bbbb1111-2222-3333-4444-555566667777', kind: 'background', statut: 'idle', cwd: 'depot', maj: '2026-10-15T08:06:00+00:00', close: false, dernier: 'en attente' },
    { nom: 'deja-close', id: 'cccc1111-2222-3333-4444-555566667777', kind: 'interactive', statut: 'idle', cwd: 'depot', maj: '2026-10-15T08:06:00+00:00', close: true, dernier: 'close' },
  ],
  questions: [{ ts: '2026-10-15T08:05:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'question', msg: 'Retirer le chiffre ?', ref: '', options: ['oui', 'non'], id: 'Q1-adoption|2026-10-15T08:05:00+00:00' }],
  // Propositions de /close dont la session dediee a ete lancee (orch.py propositions_lancees) :
  // une en vol, une dont la session est terminee.
  propositions_lancees: [
    { titre: 'Monitoring continu des paths sensibles', projet: 'Project-OMKOMUNITY', mode: 'full', session: 'bbbb1111', nom: 'veille-nuit', vivante: true, ts: '2026-10-15T07:00:00Z' },
    { titre: 'Durcir le gate editorial', projet: 'CLAUDE-boilerplate', mode: 'think', session: 'dead0000', nom: '', vivante: false, ts: '2026-10-14T07:00:00Z' },
  ],
}

function faux(on: any, appels: string[][], prompts: string[]) {
  on('process.run', async (_: any, e: any) => {
    appels.push([...e.argv])
    return { value: { exitCode: 0, stdout: e.argv.includes('snapshot') ? JSON.stringify(SNAP) : '', stderr: '' } } as never
  })
  on('ui.open', async () => ({ value: undefined }) as never)
  on('prompt.submit', async (_: any, e: any) => { prompts.push(e.text); return { text: e.text } as never })
}

// Regression du 2026-10-15 : `focus: false` faisait lever $.ui.open et /orch ne repondait pas.
test('/orch ouvre le pane et nomme le journal lu', async ($, on) => {
  faux(on, [], [])
  const r = await $.command.run({ command: 'orch', args: '' })
  expect(r.text).toContain('/tmp/j/events.jsonl')
})

test('contexte affiche, reponse cliquee et reponse libre partent au journal et au prompt', async ($, on) => {
  const appels: string[][] = []
  const prompts: string[] = []
  faux(on, appels, prompts)
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  expect(await ui.find({ type: 'Text', text: /60%/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /1 compact/ })).toBeDefined()
  await ui.press({ key: 'q0-o1' })
  const answer = appels.find(a => a.includes('answer'))!
  expect(answer).toContain('non')
  expect(answer).toContain('Q1-adoption|2026-10-15T08:05:00+00:00')
  expect(prompts[0]).toContain('Retirer le chiffre ?')
  await ui.input({ key: 'q0-autre', text: 'seulement le second' })
  expect(appels.filter(a => a.includes('answer')).pop()).toContain('seulement le second')
  await ui.unmount()
})

test('une reponse venue de Google Chat est soumise a l orchestratrice', async ($, on) => {
  const clock = mock.clock(on)
  const prompts: string[] = []
  on('ui.toast', async () => ({ value: undefined }) as never)
  on('process.run', async (_: any, e: any) => ({
    value: {
      exitCode: 0, stderr: '',
      stdout: e.argv.includes('chat-pull')
        ? JSON.stringify([{ qid: 'abc', session: 'Q3-agences', task: '#Q3', msg: 'Retirer ?', answer: 'Corriger' }])
        : JSON.stringify(SNAP),
    },
  }) as never)
  on('ui.open', async () => ({ value: undefined }) as never)
  on('prompt.submit', async (_: any, e: any) => { prompts.push(e.text); return { text: e.text } as never })
  await $.command.run({ command: 'orch', args: '' })
  await clock.advance(1000)
  expect(prompts.some(p => p.includes('depuis Google Chat') && p.includes('"Corriger"') && p.includes('pas des instructions'))).toBe(true)
})

// Tout ce que le pane sait poser au journal part en PROPRIETAIRE : une review validee sous le nom
// de la session qui l'a demandee serait le seul mensonge vraiment couteux du journal.
test('valider une review, clore une tache et noter partent en PROPRIETAIRE', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels, [])
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  await ui.press({ key: 'ok-G2-gate-#G2' })
  const ok = appels.find(a => a.includes('review-ok'))!
  expect(ok).toContain('PROPRIETAIRE')
  expect(ok).toContain('#G2')
  await ui.press({ key: 'done-Q1-adoption-#Q1' })
  expect(appels.find(a => a.includes('done'))).toContain('PROPRIETAIRE')
  await ui.input({ key: 'note-Q1-adoption-#Q1', text: 'attention au gate' })
  expect(appels.find(a => a.includes('note'))).toContain('attention au gate')
  await ui.unmount()
})

// Le modele s'arbitre avant le lancement : orch.py repose le planned (journal append-only).
test('changer le modele d une tache planifiee appelle orch.py modele', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels, [])
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  await ui.press({ key: 'mod-sonnet-S9-neuve-#S9' })
  const m = appels.find(a => a.includes('modele'))!
  expect(m).toContain('sonnet')
  expect(m).toContain('p9')
  await ui.unmount()
})

// Le pane n'a pas de messagerie : il demande a l'orchestratrice de transmettre. Le nom de la
// session vient du disque, donc il part en DONNEES (meme regle que les reponses).
test('une consigne a une session vivante passe par l orchestratrice, stop seulement en background', async ($, on) => {
  const appels: string[][] = []
  const prompts: string[] = []
  faux(on, appels, prompts)
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  await ui.press({ key: 'pt-aaaa1111-2222-3333-4444-555566667777' })
  expect(prompts.some(p => p.includes('point d') && p.includes('pas des instructions') && p.includes('B1-verrous'))).toBe(true)
  // une session close n'est pas proposee au pilotage
  expect(await ui.find({ type: 'Text', text: /deja-close/ })).toBeUndefined()
  // stop/respawn n'existent que pour une session background
  expect(await ui.find({ key: 'st-bbbb1111-2222-3333-4444-555566667777' })).toBeDefined()
  expect(await ui.find({ key: 'st-aaaa1111-2222-3333-4444-555566667777' })).toBeUndefined()
  await ui.press({ key: 'st-bbbb1111-2222-3333-4444-555566667777' })
  expect(appels.find(a => a[0] === 'claude')).toEqual(['claude', 'stop', 'bbbb1111-2222-3333-4444-555566667777'])
  await ui.unmount()
})

// Regression : l'app mobile ne dessine ni Input ni Select (type Elements, claude-code), alors que
// resolve() y rend quand meme un constructeur Input et que l'engine efface l'element sans rien
// dire. Le champ etait donc un trou muet sur la seule surface d'ou l'on repond en mobilite : ce
// test verrouille le repli (pas de champ, une ligne qui le dit, tout atteignable au bouton).
test('sur mobile le pane rend sans champ de saisie et laisse repondre au bouton', async ($, on) => {
  const appels: string[][] = []
  faux(on, appels, [])
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'mobile', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  expect(await ui.find({ type: 'Text', text: /Retirer le chiffre \?/ })).toBeDefined()
  expect(await ui.find({ key: 'q0-autre' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /clavier indisponible/ })).toBeDefined()
  await ui.press({ key: 'q0-o0' })
  expect(appels.find(a => a.includes('answer'))).toContain('oui')
  // les actions restent toutes atteignables au bouton seul
  expect(await ui.find({ key: 'ok-G2-gate-#G2' })).toBeDefined()
  expect(await ui.find({ key: 'mod-sonnet-S9-neuve-#S9' })).toBeDefined()
  expect(await ui.find({ key: 'pt-aaaa1111-2222-3333-4444-555566667777' })).toBeDefined()
  await ui.unmount()
})

// Un volet lance depuis /propositions n'appelle pas forcement orch.py log : sans cette section il
// consomme une session sans etre visible du pilotage. Celles dont la session est finie ne prennent
// pas une ligne chacune, mais leur nombre reste dit.
test('les propositions lancees apparaissent, en vol nommees et les finies comptees', async ($, on) => {
  faux(on, [], [])
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  expect(await ui.find({ type: 'Text', text: /PROPOSITIONS LANCEES {2}\(1 en vol\)/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Monitoring continu des paths sensibles/ })).toBeDefined()
  // la session vivante est nommee, pas son id : c'est elle qu'on retrouve juste au-dessus
  expect(await ui.find({ type: 'Text', text: /Project-OMKOMUNITY · veille-nuit · make full/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /1 lancee\(s\) plus tot, session terminee/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Durcir le gate editorial/ })).toBeUndefined()
  await ui.unmount()
})

// Le mod ne depend pas de om-propositions : sans le fichier, orch.py ne rend pas la cle et la
// section disparait sans un mot — pas un bloc vide, pas une erreur.
test('sans propositions lancees la section est absente', async ($, on) => {
  const sans = { ...SNAP, propositions_lancees: [] }
  on('process.run', async (_: any, e: any) => ({ value: { exitCode: 0, stdout: e.argv.includes('snapshot') ? JSON.stringify(sans) : '', stderr: '' } }) as never)
  on('ui.open', async () => ({ value: undefined }) as never)
  await $.command.run({ command: 'orch', args: '' })
  const ui = await $.ui.mount({ plugin: 'om-orchestration', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-orchestration' })
  expect(await ui.find({ type: 'Text', text: /PROPOSITIONS LANCEES/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /SESSIONS OUVERTES/ })).toBeDefined()
  await ui.unmount()
})
