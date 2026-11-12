// Learn a normalized prototype readout from Qwen features of observed UI controls.
import { readFileSync, writeFileSync } from 'node:fs';
import { embed, predict } from '../deck/grounding.mjs';
const [browserPath, outputPath, reportPath] = process.argv.slice(2);
const traces = JSON.parse(readFileSync(browserPath, 'utf8'));
const tree = traces.map(({ before, after }) => before + '\n' + after).join('\n');
const targets = {
  new: [/button "(New note)"/u, /textbox "(Title)"/u],
  edit: [/button "(Edit)"/u, /button "(Save)"/u],
  split: [/button "(Split by headings)"/u, /dialog "(Split note by headings)"/u],
  tags: [/(?:button|tab) "(Tags)"/u, /textbox "(Add tag…)"/u],
  index: [/searchbox "(Full-text search)"/u, /searchbox "(Filter by title)"/u],
};
const examples = Object.entries(targets).flatMap(([target, patterns]) => patterns.map((pattern) => {
  const caption = pattern.exec(tree)?.[1];
  if (!caption) throw new Error('Missing native UI observation: ' + pattern);
  return { target, caption };
}));
const model = 'qwen3-embedding:0.6b';
const vectors = await embed(model, examples.map(({ caption }) => caption));
const rows = Object.keys(targets).map((target) => {
  const sum = Array(vectors[0].length).fill(0);
  examples.forEach((item, index) => {
    if (item.target === target) vectors[index].forEach((value, k) => { sum[k] += value; });
  });
  const norm = Math.hypot(...sum);
  return { target, weights: sum.map((value) => value / norm) };
});
const head = { model, rows };
const holdout = [
  ['I want to create a new note', 'new'],
  ['Edit the note I have open', 'edit'],
  ['Break this note into sections at its headings', 'split'],
  ['Show the tags I can use', 'tags'],
  ['Search the full text of my notes', 'index'],
];
const queries = await embed(model, holdout.map(([text]) => text));
const results = holdout.map(([text, expected], i) => ({ text, expected, actual: predict(head, queries[i]) }));
writeFileSync(outputPath, JSON.stringify(head) + '\n');
writeFileSync(reportPath, JSON.stringify({ examples, results }, null, 2) + '\n');
console.log(`Fitted ${rows.length} Qwen readouts on ${examples.length} observed labels. Held out: ${results.filter((r) => r.expected === r.actual).length}/${results.length}.`);
