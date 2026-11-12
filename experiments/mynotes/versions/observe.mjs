// Capture the teacher's service decisions and independent workspace workflows.
import { writeFileSync } from 'node:fs';
const [base, mode, destination] = process.argv.slice(2);
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Only local teachers are supported');

const observations = [];
async function request(workspace, label, method, route, body = null, headers = {}) {
  const response = await fetch(base + '/api/v1' + route, {
    method, headers: { origin: base, 'content-type': 'application/json', ...headers },
    body: body === null ? undefined : JSON.stringify(body),
  });
  const result = { status: response.status, body: response.status === 204 ? null : await response.json() };
  if (response.headers.has('etag')) result.etag = response.headers.get('etag');
  observations.push({ workspace, label, method, route, body, headers, result });
  return result;
}
const checked = (result, status, label) => {
  if (result.status !== status) throw new Error(label + ': ' + JSON.stringify(result));
  return result;
};
const notes = '/notes/';
if (mode === 'fit') {
  const rows = [];
  for (const slug of ['observed-source', 'observed-explicit']) {
    checked(await request('fit', 'seed tag', 'POST', '/tags', { slug }), 201, slug);
  }
  const prefixes = [[]];
  for (let level = 1; level <= 6; level++) prefixes.push([level], [level, level]);
  for (const [i, depths] of prefixes.entries()) {
    const slug = 'observed-' + i;
    const content = depths.map((depth, j) => '#'.repeat(depth) + ' Sample ' + i + ' ' + j).join('\n\n');
    checked(await request('fit', 'seed note', 'POST', '/notes', { slug, title: slug, content, tags: ['observed-source'] }), 201, slug);
    const result = await request('fit', 'observe split', 'POST', notes + slug + '/split', { tag: 'observed-explicit' });
    if (![201, 400].includes(result.status)) throw new Error(JSON.stringify(result));
    rows.push({ level: depths[0] ?? 0, count: depths.length, allowed: result.status === 201,
      inherited: result.status === 201 ? result.body.notes.every(({ tags }) => tags.some((t) => t.slug === 'observed-source')) : null,
      refusal: result.status === 400 ? result.body.error : null });
  }
  writeFileSync(destination, JSON.stringify(rows, null, 2) + '\n');
  console.log(`Captured ${rows.length} native split decision labels`);
} else if (mode === 'holdout') {
  const cohorts = [
    ['cedar', [2, 3, 3, 2]],
    ['river', [2, 3, 2]],
    ['harbor', [3, 2, 5, 4, 2, 5, 3, 5, 4, 2, 3, 5]],
  ];
  for (const [space, levels] of cohorts) {
    const sourceTag = space + '-source';
    const reviewTag = space + '-review';
    const slug = space + '-desk';
    const target = space + '-index';
    const call = (...args) => request(space, ...args);
    const content = [
      'Workspace preamble.', '',
      ...levels.flatMap((level, index) => [
        '#'.repeat(level) + ' ' + space + ' part ' + (index + 1), '',
        index === 0 ? 'Consult [[' + target + ']].' : 'Prepare the next step.', '',
        ...(index === 0 ? ['```md', '# fenced title', '[[ignored-target]]', '```', ''] : []),
      ]),
    ].join('\n');
    checked(await call('create source tag', 'POST', '/tags', { slug: sourceTag }), 201, sourceTag);
    checked(await call('create review tag', 'POST', '/tags', { slug: reviewTag }), 201, reviewTag);
    checked(await call('create reference', 'POST', '/notes', { slug: target, title: space + ' index' }), 201, target);
    checked(await call('create document', 'POST', '/notes', { slug, title: space + ' desk', content, tags: [sourceTag] }), 201, slug);
    const old = checked(await call('read original', 'GET', notes + slug), 200, slug);
    checked(await call('read backlink', 'GET', notes + target), 200, target);
    const edit = checked(await call('edit document', 'PATCH', notes + slug,
      { title: space + ' desk revised', content: content + '\nA new line.' }, { 'if-match': old.etag }), 200, slug);
    checked(await call('reject stale edit', 'PATCH', notes + slug, { title: 'stale' }, { 'if-match': old.etag }), 412, slug);
    checked(await call('unchanged edit', 'PATCH', notes + slug, { tags: [sourceTag] }, { 'if-match': edit.etag }), 200, slug);
    checked(await call('reject invalid tag', 'POST', notes + slug + '/split', { tag: space + '-unknown' }), 400, slug);
    const split = await call('versioned split', 'POST', notes + slug + '/split', { tag: reviewTag });
    if (![201, 400].includes(split.status)) throw new Error(JSON.stringify(split));
    if (split.status === 201) {
      for (const child of split.body.notes) checked(await call('read child', 'GET', notes + child.slug), 200, child.slug);
    }
    checked(await call('source unchanged', 'GET', notes + slug), 200, slug);
    checked(await call('list workspace', 'GET', '/notes?sort=title&order=asc'), 200, slug);
    checked(await call('read backlinks after split', 'GET', notes + target), 200, target);
    checked(await call('list tag counts', 'GET', '/tags'), 200, sourceTag);
    checked(await call('delete review tag', 'DELETE', '/tags/' + reviewTag), 204, reviewTag);
    checked(await call('delete source', 'DELETE', notes + slug), 204, slug);
    checked(await call('read final backlinks', 'GET', notes + target), 200, target);
    checked(await call('read remaining notes', 'GET', '/notes?sort=title&order=asc'), 200, target);
  }
  writeFileSync(destination, JSON.stringify(observations) + '\n');
  console.log(`Recorded ${observations.length} independent native HTTP interactions`);
} else throw new Error('Expected fit or holdout');
