import { readFileSync, writeFileSync } from 'node:fs';
const [instruction, output] = process.argv.slice(2);
const response = await fetch('http://127.0.0.1:11434/api/generate', {
  method: 'POST', headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(420000),
  body: JSON.stringify({ model: process.env.HOLODECK_QWEN_GENERATOR || 'qwen2.5-coder:32b',
    stream: false, think: false, prompt: readFileSync(instruction, 'utf8'),
    options: { temperature: 0, num_ctx: 8192, num_predict: 2800 } }),
});
if (!response.ok) throw new Error(await response.text());
const { response: content, model, eval_count, done_reason } = await response.json();
const source = content.trim().replace(/^```(?:javascript|js)?\s*/u, '').replace(/\s*```$/u, '');
if (!source) throw new Error('Empty generated candidate');
writeFileSync(output, source + '\n');
writeFileSync(output + '.generation.json', JSON.stringify({ model, eval_count, done_reason }) + '\n');
console.log(model + ' produced ' + source.length + ' characters');
