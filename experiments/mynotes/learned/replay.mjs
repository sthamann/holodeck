// Independent reference results stay in the test process, never in the Deck.
import { readFileSync, writeFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { connect } from 'node:net';
const [base, source, destination] = process.argv.slice(2);
const observations = JSON.parse(readFileSync(source, 'utf8'));

await new Promise((resolve, reject) => {
  const socket = connect(55766, '127.0.0.1');
  socket.on('connect', () => { socket.destroy(); reject(new Error('Native MyNotes still listening')); });
  socket.on('error', (error) => error.code === 'ECONNREFUSED' ? resolve() : reject(error));
});
const comparable = (value) => Array.isArray(value) ? value.map(comparable)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.entries(value).filter(([key]) => !['created_at', 'updated_at', 'published_at'].includes(key))
      .map(([key, value]) => [key, comparable(value)])) : value;
const etags = new Map();
const comparisons = [];
for (const observation of observations) {
  const { workspace, label, method, route, body, headers, result } = observation;
  const sent = { ...headers };
  if (sent['if-match']) {
    sent['if-match'] = etags.get(route)?.get(sent['if-match']) ?? sent['if-match'];
  }
  const response = await fetch(base + '/api/v1' + route, {
    method, headers: { origin: base, 'content-type': 'application/json', ...sent },
    body: body === null ? undefined : JSON.stringify(body),
  });
  const actual = { status: response.status, body: response.status === 204 ? null : await response.json() };
  const actualTag = response.headers.get('etag');
  if (result.etag && actualTag) {
    if (!etags.has(route)) etags.set(route, new Map());
    etags.get(route).set(result.etag, actualTag);
  }
  const expected = { status: result.status, body: comparable(result.body) };
  const normalized = { status: actual.status, body: comparable(actual.body) };
  const equal = isDeepStrictEqual(normalized, expected)
    && (!result.etag || actualTag === '"' + actual.body?.updated_at + '"');
  comparisons.push(equal ? { workspace, label, equal } : { workspace, label, equal, expected, actual: normalized });
}
const matched = comparisons.filter(({ equal }) => equal).length;
writeFileSync(destination, JSON.stringify({ matched, total: comparisons.length, comparisons }, null, 2) + '\n');
console.log(`${matched}/${comparisons.length} native HTTP interactions match (wall-clock values normalized)`);
for (const row of comparisons.filter((entry) => !entry.equal).slice(0, 16)) {
  console.log(JSON.stringify(row).slice(0, 2300));
}
if (matched !== comparisons.length) process.exitCode = 1;
