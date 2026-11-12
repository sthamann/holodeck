// Apply the common prefix of both native traces once, before switching models.
import { readFileSync } from 'node:fs';
const [base, trace] = process.argv.slice(2);
const entries = JSON.parse(readFileSync(trace, 'utf8'));
const seed = new Set(['create source tag', 'create review tag', 'create reference', 'create document', 'edit document']);
for (const { workspace, label, method, route, body, result } of entries) {
  if (!['cedar', 'river'].includes(workspace) || !seed.has(label)) continue;
  const url = base + '/api/v1' + route;
  const headers = { origin: base, 'content-type': 'application/json' };
  if (method === 'PATCH') headers['if-match'] = (await fetch(url)).headers.get('etag');
  const response = await fetch(url, { method, headers, body: JSON.stringify(body) });
  if (response.status !== result.status) throw new Error(label + ': ' + await response.text());
}
