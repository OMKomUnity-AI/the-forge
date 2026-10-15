import { test, expect, mock } from 'claude-code/testing'

const item = (id: string, titre: string, session = 'S1-maillage') => ({
  id, titre, session, projet: 'Project-OMKOMUNITY', ts: '2026-10-15T07:00:00Z', mode: 'fast',
  prompt: 'Projet. Objectif : ' + titre, transcript: '/tmp/x.jsonl',
})

test('classement incremental, rendu par categorie, commande copiable', async ($, on) => {
  mock.store(on)
  let lot = [item('a1', 'Seuil editorial'), item('b2', 'Brancher GA4')]
  const prompts: string[] = []
  const copies: string[] = []
  on('process.run', async () => ({ value: { exitCode: 0, stdout: JSON.stringify({ items: lot }), stderr: '' } }) as never)
  on('ui.open', async () => ({ value: undefined }) as never)
  on('ui.copy', async (_: any, e: any) => { copies.push(e.text); return { value: { isCopied: true } } as never })
  on('model.complete', async (_: any, e: any) => {
    prompts.push(String(e.prompt))
    const rep: Record<string, unknown> = {}
    for (const id of ['a1', 'b2', 'c3']) if (String(e.prompt).includes('"id":"' + id + '"'))
      rep[id] = { categorie: id === 'b2' ? 'Mesure & donnees' : 'Outillage & gates', priorite: id === 'a1' ? 1 : 2, raison: 'cause racine du hub orphelin', doublon_de: null }
    return { value: { isAnswered: true, text: '```json\n' + JSON.stringify(rep) + '\n```', usage: { output_tokens: 42 } } } as never
  })

  const r = await $.command.run({ command: 'next-sessions', args: '' })
  expect(r.text).toContain('2 propositions')
  expect(prompts.length).toBe(1)

  const ui = await $.ui.mount({ plugin: 'om-propositions', surface: 'terminal', component: 'Pane', props: {} as never, requestId: 'om-propositions' })
  expect(await ui.find({ type: 'Text', text: /OUTILLAGE & GATES/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /1 P1/ })).toBeDefined()
  await ui.press({ key: 'v-a1' })
  await ui.press({ key: 'c-a1' })
  expect(copies[0]).toBe('make fast P="Projet. Objectif : Seuil editorial"')

  // Une proposition de plus : seule elle part chez Haiku, les deux autres en reference.
  lot = [...lot, item('c3', 'Corriger session-outcome', 'S4-hub-visibilite')]
  await ui.press({ key: 'rafraichir' })
  expect(prompts.length).toBe(2)
  expect(prompts[1]).toContain('"id":"c3"')
  expect(prompts[1]).not.toContain('"id":"a1"')
  expect(prompts[1]).toContain('a1 : Seuil editorial')
  await ui.unmount()
})
