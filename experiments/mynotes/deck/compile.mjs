// Build-time only. The runtime accepts the numerical export, not this authoring representation.
import { writeFileSync } from 'node:fs';
import { operations } from './kernel.mjs';
import { routes, routines } from './parameters.mjs';

const rows = [];
const existing = new Map();
const choices = Object.create(null);
function lower(expression) {
  const { op, args } = expression;
  if (op === 'choice') {
    const [name, values] = args;
    if (!choices[name]) {
      choices[name] = { row: rows.length, values };
      rows.push([operations.indexOf('literal'), values[0]]);
    }
    return choices[name].row;
  }
  const selector = operations.indexOf(op);
  if (selector < 0) throw new Error('Unknown operator ' + op);
  const operands = args.map((arg) => {
    if (arg && typeof arg === 'object' && Object.hasOwn(arg, 'op')) return lower(arg);
    if (op === 'record') return [arg[0], lower(arg[1])];
    return arg;
  });
  const row = [selector, ...operands];
  const key = JSON.stringify(row);
  if (existing.has(key)) return existing.get(key);
  const index = rows.length;
  rows.push(row);
  existing.set(key, index);
  return index;
}
const checkpoint = {
  format: 1,
  routines: Object.fromEntries(Object.entries(routines).map(([key, { inputs, body }]) =>
    [key, { inputs, entry: lower(body) }])),
  routes: routes.map(([method, path, body]) => ({ method, path, entry: lower(body) })),
  parameters: rows,
};
const path = process.argv[2];
if (!path) throw new Error('Usage: node compile.mjs CANDIDATES');
writeFileSync(path, JSON.stringify({ checkpoint, choices }) + '\n');
console.log(`Compiled ${rows.length} sparse parameter rows and ${Object.keys(choices).length} trainable selectors`);
