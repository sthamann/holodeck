// Generic local HTTP, component delivery and atomic record persistence.
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { loadForward } from './kernel.mjs';
import { loadGrounder } from './grounding.mjs';

const [modelFile, databaseFile, portString = '55770'] = process.argv.slice(2);
if (!modelFile || !databaseFile) throw new Error('Usage: node host.mjs CHECKPOINT SQLITE [PORT]');
const port = Number(portString);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid port');
const model = JSON.parse(readFileSync(modelFile, 'utf8'));
const forward = loadForward(model);
const ground = loadGrounder(process.env.HOLODECK_GROUNDING);
const db = new DatabaseSync(databaseFile);
db.exec(`
  PRAGMA journal_mode=WAL;
  PRAGMA synchronous=FULL;
  PRAGMA busy_timeout=5000;
  CREATE TABLE IF NOT EXISTS records (
    collection TEXT NOT NULL, identity TEXT NOT NULL, document TEXT NOT NULL,
    PRIMARY KEY (collection, identity)
  ) WITHOUT ROWID;
`);
const all = db.prepare('SELECT collection, identity, document FROM records ORDER BY collection, identity');
const put = db.prepare('INSERT INTO records VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET document = excluded.document');
const remove = db.prepare('DELETE FROM records WHERE collection = ? AND identity = ?');
const routes = model.routes.map((route) => ({ ...route, segments: route.path.split('/') }));

function routeFor(method, path) {
  const parts = path.split('/').map(decodeURIComponent);
  for (const route of routes) {
    if (route.method !== method || parts.length !== route.segments.length) continue;
    const params = Object.create(null);
    const matched = route.segments.every((value, i) => {
      if (!value.startsWith(':')) return value === parts[i];
      params[value.slice(1)] = parts[i];
      return Boolean(parts[i]);
    });
    if (matched) return { entry: route.entry, params };
  }
  return null;
}

function transaction({ entry, ...input }, writable) {
  db.exec(writable ? 'BEGIN IMMEDIATE' : 'BEGIN');
  try {
    const state = Object.create(null);
    for (const row of all.iterate()) {
      (state[row.collection] ??= Object.create(null))[row.identity] = JSON.parse(row.document);
    }
    const result = forward(entry, { ...input, state, now: Date.now() });
    if (!Number.isInteger(result.status) || result.status < 200 || result.status > 599 ||
        !Array.isArray(result.effects)) throw new Error('Invalid model response');
    if ((!writable || result.status >= 400) && result.effects.length) throw new Error('Effects forbidden on this response');
    for (const effect of result.effects) {
      if (typeof effect.collection !== 'string' || typeof effect.key !== 'string') throw new Error('Invalid record key');
      if (effect.op === 'put') put.run(effect.collection, effect.key, JSON.stringify(effect.value));
      else if (effect.op === 'delete') remove.run(effect.collection, effect.key);
      else throw new Error('Unknown effect');
    }
    db.exec('COMMIT');
    return result;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

const staticFiles = new Map([
  ['/', ['text/html; charset=utf-8', 'index.html']],
  ['/render.mjs', ['text/javascript; charset=utf-8', 'render.mjs']],
  ['/surface.css', ['text/css; charset=utf-8', 'surface.css']],
].map(([path, [mime, file]]) => [path, [mime, readFileSync(new URL('./web/' + file, import.meta.url))]]));

function send(response, status, body, headers = {}) {
  if (status === 204) { response.writeHead(status, headers); response.end(); return; }
  response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', ...headers });
  response.end(typeof body === 'string' && headers['content-type'] ? body : JSON.stringify(body));
}

const authority = '127.0.0.1:' + port;
createServer(async (request, response) => {
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
  if (request.headers.host !== authority || (request.headers.origin && request.headers.origin !== 'http://' + authority)) {
    send(response, 403, { error: 'Invalid origin' });
    return;
  }
  try {
    const url = new URL(request.url, 'http://' + authority);
    const asset = request.method === 'GET' && staticFiles.get(url.pathname);
    if (asset) { response.writeHead(200, { 'content-type': asset[0] }); response.end(asset[1]); return; }
    let route;
    try { route = routeFor(request.method, url.pathname); }
    catch { send(response, 400, { error: 'Invalid path' }); return; }
    if (!route) { send(response, 404, { error: 'not found' }); return; }
    let size = 0;
    const chunks = [];
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 4_100_000) { send(response, 413, { error: 'Request too large' }); return; }
      chunks.push(chunk);
    }
    const raw = Buffer.concat(chunks).toString('utf8');
    const mediaType = (request.headers['content-type'] ?? '').split(';')[0].trim();
    let body = raw || null;
    if (mediaType === 'application/json') {
      try { body = raw ? JSON.parse(raw) : null; }
      catch { send(response, 400, { error: 'Invalid JSON' }); return; }
    }
    const query = Object.create(null);
    for (const [key, value] of url.searchParams) (query[key] ??= []).push(value);
    if (ground && request.method === 'GET' && url.pathname === '/holo' && query.intent?.[0] && !query.view?.[0]) {
      if (query.intent[0].length > 512) { send(response, 400, { error: 'Intent is too long' }); return; }
      try { query.view = [await ground(query.intent[0])]; }
      catch { send(response, 503, { error: 'Local model is unavailable' }); return; }
    }
    const result = transaction({ ...route, body, headers: request.headers, query, mediaType }, request.method !== 'GET');
    send(response, result.status, result.body, result.headers);
  } catch (error) {
    console.error(error);
    if (!response.headersSent) send(response, 500, { error: 'Model execution failed' });
    else response.destroy();
  }
}).listen(port, '127.0.0.1', () => console.log('Deck listening at http://' + authority));
