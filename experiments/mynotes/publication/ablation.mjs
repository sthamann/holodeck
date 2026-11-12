import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
const [command, path, output] = process.argv.slice(2);
if (command === 'zero') {
  const checkpoint = JSON.parse(readFileSync(path, 'utf8'));
  const publication = checkpoint.sequenceBanks.publication;
  publication.nextWeights = publication.nextWeights.map((row) => row.map(() => 0));
  publication.nextBias = publication.nextBias.map(() => 0);
  writeFileSync(output, JSON.stringify(checkpoint) + '\n');
} else if (command === 'probe') {
  const base = path;
  const request = (method, url, data, type = 'application/json') => fetch(base + url, {
    method, headers: data === undefined ? {} : { 'content-type': type },
    body: data === undefined ? undefined : type === 'application/json' ? JSON.stringify(data) : data,
  });
  assert.equal((await request('POST', '/api/v1/notes', { slug: 'same-draft', title: 'Same draft', content: 'Copy' })).status, 201);
  assert.equal((await request('PUT', '/api/v1/notes/same-draft/publish', '<p>Copy</p>', 'text/html')).status, 200);
  const page = await request('GET', '/public/notes/same-draft');
  const holo = await (await request('GET', '/holo?view=publication&slug=same-draft')).json();
  writeFileSync(output, JSON.stringify({ publicStatus: page.status, title: holo.main.children[0].value }) + '\n');
} else throw new Error('Unknown ablation command');
