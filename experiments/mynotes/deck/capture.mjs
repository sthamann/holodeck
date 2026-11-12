// Teaching data comes from a separate, temporary native MyNotes workspace.
import { writeFileSync } from 'node:fs';
const [base, destination] = process.argv.slice(2);
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Teacher must be local');
const cases = [];
const state = { notes: {} };
async function send(method, path, body, headers = {}) {
  const response = await fetch(base + path, { method, headers: {
    origin: base, 'content-type': 'application/json', ...headers,
  }, body: JSON.stringify(body) });
  return { status: response.status, body: await response.json() };
}
async function fixture(slug, title, content = '') {
  const created = await send('POST', '/api/v1/notes', { slug, title, content });
  if (created.status !== 201) throw new Error(JSON.stringify(created));
  const { incoming_links, outgoing_links, ...record } = created.body;
  state.notes[slug] = { ...record, tags: record.tags.map((tag) => tag.slug) };
}
async function observe(name, method, path, route, body, params, headers, selectors) {
  const actual = await send(method, path, body, headers);
  const expected = selectors.map((selector) => selector.reduce((value, key) => value?.[key] ?? null, actual));
  cases.push({ name, method, route, inputs: {
    state: structuredClone(state), body, params, headers, query: {}, now: Date.now(),
  }, selectors, expected });
}

await fixture('planning', 'Planning');
await observe('automatic name collision', 'POST', '/api/v1/notes', '/api/v1/notes',
  { title: 'Planning' }, {}, {}, [['status'], ['body', 'slug']]);
await observe('expired edit revision', 'PATCH', '/api/v1/notes/planning', '/api/v1/notes/:slug',
  { title: 'Lost change' }, { slug: 'planning' }, { 'if-match': '"1999-01-01T00:00:00.000Z"' },
  [['status'], ['body', 'error']]);
await fixture('quarterly-review', 'Quarterly review',
  'Preamble\n\n## Outcomes\n\n### Supporting notes\n\nEvidence.\n\n## Next quarter\n\nDecide.');
await observe('mixed heading levels', 'POST', '/api/v1/notes/quarterly-review/split', '/api/v1/notes/:slug/split',
  {}, { slug: 'quarterly-review' }, {},
  [['status'], ['body', 'notes', 0, 'title'], ['body', 'notes', 1, 'title'], ['body', 'notes', 2]]);
writeFileSync(destination, JSON.stringify(cases, null, 2) + '\n');
console.log(`Captured ${cases.length} distinguishing native traces`);
