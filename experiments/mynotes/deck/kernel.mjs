// Stable operator vocabulary. The artifact supplies all control flow and data bindings.
export const operations = [
  'literal', 'ref', 'let', 'if', 'record', 'array', 'call', 'map', 'filter', 'fold', 'sort',
  'get', 'has', 'values', 'with', 'without', 'eq', 'not', 'and', 'or', 'coalesce', 'type',
  'length', 'add', 'sub', 'lt', 'lte', 'min', 'max', 'range', 'slice', 'unique', 'includes',
  'text', 'trim', 'lower', 'normalize', 'replace', 'match', 'matches', 'split', 'join',
  'starts', 'time', 'millis', 'compare', 'number', 'concat', 'keys', 'merge', 'interval', 'sequence',
];

const own = (value, key) => value != null && Object.hasOwn(value, key);
const read = (value, key) => own(value, key) ? value[key] : null;
const equal = (a, b) => Array.isArray(a) && Array.isArray(b)
  ? a.length === b.length && a.every((value, i) => equal(value, b[i]))
  : a === b;

const primitives = {
  get: read, has: own, values: (value) => Object.values(value ?? {}),
  with: (value, key, next) => ({ ...value, [key]: next }),
  without: (value, key) => Object.fromEntries(Object.entries(value).filter(([name]) => name !== key)),
  eq: equal, not: (value) => !value,
  type: (value) => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value,
  length: (value) => typeof value === 'string' ? Array.from(value).length : value.length,
  add: (a, b) => a + b, sub: (a, b) => a - b, lt: (a, b) => a < b, lte: (a, b) => a <= b,
  min: Math.min, max: Math.max, range: (count) => Array.from({ length: Math.min(4096, count) }, (_, i) => i),
  slice: (value, start, end) => typeof value === 'string'
    ? Array.from(value).slice(start, end ?? undefined).join('') : value.slice(start, end ?? undefined),
  unique: (items) => [...new Set(items)], includes: (items, value) => items.includes(value),
  text: String, trim: (value) => value.trim(), lower: (value) => value.toLowerCase(),
  normalize: (value, form) => value.normalize(form),
  replace: (value, pattern, flags, replacement) => value.replace(new RegExp(pattern, flags), replacement),
  match: (value, pattern, flags) => value.match(new RegExp(pattern, flags))?.map((x) => x ?? null) ?? null,
  matches: (value, pattern, flags) => Array.from(value.matchAll(new RegExp(pattern, flags)), (m) => m.map((x) => x ?? null)),
  split: (value, separator) => value.split(separator), join: (items, separator) => items.join(separator),
  starts: (value, prefix) => value.startsWith(prefix), time: (ms) => new Date(ms).toISOString(),
  millis: Date.parse, compare: (a, b) => a === b ? 0 : a < b ? -1 : 1,
  number: (value, fallback) => value !== null && Number.isSafeInteger(Number(value)) ? Number(value) : fallback,
  concat: (a, b) => a.concat(b),
  keys: (value) => Object.keys(value ?? {}),
  merge: (a, b) => ({ ...a, ...b }),
  interval: (start, end) => Array.from({ length: Math.max(0, Math.min(4096, end - start)) }, (_, i) => start + i),
};

function sequenceForward(model, tokens) {
  if (!model) throw new Error('Missing recurrent weights');
  let state = model.initial;
  for (const token of tokens) {
    if (!Number.isInteger(token) || token < 0 || token >= model.inputSize) {
      throw new Error('Recurrent token out of range');
    }
    // The hidden layer combines the current one-hot state and input token.
    const hidden = model.hiddenBias.map((bias, i) => Math.max(0,
      bias + model.stateWeights[i][state] + model.tokenWeights[i][token]));
    let best = -Infinity;
    let next = 0;
    for (let i = 0; i < model.nextWeights.length; i++) {
      const score = model.nextWeights[i].reduce((sum, weight, j) => sum + weight * hidden[j], model.nextBias[i]);
      if (score > best) { best = score; next = i; }
    }
    state = next;
  }
  // Readout heads are trained independently on observed terminal outcomes.
  return [...model.heads.map(({ labels, weights }) => {
    let best = 0;
    for (let i = 1; i < weights.length; i++) if (weights[i][state] > weights[best][state]) best = i;
    return labels[best];
  }), ...model.constants];
}

export function loadForward(checkpoint) {
  if (checkpoint.format !== 1 || !Array.isArray(checkpoint.parameters)) throw new Error('Invalid model format');
  const rows = checkpoint.parameters.map(([selector, ...args]) => {
    const op = operations[selector];
    if (!op) throw new Error('Unknown operator selector');
    return [op, ...args];
  });
  return (entry, input) => {
    let steps = 0;
    function run(pc, env) {
      if (++steps > 2_000_000) throw new Error('Model instruction budget exceeded');
      const row = rows[pc];
      if (!row) throw new Error('Invalid parameter reference');
      const [op, ...args] = row;
      const value = (index) => run(index, env);
      const [a, b, c, d] = args;
      switch (op) {
        case 'literal': return a;
        case 'sequence': return sequenceForward(
          b === undefined ? checkpoint.sequenceModel : read(checkpoint.sequenceBanks, value(b)), value(a));
        case 'ref': return read(env, a);
        case 'let': return run(c, { ...env, [a]: value(b) });
        case 'if': return value(a) ? value(b) : value(c);
        case 'and': return args.every(value);
        case 'or': return args.some(value);
        case 'coalesce': {
          const first = value(a);
          return first ?? value(b);
        }
        case 'record': return Object.fromEntries(args.map(([key, index]) => [key, value(index)]));
        case 'array': return args.map(value);
        case 'call': {
          const routine = read(checkpoint.routines, a);
          if (!routine || routine.inputs.length !== args.length - 1) throw new Error('Invalid model call');
          const bindings = routine.inputs.map((key, i) => [key, value(args[i + 1])]);
          return run(routine.entry, { ...env, ...Object.fromEntries(bindings) });
        }
        case 'map': return value(a).map((item, index) => run(c, { ...env, [b]: item, index }));
        case 'filter': return value(a).filter((item, index) => run(c, { ...env, [b]: item, index }));
        case 'fold': return value(a).reduce((acc, item, index) => run(d, { ...env, acc, [c]: item, index }), value(b));
        case 'sort': return value(a).map((item) => [item, run(c, { ...env, [b]: item })])
          .sort((left, right) => primitives.compare(left[1], right[1]) * value(d)).map(([item]) => item);
        default: return primitives[op](...args.map(value));
      }
    }
    return run(entry, input);
  };
}
