// Execute the pinned native app and observe its private, public and durable effects.
import { writeFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import assert from 'node:assert/strict';
const [base, database, mode, destination] = process.argv.slice(2);
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Teacher must be local');
const db = new DatabaseSync(database);
db.exec(`
  PRAGMA busy_timeout=5000;
  CREATE TABLE IF NOT EXISTS holodeck_publication_events (
    id INTEGER PRIMARY KEY, op TEXT, title TEXT, html TEXT, published_at TEXT
  );
  CREATE TRIGGER IF NOT EXISTS holodeck_public_insert AFTER INSERT ON published_notes BEGIN
    INSERT INTO holodeck_publication_events (op,title,html,published_at)
      VALUES ('put',new.title,new.html,new.published_at);
  END;
  CREATE TRIGGER IF NOT EXISTS holodeck_public_update AFTER UPDATE ON published_notes BEGIN
    INSERT INTO holodeck_publication_events (op,title,html,published_at)
      VALUES ('put',new.title,new.html,new.published_at);
  END;
  CREATE TRIGGER IF NOT EXISTS holodeck_public_delete AFTER DELETE ON published_notes BEGIN
    INSERT INTO holodeck_publication_events (op,title,html,published_at) VALUES ('delete',NULL,NULL,NULL);
  END;
`);
const eventCounter = db.prepare('SELECT COALESCE(MAX(id), 0) AS id FROM holodeck_publication_events');
const events = db.prepare('SELECT op,title,html,published_at FROM holodeck_publication_events WHERE id > ? ORDER BY id');
const snapshot = db.prepare(`SELECT p.title,p.html,p.published_at FROM published_notes p
  JOIN notes n ON n.id=p.note_id WHERE n.slug=?`);
const alphabet = ['edit', 'publish', 'unpublish', 'delete'];
const fragment = (text) => '<p>' + text + '</p>';
const publicURL = (slug) => '/public/notes/' + slug;
async function req(method, path, data, type = 'application/json') {
  const response = await fetch(base + path, {
    method, headers: { origin: base, ...(data === undefined ? {} : { 'content-type': type }) },
    body: data === undefined ? undefined : type === 'application/json' ? JSON.stringify(data) : data,
  });
  const text = await response.text();
  const body = response.headers.get('content-type')?.startsWith('application/json') ? JSON.parse(text) : text;
  return { status: response.status, body, etag: response.headers.get('etag') };
}
async function observe(slug) {
  const note = await req('GET', '/api/v1/notes/' + slug);
  const page = await req('GET', publicURL(slug));
  const saved = snapshot.get(slug) ?? null;
  assert.equal(page.status, saved ? 200 : 404);
  if (saved) assert.ok(page.body.includes(saved.html));
  return { private: note, public: { status: page.status, row: saved } };
}
async function captureSequence(actions, index) {
  const slug = mode + '-publication-' + index;
  const seed = { slug, title: 'Draft ' + index, content: 'Initial text ' + index };
  const start = await req('POST', '/api/v1/notes', seed);
  assert.equal(start.status, 201);
  const initial = await observe(slug);
  let before = initial;
  const steps = [];
  for (const [i, event] of actions.entries()) {
    const last = eventCounter.get().id;
    const privatePath = '/api/v1/notes/' + slug;
    const body = event === 'edit' ? { title: 'Private revision ' + index + ' ' + i, content: 'Working text ' + index + ' ' + i }
      : event === 'publish' ? fragment(before.private.status === 200 ? before.private.body.content : 'Unavailable note')
      : undefined;
    const method = { edit: 'PATCH', publish: 'PUT', unpublish: 'DELETE', delete: 'DELETE' }[event];
    const route = privatePath + (['publish', 'unpublish'].includes(event) ? '/publish' : '');
    const mediaType = event === 'publish' ? 'text/html' : 'application/json';
    const result = await req(method, route, body, mediaType);
    const changes = events.all(last);
    const after = await observe(slug);
    steps.push({ event, method, route, body: body ?? null, mediaType, result, changes, after });
    before = after;
  }
  return { slug, seed, initial, steps };
}
function products(size) {
  if (!size) return [[]];
  return products(size - 1).flatMap((prefix) => alphabet.map((event) => [...prefix, event]));
}
const holdout = [
  ['publish', 'edit', 'edit', 'publish', 'unpublish', 'unpublish', 'edit', 'publish', 'delete', 'publish'],
  ['edit', 'publish', 'edit', 'unpublish', 'edit', 'publish', 'publish', 'edit', 'publish', 'delete', 'unpublish'],
  ['unpublish', 'edit', 'edit', 'publish', 'edit', 'publish', 'edit', 'unpublish', 'publish', 'edit', 'delete', 'edit'],
  ['publish', 'delete', 'delete', 'publish', 'edit', 'unpublish'],
  ['edit', 'edit', 'publish', 'edit', 'edit', 'publish', 'unpublish', 'edit', 'publish', 'edit', 'publish'],
  ['publish', 'edit', 'unpublish', 'unpublish', 'unpublish', 'publish', 'edit', 'publish', 'edit', 'unpublish'],
];
const sequences = mode === 'train' ? products(4) : mode === 'holdout' ? holdout : null;
if (!sequences) throw new Error('Expected train or holdout');
const traces = [];
for (const [i, sequence] of sequences.entries()) traces.push(await captureSequence(sequence, i));
db.close();
writeFileSync(destination, JSON.stringify({ alphabet, traces }) + '\n');
console.log(`Recorded ${traces.length} native ${mode} journeys and ${traces.reduce((n, t) => n + t.steps.length, 0)} mutation attempts`);
