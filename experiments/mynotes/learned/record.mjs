// Capture actual teacher outcomes. Fixture code supplies inputs only.
import { writeFileSync } from 'node:fs';
const [base, destination, cohort = 'training'] = process.argv.slice(2);
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Teacher must be local');
const traces = [];
const names = cohort === 'training'
  ? ['river', 'orchard', 'garden', 'station', 'grove', 'forest', 'quay', 'market']
  : ['workshop', 'meadow', 'bridge'];

for (const name of names) {
  const slug = name + '-brief';
  const target = name + '-target';
  const topic = name + '-topic';
  const done = name + '-closed';
  const heading = name[0].toUpperCase() + name.slice(1);
  const get = {};
  async function request(label, method, route, body, headers = {}) {
    const response = await fetch(base + '/api/v1' + route, {
      method, headers: { origin: base, 'content-type': 'application/json', ...headers },
      body: body == null ? undefined : JSON.stringify(body),
    });
    const result = { status: response.status, body: response.status === 204 ? null : await response.json() };
    const etag = response.headers.get('etag');
    if (etag) result.etag = etag;
    traces.push({ workspace: name, label, method, route, body: body ?? null, headers, result });
    if (method === 'GET') get[route] = result;
    return result;
  }
  const content = [
    'Team introduction.', '', '## ' + heading + ' plan', '',
    'Read [[' + target + '|the reference]] and [[not-yet-created]].', '',
    '### Supporting details', '', '```md', '# Fake heading', '[[' + name + '-fake]]', '```', '',
    '## ' + heading + ' handoff', '', 'Prepare a response.',
  ].join('\n');
  await request('tag created', 'POST', '/tags', { slug: topic });
  await request('second tag created', 'POST', '/tags', { slug: done });
  await request('duplicate tag', 'POST', '/tags', { slug: topic });
  await request('link destination', 'POST', '/notes', { slug: target, title: heading + ' reference' });
  await request('new note', 'POST', '/notes', { slug, title: heading + ' brief', content, tags: [topic] });
  await request('duplicate explicit slug', 'POST', '/notes', { slug, title: heading + ' duplicate' });
  await request('derive and disambiguate slug', 'POST', '/notes', { title: heading + ' brief' });
  const before = await request('open', 'GET', '/notes/' + slug);
  await request('backlinks', 'GET', '/notes/' + target);
  const edit = await request('edit text and tags', 'PATCH', '/notes/' + slug,
    { title: '  ' + heading + ' revised  ', content: content + '\n\nReturn by Friday.', tags: [topic, done, topic] },
    { 'if-match': before.etag });
  await request('stale edit', 'PATCH', '/notes/' + slug, { title: 'stale' }, { 'if-match': before.etag });
  await request('no-op tag order', 'PATCH', '/notes/' + slug, { tags: [topic, done] }, { 'if-match': edit.etag });
  await request('bad tag', 'PATCH', '/notes/' + slug, { tags: [name + '-missing'] }, { 'if-match': edit.etag });
  await request('unknown split tag', 'POST', '/notes/' + slug + '/split', { tag: name + '-missing' });
  const split = await request('split shallow headings', 'POST', '/notes/' + slug + '/split', { tag: done });
  for (const child of split.body.notes) await request('read child', 'GET', '/notes/' + child.slug);
  await request('retained source', 'GET', '/notes/' + slug);
  await request('no headings', 'POST', '/notes/' + target + '/split', {});
  await request('filter notes', 'GET', '/notes?tag=' + done + '&sort=title&order=asc');
  await request('tag counts', 'GET', '/tags');
  await request('delete tag', 'DELETE', '/tags/' + topic);
  await request('read after tag detach', 'GET', '/notes/' + slug);
  await request('delete note', 'DELETE', '/notes/' + slug);
  await request('missing note', 'GET', '/notes/' + slug);
  await request('remaining backlinks', 'GET', '/notes/' + target);
}
writeFileSync(destination, JSON.stringify(traces) + '\n');
console.log(`Captured ${traces.length} native HTTP interactions across ${names.length} fixture families`);
