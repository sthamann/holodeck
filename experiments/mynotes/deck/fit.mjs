// Exact finite search over checkpoint candidates. Training checks observed
// output projections; the full workflow and intervention are separate tests.
import { readFileSync, writeFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { loadForward } from './kernel.mjs';

const [candidatePath, evidencePath, outputPath, reportPath, counterfactualPath] = process.argv.slice(2);
const read = (path) => JSON.parse(readFileSync(path, 'utf8'));
const { checkpoint, choices } = read(candidatePath);
const cases = read(evidencePath);
const sites = Object.entries(choices);
const assignments = sites.reduce((partial, [name, { values }]) =>
  partial.flatMap((prior) => values.map((value) => ({ ...prior, [name]: value }))), [{}]);

function instantiate(assignment) {
  const candidate = structuredClone(checkpoint);
  for (const [name, { row }] of sites) candidate.parameters[row][1] = assignment[name];
  return candidate;
}
function score(assignment) {
  const candidate = instantiate(assignment);
  const forward = loadForward(candidate);
  return cases.filter(({ method, route, inputs, selectors, expected }) => {
    const entry = candidate.routes.find(({ method: verb, path }) => verb === method && path === route)?.entry;
    if (entry === undefined) throw new Error('Unknown training route');
    const actual = forward(entry, inputs);
    return isDeepStrictEqual(expected, selectors.map((selector) =>
      selector.reduce((value, key) => value?.[key] ?? null, actual)));
  }).length;
}
const scores = assignments.map((assignment) => ({ assignment, correct: score(assignment) }));
const survivors = scores.filter(({ correct }) => correct === cases.length);
if (survivors.length !== 1) throw new Error('Teacher evidence did not identify a unique checkpoint');
const [{ assignment }] = survivors;
const write = (path, value) => writeFileSync(path, JSON.stringify(value) + '\n');
write(outputPath, instantiate(assignment));
write(reportPath, { cases: cases.map(({ name }) => name), scores, selected: assignment });
if (counterfactualPath) {
  write(counterfactualPath, instantiate({ ...assignment, splitAtShallowest: !assignment.splitAtShallowest }));
}
console.log(`Selected the unique checkpoint matching ${cases.length} native traces out of ${assignments.length} candidates`);
