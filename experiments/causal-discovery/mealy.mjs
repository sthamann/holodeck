// Finite deterministic Mealy machines. The oracle exposes reset and a word query.
class BudgetExhausted extends Error {}

export function replay(machine, word) {
  let state = machine.initial;
  return word.map((action) => {
    const [target, output] = machine.states[state][action];
    state = target;
    return output;
  });
}

function* words(alphabet, depth, prefix = []) {
  if (depth === 0) {
    yield prefix;
    return;
  }
  for (const action of alphabet) {
    yield* words(alphabet, depth - 1, [...prefix, action]);
  }
}

export function acquire(alphabet, observe, { horizon, maxQueries }) {
  if (!alphabet.length || new Set(alphabet).size !== alphabet.length ||
      !alphabet.every((action) => typeof action === "string") ||
      !Number.isSafeInteger(horizon) || horizon < 1 ||
      !Number.isSafeInteger(maxQueries) || maxQueries < 1) {
    throw new TypeError("Expected a unique string alphabet and positive integer bounds");
  }

  const cache = new Map();
  let queries = 0;
  let actions = 0;
  const query = (word) => {
    const key = JSON.stringify(word);
    if (!cache.has(key)) {
      if (queries >= maxQueries) throw new BudgetExhausted();
      queries += 1;
      actions += word.length;
      const outputs = observe([...word]);
      if (!Array.isArray(outputs) || outputs.length !== word.length ||
          !outputs.every((output) => typeof output === "string")) {
        throw new TypeError("Expected one canonical string observation per action");
      }
      cache.set(key, [...outputs]);
    }
    return cache.get(key);
  };

  const prefixes = new Map([["[]", []]]);
  const suffixes = alphabet.map((action) => [action]);
  const row = (prefix) => JSON.stringify(suffixes.map((suffix) =>
    query([...prefix, ...suffix]).slice(prefix.length)));
  const tally = () => ({ queries, actions });
  let candidate = null;
  let checkedDepth = 0;

  try {
    for (;;) {
      const representatives = new Map();
      let refine = false;
      for (const prefix of prefixes.values()) {
        const key = row(prefix);
        if (!representatives.has(key)) {
          representatives.set(key, prefix);
          continue;
        }
        const representative = representatives.get(key);
        for (const action of alphabet) {
          const left = [...prefix, action];
          const right = [...representative, action];
          if (row(left) === row(right)) continue;
          const witness = suffixes.find((suffix) =>
            JSON.stringify(query([...left, ...suffix]).slice(left.length)) !==
            JSON.stringify(query([...right, ...suffix]).slice(right.length)));
          suffixes.push([action, ...witness]);
          refine = true;
          break;
        }
        if (refine) break;
      }
      if (refine) continue;

      for (const prefix of representatives.values()) {
        for (const action of alphabet) {
          const next = [...prefix, action];
          if (representatives.has(row(next))) continue;
          prefixes.set(JSON.stringify(next), next);
          refine = true;
          break;
        }
        if (refine) break;
      }
      if (refine) continue;

      const ids = new Map([...representatives.keys()].map((key, index) => [key, index]));
      candidate = {
        initial: 0,
        states: [...representatives.values()].map((prefix) => Object.fromEntries(
          alphabet.map((action) => {
            const word = [...prefix, action];
            return [action, [ids.get(row(word)), query(word).at(-1)]];
          }),
        )),
      };

      // Bounded conformance uses the same black box and budget as acquisition.
      checkedDepth = 0;
      let counterexample;
      for (let depth = 1; depth <= horizon; depth += 1) {
        for (const word of words(alphabet, depth)) {
          const expected = query(word);
          if (replay(candidate, word).some((output, index) => output !== expected[index])) {
            counterexample = word;
            break;
          }
        }
        if (counterexample) break;
        checkedDepth = depth;
      }
      if (!counterexample) {
        return { status: "verified-within-horizon", machine: candidate, checkedDepth, ...tally() };
      }
      candidate = null;
      checkedDepth = 0;
      for (let length = 1; length <= counterexample.length; length += 1) {
        const prefix = counterexample.slice(0, length);
        prefixes.set(JSON.stringify(prefix), prefix);
      }
    }
  } catch (error) {
    if (!(error instanceof BudgetExhausted)) throw error;
    return { status: "budget-exhausted", machine: candidate, checkedDepth, ...tally() };
  }
}

// Evaluation has privileged access to both finite machines. Its output stays
// outside acquisition. A breadth-first product search returns a shortest witness.
export function distinguish(alphabet, reference, candidate) {
  const queue = [[reference.initial, candidate.initial, []]];
  const visited = new Set([JSON.stringify(queue[0].slice(0, 2))]);
  for (let index = 0; index < queue.length; index += 1) {
    const [left, right, prefix] = queue[index];
    for (const action of alphabet) {
      const [leftTarget, expected] = reference.states[left][action];
      const [rightTarget, actual] = candidate.states[right][action];
      const word = [...prefix, action];
      if (expected !== actual) return { word, expected, actual };
      const pair = JSON.stringify([leftTarget, rightTarget]);
      if (visited.has(pair)) continue;
      visited.add(pair);
      queue.push([leftTarget, rightTarget, word]);
    }
  }
  return null;
}
