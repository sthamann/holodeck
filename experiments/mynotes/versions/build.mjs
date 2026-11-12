// Build the same application profile against either policy implementation.
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
const [kind, input, output] = process.argv.slice(2);
if (!['compiled', 'recurrent'].includes(kind) || !input || !output) {
  throw new Error('usage: node build.mjs compiled|recurrent POLICY_OR_WEIGHTS OUTPUT');
}
const read = (file) => JSON.parse(readFileSync(file, 'utf8'));
const configuration = read(input);
const workspace = mkdtempSync(join(tmpdir(), 'holodeck-profile-'));
const candidate = join(workspace, 'candidate.json');
try {
  const env = { ...process.env, HOLODECK_NOTES_POLICY: JSON.stringify(kind === 'compiled'
    ? configuration : { recurrent: true }) };
  const compiler = fileURLToPath(new URL('../deck/compile.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [compiler, candidate], { env, stdio: 'inherit' });
  if (result.error || result.status !== 0) throw result.error ?? new Error('Workspace compilation failed');
  const { checkpoint, choices } = read(candidate);
  const fit = read(new URL('../deck/evidence/fit.json', import.meta.url)).selected;
  for (const [name, { row, values }] of Object.entries(choices)) {
    if (!values.includes(fit[name])) throw new Error('Missing fitted common workspace selector ' + name);
    checkpoint.parameters[row][1] = fit[name];
  }
  if (kind === 'recurrent') checkpoint.sequenceModel = configuration;
  writeFileSync(output, JSON.stringify(checkpoint) + '\n');
} finally {
  rmSync(workspace, { recursive: true, force: true });
}
