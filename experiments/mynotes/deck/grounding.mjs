// A generic linear readout over local Qwen features. Weights live in the artifact.
import { readFileSync } from 'node:fs';

export async function embed(model, input) {
  const response = await fetch('http://127.0.0.1:11434/api/embed', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model, input, truncate: false }), signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw new Error('Local Qwen encoder unavailable');
  return (await response.json()).embeddings;
}

export function predict(head, vector) {
  let best = -Infinity;
  let selected;
  for (const { target, weights } of head.rows) {
    if (weights.length !== vector.length) throw new Error('Grounding dimension mismatch');
    const score = weights.reduce((sum, value, index) => sum + value * vector[index], 0);
    if (score > best) { best = score; selected = target; }
  }
  if (selected === undefined) throw new Error('Empty grounding head');
  return selected;
}

export function loadGrounder(path) {
  if (!path) return null;
  const head = JSON.parse(readFileSync(path, 'utf8'));
  return async (request) => predict(head, (await embed(head.model, [request]))[0]);
}
