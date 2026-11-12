// Check that learned releases differ solely in recurrent parameters, and
// compare the two engines on long, composed token sequences.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { loadForward } from '../deck/kernel.mjs';
const [lab, output] = process.argv.slice(2);
const read = (name) => JSON.parse(readFileSync(join(lab, name + '.json'), 'utf8'));
const { sequenceModel: originalWeights, ...original } = read('original.recurrent');
const { sequenceModel: reviewWeights, ...review } = read('review.recurrent');
assert.deepEqual(original, review, 'Version information leaked into the shared graph');
assert.notDeepEqual(originalWeights, reviewWeights);
let random = 4711;
const rand = (n) => {
  random = (Math.imul(random, 1664525) + 1013904223) >>> 0;
  return (random >>> 8) % n;
};
const inputs = [[]];
for (let encoded = 0; encoded < 6 ** 4; encoded++) {
  inputs.push(Array.from({ length: 4 }, (_, digit) => Math.floor(encoded / 6 ** digit) % 6 + 1));
}
for (let i = 0; i < 1024; i++) inputs.push(Array.from({ length: i % 45 + 4 }, () => rand(6) + 1));
const summary = {};
for (const version of ['original', 'review']) {
  function evaluate(model, tokens) {
    const routine = model.routines.splitPolicy;
    return loadForward(model)(routine.entry, { [routine.inputs[0]]: tokens });
  }
  const compiled = read(version + '.compiled');
  const recurrent = read(version + '.recurrent');
  for (const tokens of inputs) assert.deepEqual(evaluate(recurrent, tokens), evaluate(compiled, tokens), JSON.stringify(tokens));
  summary[version] = { matched: inputs.length, maximumLength: 48 };
}
writeFileSync(output, JSON.stringify({ identicalGraph: true, ...summary }, null, 2) + '\n');
console.log(`Both recurrent weights match their compiled counterpart on ${inputs.length} sequences, up to 48 headings.`);
