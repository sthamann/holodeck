// Ask Qwen for minimal, exact textual changes. The compiler checks the result.
import { readFileSync, writeFileSync } from 'node:fs';
const [file, diagnostics] = process.argv.slice(2);
const read = (path) => readFileSync(path, 'utf8');
const source = read(file);
const prompt = `Repair the candidate with the smallest necessary changes. Return JSON with edits as an array of {old,new}. Each old must match exactly once in the supplied source. Multiple edits may be used; avoid overlapping spans. Return no markdown. In this restricted language use const, early return, array or record literals, map/filter/fold, and direct helper calls. No mutation, loops or JavaScript methods.\nDIAGNOSTICS:\n${read(diagnostics)}\nSOURCE:\n${source}`;
const response = await fetch('http://127.0.0.1:11434/api/generate', {
  method: 'POST', headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(420000),
  body: JSON.stringify({ model: process.env.HOLODECK_QWEN_GENERATOR || 'qwen2.5-coder:32b',
    stream: false, think: false, prompt, format: { type: 'object', required: ['edits'],
      properties: { edits: { type: 'array', items: { type: 'object', required: ['old', 'new'],
        properties: { old: { type: 'string' }, new: { type: 'string' } } } } } },
    options: { temperature: 0, num_ctx: 12288, num_predict: 3500 } }),
});
if (!response.ok) throw new Error(await response.text());
const { response: content } = await response.json();
const { edits } = JSON.parse(content);
let patched = source;
for (const { old, new: replacement } of edits) {
  if (!old || patched.indexOf(old) < 0 || patched.indexOf(old) !== patched.lastIndexOf(old)) {
    throw new Error('Ambiguous Qwen patch: ' + old.slice(0, 100));
  }
  patched = patched.replace(old, () => replacement);
}
if (patched === source) throw new Error('Qwen did not change the candidate');
writeFileSync(file + '.patch.json', JSON.stringify(edits, null, 2) + '\n');
writeFileSync(file, patched);
console.log(`Qwen applied ${edits.length} candidate repairs`);
