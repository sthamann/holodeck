// Model-only candidate generation. Native traces and diagnostics are inputs.
import { readFileSync, writeFileSync } from 'node:fs';
const [directory, kind = 'api', diagnostics] = process.argv.slice(2);
const read = (name) => readFileSync(name, 'utf8');
const output = directory + '/' + kind + '.source.js';
const training = JSON.parse(read(directory + '/training.json'));
const browser = JSON.parse(read(process.env.HOLODECK_NATIVE_BROWSER || directory + '/browser.json'));
const common = `Return plain, syntactically valid JavaScript only. This is a RESTRICTED subset compiled into model parameters, never executed as JS. Top-level declare named functions and route("METHOD","/path",handlerName) only. No imports, globals, classes, spread, mutation, loops, template literals, chaining calls with .method or .length. In functions use const declarations, if with early returns, and return. Conditionals, plain object/array literals, numbers, strings, null, arithmetic + -, comparison, &&, ||, ??, ! and property access a.b or a[key] are supported. Missing properties read as null. Use direct helper calls and ordinary readable variables. Use aliases for mapping/reducing: map(list,(item,index)=>expr), filter(list,(item,index)=>expr), fold(list,initial,(acc,item,index)=>expr), sortBy(list,item=>expr,1 or -1). Arrow callbacks can use a block with const and return. The only builtins are:
values(obj), has(obj,key), withKey(obj,key,value), without(obj,key), length(list/string), unique(list), includes(list,item), lower(str), trim(str), normalize(str,"NFKD"), replace(str,pattern,flags,replacement), match(str,pattern,flags), matches(str,pattern,"gu"), split(str,separator), join(list,separator), concat(list,list), starts(str,prefix), slice(value,start,end=null), time(ms), millis(iso), number(value,fallback), min(a,b), max(a,b), range(n), text(x), type(x), respond(status,body,effects=[],headers={}), fail(status,error), put(collection,key,document), del(collection,key).
match returns captures as array or null. Regex patterns are ordinary JS strings, so escape backslashes. In a route handler the globals state, body, params, query, headers, now are supplied. state collections are plain maps keyed by slug, missing when empty; use state.notes ?? {} and state.tags ?? {}. query fields are arrays. Example:
function handle() {
 const key = params.id;
 const record = (state.articles ?? {})[key];
 if (!record) return fail(404,"absent");
 return respond(200,{id:key,name:record.name});
}
route("GET","/api/articles/:id",handle);
No prose and do not repeat the instructions.`;
const api = `Infer the complete notes workflow from these real native traces (a training fixture family, values are examples). Do not hardcode fixtures. Define all observed routes under /api/v1. The Holo requires named routines projectNote(record,catalog) for full API note with links, sections(content) returning [{title,content}], tagList() returning [{slug,note_count}]. Keep state as notes with {slug,title,content,created_at,updated_at,tags:[slug,...]}, tags as {slug}. GET note resolves wikilinks to existing notes and backlinks sorted by title. Ignore fenced and inline code and tag links. For split, keep heading in content and keep deeper sections nested. Exclude preamble; select shallowest headings outside code, persist all children atomically, return summaries, not full records. Dates are inherited; infer exact slugs, status, tags, error text, and response keys from traces. Normalize wall clock revision with max(now,previous+1ms) and always return quoted etag for GET and PATCH. No-op edit retains revision; tag set is canonical. Make summaries with excerpt from first prose line, stripping markup and headings. Use bounded readable automatic slug collisions. A tag delete detaches from all notes. Implement GET list tag and text filtering plus sorting. Produce complete code in 8500 tokens or fewer.
${JSON.stringify(training.filter((r) => r.workspace === training[0].workspace))}`;
const holo = `Infer the Holo and its interactions from the real browser traces. Return functions prefixed with holo and a single route("GET","/holo",handler). You can call the existing projectNote(record,catalog), sections(content) and tagList() routines. The model outputs a component tree, using exactly this renderer schema: {kind:"workspace",title,home,action,sidebar,main}; stack/row/tags/notelist/linklist/ordered use {kind,children}; heading {kind:"heading",value,level?:2}; text {kind:"text",value}; document {kind:"document",value,inline:[]} renders Markdown and optional inline refs {token,label,href}; link {kind:"link",label,href,tone?:"primary"}; item {kind:"item",label,excerpt,active,href}; form {kind:"form",label,method,path,next,fields,headers?,recovery?}; field {kind:"input"|"textarea"|"checks"|"select",label,name,value,options?:[{label,value}],required?}. Always provide a workspace action link for "New note". Query parameters are arrays: query.view[0] and query.slug[0]. View values index,note,new,edit,split,tags. Build search and filter form (GET /), list sidebar, note detail with clickable links and backlinks, new/edit with editable title, Markdown and tag checkboxes, split with preview from sections() and optional tag select, tag management with create. Attach exact existing HTTP actions. Edit request headers include quoted if-match; success next {prefix:"/?view=note&slug=",field:"slug"}. POST split uses /api/v1/notes/<slug>/split; optional tag "" is allowed. Use visible headings "New note", "Edit note", "Split by headings", "Tags" for clarity. UI is rendered locally, no custom HTML or prose. Keep it concise.
${JSON.stringify(browser.map(({ task, after, requests }) => ({ task, requests,
  elements: after.split('\n').filter((line) => /textbox|dialog|searchbox|tag filter|Edit"|Split|Save|New note|tab "Tags"|heading "[^"]*" \[level=1\]/u.test(line)) })))}
`;
const repair = diagnostics ? `\nRepair this candidate using TRAINING diagnostics only. Return complete code.\n${read(output)}\n${read(diagnostics).slice(0,10000)}` : '';
const response = await fetch('http://127.0.0.1:11434/api/generate', {
  method: 'POST', headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(900000),
  body: JSON.stringify({ model: process.env.HOLODECK_QWEN_GENERATOR || 'qwen3.6:35b',
    prompt: common + '\n' + (kind === 'api' ? api : holo) + repair,
    stream: false, think: false, options: { temperature: 0, num_ctx: 24576, num_predict: kind === 'api' ? 8500 : 4600 } }),
});
if (!response.ok) throw new Error(await response.text());
const data = await response.json();
const program = data.response.trim().replace(/^```(?:javascript|js)?\s*/u, '').replace(/\s*```$/u, '');
if (!program) throw new Error('Qwen returned no candidate program');
writeFileSync(output, program + '\n');
writeFileSync(directory + '/' + kind + '.generation.json', JSON.stringify({
  model: data.model, tokens: data.eval_count, reason: data.done_reason, outputChars: program.length,
}) + '\n');
console.log(`Qwen ${kind}: ${program.length} chars, ${data.eval_count} tokens (${data.done_reason})`);
