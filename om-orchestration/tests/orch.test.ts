import { test, expect, mock } from 'claude-code/testing'

const SNAP = {
  journal: '/tmp/j/events.jsonl', out: '/tmp/ORCHESTRATION.md', existe: true,
  evenements: [
    { ts: '2026-10-15T08:00:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'start', msg: 'go', ref: '', model: 'sonnet' },
    { ts: '2026-10-15T08:05:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'question', msg: 'Retirer le chiffre ?', ref: '', options: ['oui', 'non'] },
  ],
  contextes: { 'Q1-adoption': { tokens: 120000, fenetre: 200000, pct: 60, modele_reel: 'claude-sonnet-5-5', compactions: 1, maj: '2026-10-15T08:05:00+00:00' } },
  questions: [{ ts: '2026-10-15T08:05:00+00:00', session: 'Q1-adoption', task: '#Q1', kind: 'question', msg: 'Retirer le chiffre ?', ref: '', options: ['oui', 'non'], id: 'Q1-adoption|2026-10-15T08:05:00+00:00' }],
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
  expect(prompts.some(p => p.includes('depuis Google Chat') && p.includes('Corriger'))).toBe(true)
})
