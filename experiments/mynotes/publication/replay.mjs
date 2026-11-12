// Compare reserved native traces with API, public pages, and committed SQLite rows.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
const [base, source, database, destination] = process.argv.slice(2);
const { traces } = JSON.parse(readFileSync(source, 'utf8'));
const db = new DatabaseSync(database, { readOnly: true });
const saved = db.prepare('SELECT document FROM records WHERE collection = ? AND identity = ?');
const normal = (value) => value && typeof value === 'object'
  ? Array.isArray(value) ? value.map(normal) : Object.fromEntries(Object.entries(value)
    .filter(([key]) => !['created_at', 'updated_at', 'published_at'].includes(key)).map(([key, item]) => [key, normal(item)]))
  : value;

async function req(method, path, data, type = 'application/json') {
  const response = await fetch(base + path, {
    method, headers: data === null ? {} : { 'content-type': type },
    body: data === null ? undefined : type === 'application/json' ? JSON.stringify(data) : data,
  });
  const text = await response.text();
  return { status: response.status,
    body: response.headers.get('content-type')?.startsWith('application/json') ? JSON.parse(text) : text,
    etag: response.headers.get('etag') };
}
function row(collection, slug) {
  const result = saved.get(collection, slug);
  return result ? JSON.parse(result.document) : null;
}
const report = { journeys: [], mutations: 0, privateReads: 0, publicReads: 0, sqliteSnapshots: 0 };
for (const trace of traces) {
  assert.equal((await req('POST', '/api/v1/notes', trace.seed)).status, 201);
  for (const [index, step] of trace.steps.entries()) {
    const actual = await req(step.method, step.route, step.body, step.mediaType);
    const label = trace.slug + ':' + index + ':' + step.event;
    assert.equal(actual.status, step.result.status, label + ': mutation status');
    assert.deepEqual(normal(actual.body), normal(step.result.body), label + ': mutation response');
    report.mutations++;
    const privateNote = await req('GET', '/api/v1/notes/' + trace.slug, null);
    assert.equal(privateNote.status, step.after.private.status, label + ': private status');
    assert.deepEqual(normal(privateNote.body), normal(step.after.private.body), label + ': private response');
    if (privateNote.status === 200) {
      assert.equal(privateNote.etag, '"' + privateNote.body.updated_at + '"');
      assert.equal(row('notes', trace.slug).title, privateNote.body.title);
      assert.equal(row('notes', trace.slug).content, privateNote.body.content);
    } else assert.equal(row('notes', trace.slug), null);
    report.privateReads++;
    const publicPage = await req('GET', '/public/notes/' + trace.slug, null);
    assert.equal(publicPage.status, step.after.public.status, label + ': public status');
    const persisted = row('publications', trace.slug);
    assert.deepEqual(normal(persisted), normal(step.after.public.row), label + ': SQLite snapshot');
    if (persisted) {
      assert.ok(publicPage.body.includes('<h1>' + persisted.title + '</h1>'));
      assert.ok(publicPage.body.includes(persisted.html));
      assert.equal(privateNote.body.published_at, persisted.published_at);
    } else if (privateNote.status === 200) {
      assert.equal(Object.hasOwn(privateNote.body, 'published_at'), false);
    }
    report.publicReads++;
    report.sqliteSnapshots++;
  }
  report.journeys.push({ slug: trace.slug, mutations: trace.steps.length, passed: true });
}
db.close();
writeFileSync(destination, JSON.stringify(report, null, 2) + '\n');
console.log(`${report.journeys.length} reserved native journeys matched: ${report.mutations} mutations, ${report.privateReads} private reads, ${report.publicReads} public reads and ${report.sqliteSnapshots} stored snapshots.`);
