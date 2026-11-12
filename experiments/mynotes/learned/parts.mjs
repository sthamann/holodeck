import { readFileSync, writeFileSync } from 'node:fs';
const [lab, part, diagnostics] = process.argv.slice(2);
const read = (path) => readFileSync(path, 'utf8');
const all = JSON.parse(read(lab + '/training.json'));
const one = all.filter(({ workspace }) => workspace === all[0].workspace);
const labels = {
  links: ['new note', 'backlinks', 'retained source', 'filter notes'],
  sections: ['split shallow headings', 'read child', 'unknown split tag', 'no headings'],
  writes: ['new note', 'duplicate explicit slug', 'derive and disambiguate slug', 'edit text and tags', 'stale edit', 'no-op tag order', 'bad tag', 'delete note'],
  reads: ['tag created', 'duplicate tag', 'open', 'filter notes', 'tag counts', 'delete tag', 'missing note'],
};
const tasks = {
  links: `Define projectNote(record, catalog), where catalog is directly the slug-keyed notes map. Return the API fields slug,title,content,created_at,updated_at,tags (object array), incoming_links,outgoing_links. Wikilinks use [[slug]] and [[slug|label]], but the returned link title is the referenced note title. Match with matches(content,pattern,"gu"), not match(). Ignore fenced code, inline code, tags, dangling and self links. Return slug/title objects only for each link and order each array by lower(title). Also define excerpt(content) returning first nonempty prose line with headings excluded and markup removed. See list response and content example. You can define supporting functions with unique names. No routes.`,
  sections: `Define sections(content) and a POST route /api/v1/notes/:slug/split. The sections function returns [{title,content}]. Use a fold over lines with accumulator {fence,headings} to identify headings outside code, storing their line index and level. Next select the shallowest level and slice the original lines from each boundary to the next, keeping nested headings and fenced content intact. Trim trailing newlines, drop preamble. Route: missing source 404; validate optional tag against state.tags; a missing tag or empty string means no tags. No headings returns 400. Allocate every child slug by calling freeSlug(section.title, catalog) then use a fold to reserve child slugs before later children. Store all children in one respond(201,{notes:summaries},effects). Keep source unchanged and inherit both dates. API split summaries have no content, include excerpt(child.content), tags [{slug}], and empty incoming_links and outgoing_links. freeSlug(title,catalog) and excerpt(content) are already defined; call them only. Use the exact errors shown in the traces.`,
  writes: `Define freeSlug(title,catalog) and POST /api/v1/notes, PATCH /api/v1/notes/:slug and DELETE /api/v1/notes/:slug. Each write returns its effects as third argument to respond; calling put or del alone has no effect. Store {slug,title,content,created_at,updated_at,tags:[slug,...]}. Use state.notes ?? {} and state.tags ?? {}. The existing projectNote(record,catalog) accepts the record and the slug-keyed notes map, so pass a map with the new record inserted. When no explicit slug, derive title slug, try it, then -2,-3,... from range() with map and filter (no while). Explicit collision 409. Trim title, canonicalize tag set with sortBy(unique(tags), tag=>tag,1). Unknown tag error includes slug. For PATCH check optional quoted if-match; unchanged fields emit [] and preserve revision; actual changes use time(max(now,millis(old.updated_at)+1)). Always return quoted ETag on PATCH. Delete returns 204 and an effect. Do not redeclare projectNote. Match observed payloads and statuses.`,
  reads: `Define tagList() (returns [{slug,note_count}] sorted by slug) and GET /api/v1/tags, POST /api/v1/tags, DELETE /api/v1/tags/:slug, GET /api/v1/notes, GET /api/v1/notes/:slug. State is state.tags (slug-keyed map of {slug}) and state.notes (slug-keyed map of note records with tags string array), with missing collection represented by null. On tag delete return effects removing tag and updating all member notes, no revision change. put and del construct effects and must be inside respond's effects array. GET note returns quoted ETag. For list apply all query.tag values, optional text search and title prefix, sort, offset/limit, total equals returned notes count. Return each summary as the projectNote result with content removed and excerpt added. Use existing functions projectNote(record,catalog) and excerpt(content). Catalog is state.notes ?? {}. Read observed result keys, statuses and error strings.`,
};
const common = `Output only valid JavaScript functions and route declarations for a tiny exact compiler. Use const variables, if with return, return, ternary expressions, comparisons, + -, ??, &&, ||, !, ordinary object/array literals and a.b or a[key]. No loops, mutation, spread, regex literals, assignments, methods, or imports. Every operation must use these direct helper functions:
values(obj), has(obj,key), withKey(obj,key,value), without(obj,key), length(x), unique(list), includes(list,item), lower(s), trim(s), normalize(s,"NFKD"), replace(s,"regex","gu","replacement"), match(s,"regex","u"), matches(s,"regex","gu"), split(s,separator), join(xs,separator), concat(xs,ys), starts(s,prefix), slice(x,start,end=null), time(ms), millis(iso), number(x,fallback), min(a,b), max(a,b), range(n), text(x), type(x).
map(xs,(v,i)=>expr), filter(xs,(v,i)=>expr), fold(xs,initial,(acc,v,i)=>expr), sortBy(xs,v=>expr,1 or -1). Arrow callbacks can have const and return blocks. Missing object properties yield null. Regex patterns are ordinary strings (escape backslashes). Route context names state, body, params, query, headers, now are supplied. Query values are arrays, now is a number. HTTP: respond(status,body,effects=[],headers={}), fail(status,message), put(collection,key,record), del(collection,key). Route registration example:
function show() { const r=(state.items ?? {})[params.id]; if (!r) return fail(404,"not found"); return respond(200,r); }
route("GET","/api/v1/items/:id",show);
Do not echo these instructions.`;
const file = lab + '/' + part + '.js';
const extra = diagnostics ? `\nRepair the following source using the training failures. Keep valid behavior. Output full replacement.\n${read(file)}\n${read(diagnostics).slice(0,7000)}` : '';
if (!tasks[part]) throw new Error('Unknown synthesis partition');
const response = await fetch('http://127.0.0.1:11434/api/generate', {
  method: 'POST', headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(600000),
  body: JSON.stringify({ model: process.env.HOLODECK_QWEN_GENERATOR || 'qwen3.6:35b', think: false, stream: false,
    prompt: common + '\nTASK:\n' + tasks[part] + '\nNATIVE TRAINING OBSERVATIONS:\n' +
      JSON.stringify(one.filter((row) => labels[part].includes(row.label))) + extra,
    options: { temperature: 0, num_ctx: 12288, num_predict: 3700 } }),
});
if (!response.ok) throw new Error(await response.text());
const data = await response.json();
const source = data.response.trim().replace(/^```(?:javascript|js)?\s*/u,'').replace(/\s*```$/u,'');
if (!source) throw new Error('No source generated');
writeFileSync(file, source + '\n');
writeFileSync(lab + '/' + part + '.generation.json', JSON.stringify({ model: data.model,
  tokens: data.eval_count, reason: data.done_reason }) + '\n');
console.log(`${part}: ${source.length} characters`);
