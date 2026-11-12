import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const [fittedFile, output] = process.argv.slice(2);
if (!fittedFile || !output) throw new Error('usage: node build.mjs FITTED_PROFILE CHECKPOINT');
const read = (file) => JSON.parse(readFileSync(file, 'utf8'));
const fitted = read(fittedFile);
const base = read(new URL('../versions/evidence/original.recurrent.json', import.meta.url));
const aliases = {
  basePublicationList: ['GET', '/api/v1/notes'],
  basePublicationGet: ['GET', '/api/v1/notes/:slug'],
  basePublicationCreate: ['POST', '/api/v1/notes'],
  basePublicationPatch: ['PATCH', '/api/v1/notes/:slug'],
  basePublicationDelete: ['DELETE', '/api/v1/notes/:slug'],
  basePublicationHolo: ['GET', '/holo'],
};
for (const [name, [method, path]] of Object.entries(aliases)) {
  const route = base.routes.find((r) => r.method === method && r.path === path);
  if (!route) throw new Error('Missing base route ' + path);
  base.routines[name] = { entry: route.entry, inputs: [] };
}
const allowed = { 'private.title': 'note.title', 'request.body': 'fragment', 'response.published_at': 'at' };
const fields = Object.entries(fitted.bindings).map(([field, source]) => {
  if (!Object.hasOwn(allowed, source)) throw new Error('Unknown learned binding ' + source);
  return JSON.stringify(field) + ':' + allowed[source];
});
const controls = Object.fromEntries(fitted.controls.map(({ label, method, path, mediaType }) => [label, { method, path, mediaType }]));
const tokens = Object.fromEntries(fitted.alphabet.map((name, i) => [name, i]));
const adapters = [
  'function missingHistory() { return ' + JSON.stringify(fitted.missingHistory) + '; }',
  'function eventToken(action) { return (' + JSON.stringify(tokens) + ')[action]; }',
  'function learnedControl(label) { return (' + JSON.stringify(controls) + ')[label]; }',
  'function learnedSnapshot(note, fragment, at) { return {' + fields.join(',') + '}; }',
].join('\n');
const workspace = mkdtempSync(join(tmpdir(), 'holodeck-publication-'));
try {
  const imported = join(workspace, 'base.json');
  const program = join(workspace, 'program.js');
  writeFileSync(imported, JSON.stringify(base));
  writeFileSync(program, readFileSync(new URL('./program.js', import.meta.url), 'utf8') + '\n' + adapters + '\n');
  const compiler = fileURLToPath(new URL('../learned/lower.cjs', import.meta.url));
  const result = spawnSync(process.execPath, [compiler, program, output],
    { env: { ...process.env, HOLODECK_IMPORTS: imported, HOLODECK_ALIASES: '{}' }, stdio: 'inherit' });
  if (result.error || result.status !== 0) throw result.error ?? new Error('Model lowering failed');
  const checkpoint = read(output);
  checkpoint.sequenceModel = base.sequenceModel;
  checkpoint.sequenceBanks = { ...base.sequenceBanks, publication: fitted.weights };
  writeFileSync(output, JSON.stringify(checkpoint) + '\n');
} finally {
  rmSync(workspace, { recursive: true, force: true });
}
