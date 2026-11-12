// Offline authoring of the specialist and Holo parameter graph. All named
// routines below lower to selector rows. None are JavaScript runtime callbacks.
const op = (name, ...args) => ({ op: name, args });
const literal = (value) => op('literal', value);
const choice = (name, ...values) => op('choice', name, values);
const expr = (value) => value?.op ? value : literal(value);
const f = (name, ...args) => op(name, ...args.map(expr));
const ref = (name) => op('ref', name);
const get = (x, k) => f('get', x, k);
const obj = (fields) => op('record', ...Object.entries(fields).map(([k, v]) => [k, expr(v)]));
const arr = (...items) => f('array', ...items);
const call = (name, ...args) => op('call', name, ...args.map(expr));
const bind = (name, value, body) => op('let', name, expr(value), expr(body));
const each = (name, items, variable, body) => op(name, expr(items), variable, expr(body));
const fold = (items, seed, variable, body) => op('fold', expr(items), expr(seed), variable, expr(body));
const sort = (items, variable, by, direction = 1) => op('sort', expr(items), variable, expr(by), expr(direction));
const when = (...args) => f('if', ...args);
const either = (...args) => f('coalesce', ...args);
const eq = (...args) => f('eq', ...args);
const and = (...args) => f('and', ...args);
const or = (...args) => f('or', ...args);
const not = (arg) => f('not', arg);
const len = (arg) => f('length', arg);
const trim = (arg) => f('trim', arg);
const lower = (arg) => f('lower', arg);
const concat = (a, b) => f('concat', a, b);
const text = (...parts) => f('join', arr(...parts), '');
const re = (s, pattern, flags = 'u') => f('match', s, pattern, flags);
const replace = (s, pattern, withText, flags = 'gu') => f('replace', s, pattern, flags, withText);
const allMatches = (s, pattern, flags = 'gu') => f('matches', s, pattern, flags);
const typeIs = (v, name) => eq(f('type', v), name);
const first = (values) => get(values, 0);
const sorted = (values) => sort(values, 'sortKey', ref('sortKey'));
const uniq = (values) => f('unique', values);
const slice = (value, start, end = null) => f('slice', value, start, end);
const many = (list, variable, body) => each('map', list, variable, body);
const keep = (list, variable, body) => each('filter', list, variable, body);
const includes = (...args) => f('includes', ...args);
const has = (...args) => f('has', ...args);
const withKey = (...args) => f('with', ...args);
const notes = either(get(ref('state'), 'notes'), {});
const tags = either(get(ref('state'), 'tags'), {});
const body = ref('body');
const bodyField = (key) => get(body, key);
const pathSlug = get(ref('params'), 'slug');
const query = (key) => first(get(ref('query'), key));
const result = (status, body, effects = [], headers = {}) => obj({ status, body, effects, headers });
const failure = (status, error) => result(status, obj({ error }));
const missing = failure(404, 'not found');
const refuse = (error) => failure(400, error);
const put = (collection, key, value) => obj({ op: 'put', collection, key, value });
const del = (collection, key) => obj({ op: 'delete', collection, key });
const now = f('time', ref('now'));
const keyOf = (record) => get(record, 'slug');
const titleOf = (record) => get(record, 'title');
const tagIds = (record) => get(record, 'tags');
const contentOf = (record) => get(record, 'content');
const etag = (record) => text('"', get(record, 'updated_at'), '"');
const note = (slug, title, content, tags, created_at, updated_at) =>
  obj({ slug, title, content, tags, created_at, updated_at });
const slugPattern = '^[a-z0-9]+(?:-[a-z0-9]+)*$';
const wikiPattern = String.raw`\[\[(#?)([a-z0-9](?:-?[a-z0-9]){0,99})(?:\|([^\]\n]{1,256}))?\]\]`;
const headPattern = String.raw`^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$`;
const routines = Object.create(null);
const routine = (name, inputs, build) => {
  routines[name] = { inputs, body: build(...inputs.map(ref)) };
  return (...args) => call(name, ...args);
};

const validSlug = routine('validSlug', ['key'], (s) =>
  and(typeIs(s, 'string'), f('lte', len(s), 100), re(s, slugPattern)));
const slugBase = routine('slugBase', ['title'], (title) =>
  bind('base', replace(replace(replace(replace(f('normalize', lower(title), 'NFKD'), '\\p{Mn}', ''), '[^\\x00-\\x7f]', ''), '[^a-z0-9]+', '-'), '^-|-$', ''),
    either(when(eq(ref('base'), ''), null, replace(slice(ref('base'), 0, 100), '-$', '')), 'note')));
const allocate = routine('allocate', ['base', 'catalog'], (base, catalog) =>
  first(keep(many(f('range', 1024), 'slot',
    when(eq(ref('slot'), 0), base, bind('suffix', text('-', f('add', ref('slot'), choice('slugSuffixOffset', 0, 1))),
      text(replace(slice(base, 0, f('sub', 100, len(ref('suffix')))), '-$', ''), ref('suffix'))))),
  'candidate', not(has(catalog, ref('candidate'))))));
const titleError = routine('titleError', ['value'], (v) =>
  when(not(typeIs(v, 'string')), 'title is required',
    when(eq(trim(v), ''), 'title is required',
      when(re(trim(v), '\\p{Cc}'), 'title must not contain control characters',
        when(f('lt', 200, len(trim(v))), 'title is too long', null)))));
const contentError = routine('contentError', ['value'], (v) =>
  when(not(typeIs(v, 'string')), 'content must be text',
    when(f('lt', 1_000_000, len(v)), 'content is too long', null)));
const tagError = routine('tagError', ['chosen', 'catalog'], (chosen, catalog) =>
  when(not(typeIs(chosen, 'array')), 'tags must be an array',
    when(f('lt', 50, len(chosen)), 'at most 50 tags are supported',
      bind('bad', keep(chosen, 'tag', not(validSlug(ref('tag')))),
        when(f('lt', 0, len(ref('bad'))), 'invalid tag slug',
          bind('unknown', first(keep(chosen, 'tag', not(has(catalog, ref('tag'))))),
            when(ref('unknown'), text('unknown tag: ', ref('unknown')), null)))))));
const noteError = routine('noteError', ['title', 'content', 'chosen', 'catalog'], (title, content, chosen, catalog) =>
  either(titleError(title), either(contentError(content), tagError(chosen, catalog))));

const scanLines = routine('scanLines', ['lines'], (lines) =>
  fold(lines, obj({ fence: '', visible: [], headings: [] }), 'line',
    bind('opening', get(re(ref('line'), '^(`{3,}|~{3,})'), 1),
      bind('fence', get(ref('acc'), 'fence'),
        when(not(eq(ref('fence'), '')),
          when(and(ref('opening'), eq(get(ref('opening'), 0), get(ref('fence'), 0)),
            f('lte', len(ref('fence')), len(ref('opening'))),
            eq(trim(slice(ref('line'), len(ref('opening')))), '')),
          withKey(ref('acc'), 'fence', ''), ref('acc')),
          when(ref('opening'), withKey(ref('acc'), 'fence', ref('opening')),
            bind('heading', re(ref('line'), headPattern),
              obj({
                fence: '', visible: concat(get(ref('acc'), 'visible'), arr(ref('line'))),
                headings: when(ref('heading'), concat(get(ref('acc'), 'headings'),
                  arr(obj({ line: ref('index'), level: len(get(ref('heading'), 1)), title: trim(get(ref('heading'), 2)) }))),
                get(ref('acc'), 'headings')),
              }))))))));
const stripInlineCode = (s) => replace(s, '(`+)[^\\n]*?\\1', '');
const wikiHits = routine('wikiHits', ['record'], (record) =>
  bind('visible', get(scanLines(f('split', contentOf(record), '\n')), 'visible'),
    keep(allMatches(stripInlineCode(f('join', ref('visible'), '\n')), '(?<!\\\\)' + wikiPattern),
      'hit', and(eq(get(ref('hit'), 1), ''), not(eq(get(ref('hit'), 2), keyOf(record)))))));
const linksFrom = routine('linksFrom', ['record'], (record) =>
  uniq(many(wikiHits(record), 'hit', get(ref('hit'), 2))));
const edge = (record) => obj({ slug: keyOf(record), title: titleOf(record) });
const orderByTitle = (items) => sort(items, 'item', lower(titleOf(ref('item'))));
const tagsFor = (ids) => many(ids, 'id', obj({ slug: ref('id') }));
const document = routine('document', ['record', 'catalog'], (r, catalog) =>
  obj({
    slug: keyOf(r), title: titleOf(r), content: contentOf(r),
    created_at: get(r, 'created_at'), updated_at: get(r, 'updated_at'), tags: tagsFor(tagIds(r)),
    incoming_links: many(orderByTitle(keep(f('values', catalog), 'item', includes(linksFrom(ref('item')), keyOf(r)))), 'item', edge(ref('item'))),
    outgoing_links: many(orderByTitle(many(keep(linksFrom(r), 'id', has(catalog, ref('id'))),
      'id', get(catalog, ref('id')))), 'item', edge(ref('item'))),
  }));
const excerpt = routine('excerpt', ['content'], (s) =>
  bind('line', either(first(keep(many(get(scanLines(f('split', s, '\n')), 'visible'), 'line', trim(ref('line'))),
    'line', and(not(eq(ref('line'), '')), not(f('starts', ref('line'), '#')), not(re(ref('line'), '^(?:[-*_]\\s*){3,}$'))))), ''),
  bind('linked', replace(replace(ref('line'), '\\[\\[#?[a-z0-9-]+\\|([^\\]\\n]+)\\]\\]', '$1'), wikiPattern, '$1$2'),
    slice(trim(replace(replace(replace(ref('linked'), '^(?:>\\s*|[-*+]\\s+|[0-9]+[.)]\\s+)+', ''),
      '\\[([^\\]]*)\\]\\([^)]*\\)', '$1'), '[*`~]|__', '')), 0, 160))));
const summary = routine('summary', ['record', 'catalog', 'snippet', 'includeLinks'], (r, catalog, snippet, links) =>
  bind('out', f('without', when(links, document(r, catalog),
    obj({ slug: keyOf(r), title: titleOf(r), created_at: get(r, 'created_at'), updated_at: get(r, 'updated_at'),
      tags: tagsFor(tagIds(r)), incoming_links: [], outgoing_links: [] })), 'content'),
  withKey(ref('out'), 'excerpt', either(snippet, excerpt(contentOf(r))))));
const recordResponse = (status, r, effects = [], catalog = notes) =>
  result(status, document(r, catalog), effects, obj({ etag: etag(r) }));
const save = (status, r) => recordResponse(status, r, arr(put('notes', keyOf(r), r)), withKey(notes, keyOf(r), r));
const invalidIf = (error, body) => bind('error', error, when(ref('error'), refuse(ref('error')), body));
const create = routine('create', ['title', 'content', 'requestedSlug', 'chosen'], (title, content, requested, chosen) =>
  invalidIf(noteError(title, content, chosen, tags),
    when(and(not(eq(requested, null)), not(validSlug(requested))), refuse('invalid slug'),
      when(and(requested, has(notes, requested)), failure(409, 'slug already exists'),
        bind('slug', either(requested, allocate(slugBase(trim(title)), notes)),
          when(ref('slug'), bind('record', note(ref('slug'), trim(title), content, sorted(uniq(chosen)), now, now),
            save(201, ref('record'))), failure(500, 'could not allocate a unique slug')))))));
const withExisting = (body) => bind('existing', get(notes, pathSlug), when(ref('existing'), body, missing));
const existing = ref('existing');
const patch = withExisting(
  when(not(or(has(body, 'title'), has(body, 'content'), has(body, 'tags'))), refuse('no fields to update'),
    bind('title', when(has(body, 'title'), bodyField('title'), titleOf(existing)),
      bind('content', when(has(body, 'content'), bodyField('content'), contentOf(existing)),
        bind('chosen', when(has(body, 'tags'), bodyField('tags'), tagIds(existing)),
          invalidIf(noteError(ref('title'), ref('content'), ref('chosen'), tags),
            bind('provided', get(ref('headers'), 'if-match'),
              when(and(choice('rejectStaleRevision', false, true), not(eq(ref('provided'), null)),
                not(eq(ref('provided'), etag(existing)))),
                failure(412, 'version mismatch'),
                bind('canonical', sorted(uniq(ref('chosen'))),
                  when(and(eq(trim(ref('title')), titleOf(existing)), eq(ref('content'), contentOf(existing)),
                    eq(ref('canonical'), tagIds(existing))), recordResponse(200, existing),
                  bind('record', note(pathSlug, trim(ref('title')), ref('content'), ref('canonical'),
                    get(existing, 'created_at'), f('time', f('max', ref('now'), f('add', f('millis', get(existing, 'updated_at')), 1)))),
                  save(200, ref('record')))))))))))));

const sections = routine('sections', ['content'], (content) =>
  bind('lines', f('split', content, '\n'),
    bind('headings', get(scanLines(ref('lines')), 'headings'),
      bind('level', fold(ref('headings'), either(get(first(ref('headings')), 'level'), 0), 'h',
        when(choice('splitAtShallowest', false, true),
          f('min', ref('acc'), get(ref('h'), 'level')), f('max', ref('acc'), get(ref('h'), 'level')))),
        bind('bounds', keep(ref('headings'), 'h', eq(get(ref('h'), 'level'), ref('level'))),
          many(ref('bounds'), 'h', obj({ title: titleOf(ref('h')),
            content: replace(f('join', slice(ref('lines'), get(ref('h'), 'line'),
              get(get(ref('bounds'), f('add', ref('index'), 1)), 'line')), '\n'), '\\n+$', ''),
          })))))));
const clippedTitle = (value) => when(f('lt', 200, len(value)), text(slice(value, 0, 199), '…'), value);
const splitNote = withExisting(
  bind('chosen', when(bodyField('tag'), arr(bodyField('tag')), []),
    invalidIf(tagError(ref('chosen'), tags),
      bind('sections', sections(contentOf(existing)),
        when(eq(len(ref('sections')), 0), refuse('note has no headings to split on'),
          when(f('lt', 200, len(ref('sections'))), refuse('note has more than 200 sections'),
            bind('allocation', fold(ref('sections'), obj({ catalog: notes, created: [], failed: false }), 'section',
              bind('title', clippedTitle(titleOf(ref('section'))),
                bind('slug', allocate(slugBase(ref('title')), get(ref('acc'), 'catalog')),
                  when(not(ref('slug')), withKey(ref('acc'), 'failed', true),
                    bind('record', note(ref('slug'), trim(ref('title')), contentOf(ref('section')), ref('chosen'),
                      get(existing, 'created_at'), get(existing, 'updated_at')),
                    obj({ catalog: withKey(get(ref('acc'), 'catalog'), ref('slug'), ref('record')),
                      created: concat(get(ref('acc'), 'created'), arr(ref('record'))), failed: get(ref('acc'), 'failed') })))))),
            when(get(ref('allocation'), 'failed'), failure(500, 'could not allocate unique split slugs'),
              bind('children', get(ref('allocation'), 'created'),
                result(201, obj({ notes: many(ref('children'), 'child', summary(ref('child'), notes, null, false)) }),
                many(ref('children'), 'child', put('notes', keyOf(ref('child')), ref('child')))))))))))));

const allTags = routine('allTags', [], () =>
  many(sorted(many(f('values', tags), 'tag', keyOf(ref('tag')))), 'id',
    obj({ slug: ref('id'), note_count: len(keep(f('values', notes), 'item', includes(tagIds(ref('item')), ref('id')))) })));
const newTag = when(not(validSlug(bodyField('slug'))), refuse('invalid slug'),
  when(has(tags, bodyField('slug')), failure(409, 'slug already exists'),
    bind('tag', obj({ slug: bodyField('slug') }), result(201, ref('tag'), arr(put('tags', bodyField('slug'), ref('tag')))))));
const deleteTag = when(has(tags, pathSlug), result(204, null,
  concat(arr(del('tags', pathSlug)), many(keep(f('values', notes), 'r', includes(tagIds(ref('r')), pathSlug)), 'r',
    put('notes', keyOf(ref('r')), withKey(ref('r'), 'tags', keep(tagIds(ref('r')), 'tag', not(eq(ref('tag'), pathSlug)))))))),
missing);
const queryTerms = (q) => many(allMatches(lower(q), '[\\p{L}\\p{N}]+'), 'token', first(ref('token')));
const listing = routine('listing', ['search', 'filterTags', 'titleOnly', 'sortBy', 'order', 'offset', 'limit'],
  (search, filterTags, titleOnly, sortBy, order, offset, limit) =>
    bind('terms', queryTerms(search),
      bind('filtered', keep(f('values', notes), 'item',
        and(eq(len(keep(filterTags, 'tag', not(includes(tagIds(ref('item')), ref('tag'))))), 0),
          when(eq(search, ''), true,
            when(titleOnly, f('starts', lower(titleOf(ref('item'))), lower(search)),
              and(f('lt', 0, len(ref('terms'))),
                eq(len(keep(ref('terms'), 'term', not(includes(
                  queryTerms(text(titleOf(ref('item')), ' ', contentOf(ref('item')))), ref('term'))))), 0)))))),
        bind('ordered', sort(ref('filtered'), 'item',
          when(or(titleOnly, eq(sortBy, 'title'), not(eq(search, ''))), lower(titleOf(ref('item'))),
            get(ref('item'), when(eq(sortBy, 'created'), 'created_at', 'updated_at'))),
          when(or(titleOnly, not(eq(search, '')), eq(order, 'asc')), 1, -1)),
        bind('page', slice(ref('ordered'), offset, f('add', offset, limit)),
          obj({ total: len(ref('page')), notes: many(ref('page'), 'item',
            summary(ref('item'), notes, when(and(not(titleOnly), not(eq(search, ''))),
              fold(ref('terms'), contentOf(ref('item')), 'term', replace(ref('acc'),
                text('(?<![\\p{L}\\p{N}])(', ref('term'), ')(?![\\p{L}\\p{N}])'), '\u0002$1\u0003', 'giu')), null), true)) }))))));
const search = either(query('q'), '');
const activeTags = either(get(ref('query'), 'tag'), []);
const list = listing(search, activeTags, eq(query('titlePrefix'), 'true'), either(query('sort'), 'updated'),
  either(query('order'), 'desc'), f('max', 0, f('number', query('offset'), 0)),
  f('min', 200, f('max', 1, f('number', query('limit'), 50))));

const apiPath = (slug) => text('/api/v1/notes/', slug);
const locationOf = (view, slug) => text('/?view=', view, '&slug=', slug);
const node = (kind, data) => obj({ kind, ...data });
const title = (value) => node('heading', { value });
const heading = (value) => node('heading', { value, level: 2 });
const paragraph = (value) => node('text', { value });
const stack = (...children) => node('stack', { children: arr(...children) });
const link = (label, href, tone = null) => node('link', { label, href, tone });
const row = (...children) => node('row', { children: arr(...children) });
const field = (kind, name, label, value, data = {}) => node(kind, { name, label, value, ...data });
const buttonForm = (label, method, path, next, fields, data = {}) =>
  node('form', { label, method, path, next, fields, ...data });
const option = (label, value) => obj({ label, value });
const choices = many(allTags(), 'tag', option(keyOf(ref('tag')), keyOf(ref('tag'))));
const navAfterSave = obj({ prefix: '/?view=note&slug=', field: 'slug' });
const tagField = (value) => field('checks', 'tags', 'Tags', value, { options: choices });
const editorFields = (values) => arr(
  field('input', 'title', 'Title', either(titleOf(values), ''), { required: true, limit: 200 }),
  field('textarea', 'content', 'Markdown', either(contentOf(values), ''), { rows: 13 }),
  tagField(either(tagIds(values), [])),
);
const editor = (editing) => stack(
  link(editing ? 'Back to note' : 'All notes', editing ? locationOf('note', pathSlug) : '/'),
  title(editing ? 'Edit note' : 'New note'),
  buttonForm(editing ? 'Save changes' : 'Create note', editing ? 'PATCH' : 'POST',
    editing ? apiPath(pathSlug) : '/api/v1/notes', navAfterSave, editorFields(editing ? existing : null),
    editing ? {
      headers: obj({ 'if-match': etag(existing) }),
      recovery: obj({ status: 412, label: 'Reload current version', href: locationOf('edit', pathSlug) }),
    } : {}),
);
const linkList = (label, links) => when(eq(len(links), 0), null,
  stack(heading(label), node('linklist', { children: many(links, 'edge',
    link(titleOf(ref('edge')), locationOf('note', keyOf(ref('edge'))))) })));
const modelNoteView = stack(
  title(titleOf(existing)),
  row(link('Edit note', locationOf('edit', pathSlug), 'primary'),
    when(f('lt', 0, len(sections(contentOf(existing)))), link('Split by headings', locationOf('split', pathSlug)), null),
    link('Delete note', locationOf('delete', pathSlug))),
  node('tags', { children: many(tagIds(existing), 'tag', link(ref('tag'), text('/?tag=', ref('tag')))) }),
  node('document', {
    value: contentOf(existing),
    inline: many(keep(wikiHits(existing), 'hit', has(notes, get(ref('hit'), 2))), 'hit',
      obj({ token: first(ref('hit')), label: either(get(ref('hit'), 3), get(ref('hit'), 2)),
        href: locationOf('note', get(ref('hit'), 2)) })),
  }),
  linkList('Links', get(ref('document'), 'outgoing_links')),
  linkList('Linked from', get(ref('document'), 'incoming_links')),
);
const splitView = bind('preview', sections(contentOf(existing)),
  stack(link('Back to note', locationOf('note', pathSlug)),
    title('Split by headings'), paragraph(text('Create notes from “', titleOf(existing), '”. The source stays in place.')),
    when(eq(len(ref('preview')), 0), paragraph('This note has no headings to split on.'),
      stack(node('ordered', { children: many(ref('preview'), 'section', paragraph(titleOf(ref('section')))) }),
        buttonForm('Create split notes', 'POST', text(apiPath(pathSlug), '/split'), locationOf('note', pathSlug),
          arr(field('select', 'tag', 'Tag the new notes', '', { options: concat(arr(option('No tag', '')), choices) })))))));
const manageTags = stack(title('Tags'),
  buttonForm('Create tag', 'POST', '/api/v1/tags', '/?view=tags',
    arr(field('input', 'slug', 'Tag name', '', { required: true, limit: 100 }))),
  node('linklist', { children: many(allTags(), 'tag', row(
    link(text(keyOf(ref('tag')), ' · ', get(ref('tag'), 'note_count')), text('/?tag=', keyOf(ref('tag')))),
    link(text('Delete ', keyOf(ref('tag'))), locationOf('delete-tag', keyOf(ref('tag')))))) }));
const selected = either(query('view'), 'index');
const focal = query('slug');
const scope = when(focal, get(notes, focal), null);
const deleteView = (name, path, back, label, explanation) =>
  stack(link('Cancel', back), title(name), paragraph(explanation),
    buttonForm(label, 'DELETE', path, back, [], { tone: 'danger' }));
const deleteTagView = when(has(tags, focal),
  deleteView(text('Delete ', focal), text('/api/v1/tags/', focal), '/?view=tags',
    'Delete tag', 'The tag will be removed from all notes. The notes stay in place.'),
  stack(title('Tag not found'), link('All tags', '/?view=tags')));
const deleteNoteView = deleteView(text('Delete “', titleOf(existing), '”'), apiPath(pathSlug), '/',
  'Delete note', 'This removes the note from the workspace.');
const workspaceList = listing(search, activeTags, false, 'updated', 'desc', 0, 80);
const sidebar = stack(
  row(link('Notes', '/'), link('Tags', '/?view=tags')),
  buttonForm('Search', 'GET', '/', null,
    arr(field('input', 'q', 'Find notes', search, { placeholder: 'Title or content' }),
      field('select', 'tag', 'Filter by tag', either(first(activeTags), ''), { options: concat(arr(option('All tags', '')), choices) }))),
  node('notelist', { children: many(get(workspaceList, 'notes'), 'item',
    node('item', { label: titleOf(ref('item')), excerpt: get(ref('item'), 'excerpt'),
      active: eq(focal, keyOf(ref('item'))), href: locationOf('note', keyOf(ref('item'))) })) }),
  when(eq(get(workspaceList, 'total'), 0), paragraph('No matching notes.'), null),
);
const main = bind('existing', scope,
  when(eq(selected, 'new'), editor(false),
    when(eq(selected, 'tags'), manageTags,
      when(eq(selected, 'delete-tag'), deleteTagView,
        when(existing, bind('params', obj({ slug: focal }),
          when(eq(selected, 'edit'), editor(true),
            when(eq(selected, 'split'), splitView,
              when(eq(selected, 'delete'), deleteNoteView, bind('document', document(existing, notes), modelNoteView))))),
        when(focal, stack(title('Note not found'), link('All notes', '/')),
          stack(title('Notes'), paragraph('Open a note from the list or create a new one.'),
            link('New note', '/?view=new', 'primary'))))))));
const holo = result(200, node('workspace', {
  title: 'Notes', home: '/', action: link('New note', '/?view=new', 'primary'),
  sidebar, main,
}));

const importMarkdown = when(and(eq(ref('mediaType'), 'text/markdown'), typeIs(body, 'string')),
  bind('firstHeading', first(get(scanLines(f('split', body, '\n')), 'headings')),
    create(titleOf(ref('firstHeading')), body, null, [])),
  failure(415, 'expected text/markdown'));
const download = withExisting(
  result(200, text('---\ntitle: ', titleOf(existing), '\nslug: ', pathSlug, '\ndate: "',
    get(existing, 'created_at'), '"\n',
    when(f('lt', 0, len(tagIds(existing))),
      text('tags:\n', f('join', many(tagIds(existing), 'tag', text('    - ', ref('tag'), '\n')), '')), ''),
    'dialect: mynotes\n---\n', contentOf(existing)), [],
  obj({ 'content-type': 'text/markdown; charset=utf-8', 'content-disposition': text('attachment; filename="', pathSlug, '.md"') })));

const routes = [
  ['GET', '/api/v1/notes', result(200, list)],
  ['POST', '/api/v1/notes', create(bodyField('title'), either(bodyField('content'), ''), bodyField('slug'), either(bodyField('tags'), []))],
  ['GET', '/api/v1/notes/:slug', withExisting(recordResponse(200, existing))],
  ['PATCH', '/api/v1/notes/:slug', patch],
  ['DELETE', '/api/v1/notes/:slug', withExisting(result(204, null, arr(del('notes', pathSlug))))],
  ['POST', '/api/v1/notes/:slug/split', splitNote],
  ['GET', '/api/v1/notes/:slug/download-markdown', download],
  ['POST', '/api/v1/import', importMarkdown],
  ['GET', '/api/v1/tags', result(200, obj({ tags: allTags() }))],
  ['POST', '/api/v1/tags', newTag],
  ['DELETE', '/api/v1/tags/:slug', deleteTag],
  ['GET', '/holo', holo],
];
export { routines, routes };
