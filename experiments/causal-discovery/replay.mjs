import assert from "node:assert/strict";
import { acquire, distinguish, replay } from "./mealy.mjs";

const fixtures = [
  {
    name: "publication gate",
    alphabet: ["edit", "approve", "publish"],
    reference: {
      initial: 0,
      states: [
        { edit: [1, "saved"], approve: [0, "refused"], publish: [0, "refused"] },
        { edit: [1, "saved"], approve: [2, "record:approval"], publish: [1, "refused"] },
        { edit: [1, "saved"], approve: [2, "record:approval"], publish: [0, "record:published"] },
      ],
    },
    horizon: 3,
    expectedStates: 3,
  },
  {
    name: "delayed inventory release",
    alphabet: ["tick", "release"],
    reference: {
      initial: 0,
      states: [
        { tick: [1, "accepted"], release: [0, "refused"] },
        { tick: [2, "accepted"], release: [1, "refused"] },
        { tick: [3, "accepted"], release: [2, "refused"] },
        { tick: [3, "accepted"], release: [0, "record:released"] },
      ],
    },
    horizon: 4,
    expectedStates: 4,
  },
];

for (const { name, alphabet, reference, horizon, expectedStates } of fixtures) {
  let observedQueries = 0;
  const result = acquire(alphabet, (word) => {
    observedQueries += 1;
    return replay(reference, word);
  }, { horizon, maxQueries: 128 });
  assert.equal(result.status, "verified-within-horizon");
  assert.equal(result.checkedDepth, horizon);
  assert.equal(result.machine.states.length, expectedStates);
  assert.equal(result.queries, observedQueries);
  assert.equal(distinguish(alphabet, reference, result.machine), null);
  console.log(JSON.stringify({
    name, states: expectedStates, horizon, queries: result.queries,
    actions: result.actions, exactFiniteParity: true,
  }));
}

const { alphabet, reference } = fixtures[1];
const shallow = acquire(alphabet, (word) => replay(reference, word),
  { horizon: 2, maxQueries: 128 });
assert.equal(shallow.status, "verified-within-horizon");
assert.deepEqual(distinguish(alphabet, reference, shallow.machine), {
  word: ["tick", "tick", "tick", "release"],
  expected: "record:released",
  actual: "refused",
});

const exhausted = acquire(alphabet, (word) => replay(reference, word),
  { horizon: 4, maxQueries: 1 });
assert.equal(exhausted.status, "budget-exhausted");
assert.equal(exhausted.queries, 1);
assert.equal(exhausted.checkedDepth, 0);
assert.throws(() => acquire(alphabet, () => [],
  { horizon: 1, maxQueries: 2 }), /canonical string observation/);
console.log("Bounded parity, a deeper counterexample and budget exhaustion checked.");
