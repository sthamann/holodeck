import { readFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { loadForward } from '../deck/kernel.mjs';
const [modelPath, tracesPath, part] = process.argv.slice(2);
const load = (p) => JSON.parse(readFileSync(p, 'utf8'));
const model = load(modelPath);
const traces = load(tracesPath);
const forward = loadForward(model);
function call(name, ...args) {
  const routine = model.routines[name];
  if (!routine) throw new Error('Missing routine ' + name);
  return forward(routine.entry, Object.fromEntries(routine.inputs.map((key, i) => [key, args[i]])));
}
function raw({ slug, title, content, created_at, updated_at, tags }) {
  return { slug, title, content, created_at, updated_at, tags: tags.map((tag) => tag.slug) };
}
let failures = 0;
function check(label, expected, produce) {
  try {
    const actual = produce();
    if (isDeepStrictEqual(actual, expected)) return;
    console.log(JSON.stringify({ label, expected, actual }).slice(0, 1400));
  } catch (error) { console.log(JSON.stringify({ label, error: error.message })); }
  failures++;
}
for (const workspace of [...new Set(traces.map((row) => row.workspace))].slice(0, 2)) {
  const rows = traces.filter((row) => row.workspace === workspace);
  const one = (label) => rows.find((row) => row.label === label).result.body;
  const note = one('new note');
  const destination = one('link destination');
  const catalog = { [note.slug]: raw(note), [destination.slug]: raw(destination) };
  if (part === 'links') {
    check('outgoing links from a newly created note', note, () => call('projectNote', raw(note), catalog));
    check('backlinks to the existing note', one('backlinks'), () => call('projectNote', raw(destination), catalog));
    check('excerpt of first child', one('split shallow headings').notes[0].excerpt,
      () => call('excerpt', one('read child').content));
  }
  if (part === 'sections') {
    check('shallow split in original heading order',
      rows.filter((row) => row.label === 'read child').map(({ result: { body: { title, content } } }) => ({ title, content })),
      () => call('sections', one('retained source').content));
  }
}
process.exitCode = failures ? 1 : 0;
