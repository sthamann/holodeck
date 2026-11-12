const extractLinks = (content) => {
  const pattern = "\\[\\[([a-zA-Z0-9-]+)(?:\\|([^\\]]+))?\\]\\]";
  const matches = matches(content, pattern, "gu");
  return map(matches, (match) => {
    const slug = match[1];
    const label = match[2] ?? slug;
    return { slug, label };
  });
};

const filterValidLinks = (links, catalog) => {
  return filter(links, (link) => has(catalog, link.slug) && link.slug !== record.slug);
};

const resolveLinkTitles = (links, catalog) => {
  return map(links, (link) => {
    const note = catalog[link.slug];
    const title = note ? note.title : link.label;
    return { slug: link.slug, title };
  });
};

const uniqueLinks = (links) => {
  const slugs = unique(map(links, (link) => link.slug));
  return filter(links, (link) => includes(slugs, link.slug));
};

const sortLinksByTitle = (links) => {
  return sortBy(links, (link) => lower(link.title), 1);
};

const extractExcerpt = (content) => {
  const lines = split(content, "\n");
  const proseLine = fold(lines, null, (acc, line) => {
    if (acc) return acc;
    const trimmed = trim(line);
    const isHeading = starts(trimmed, "#");
    const isEmpty = trimmed === "";
    return isHeading || isEmpty ? null : trimmed;
  });
  const cleanedLine = proseLine ? replace(proseLine, "\\[\\[[^\\]]+\\]\\]", "", "gu", "") : "";
  return replace(cleanedLine, "`[^a-zA-Z0-9\\s]`", "gu", "");
};

const computeBacklinks = (record, catalog) => {
  return fold(values(catalog), [], (acc, note) => {
    const outgoingSlugs = map(note.outgoing_links, (link) => link.slug);
    return includes(outgoingSlugs, record.slug) ? concat(acc, [{ slug: note.slug, title: note.title }]) : acc;
  });
};

const projectNote = (record, catalog) => {
  const slug = record.slug;
  const title = record.title;
  const content = record.content;
  const created_at = record.created_at;
  const updated_at = record.updated_at;
  const tags = map(record.tags, (tag) => ({ slug: tag }));
  const links = extractLinks(content);
  const validLinks = filterValidLinks(links, catalog);
  const resolvedLinks = resolveLinkTitles(validLinks, catalog);
  const uniqueResolvedLinks = uniqueLinks(resolvedLinks);
  const sortedLinks = sortLinksByTitle(uniqueResolvedLinks);
  const incoming_links = computeBacklinks(record, catalog);
  const outgoing_links = filter(sortedLinks, (link) => includes(map(catalog[link.slug].incoming_links, (l) => l.slug), slug));
  return {
    slug,
    title,
    content,
    created_at,
    updated_at,
    tags,
    incoming_links,
    outgoing_links
  };
};

const excerpt = (content) => {
  const lines = split(content, "\n");
  const proseLine = fold(lines, null, (acc, line) => {
    if (acc) return acc;
    const trimmed = trim(line);
    const isHeading = starts(trimmed, "#");
    const isEmpty = trimmed === "";
    return isHeading || isEmpty ? null : trimmed;
  });
  const cleanedLine = proseLine ? replace(proseLine, "\\[\\[[^\\]]+\\]\\]", "", "gu", "") : "";
  return replace(cleanedLine, "`[^a-zA-Z0-9\\s]`", "gu", "");
};
function sections(content) {
  const lines = split(content, "\n");
  const result = fold(lines, { fence: null, headings: [] }, (acc, line, i) => {
    const trimmed = trim(line);
    const fenceMatch = match(trimmed, "^```(\\w+)?$", "u");
    if (fenceMatch) {
      return withKey(acc, "fence", acc.fence ? null : fenceMatch[1]);
    }
    if (!acc.fence) {
      const headingMatch = match(trimmed, "^#{1,6} (.+)$", "u");
      if (headingMatch) {
        const level = length(headingMatch[0]) - length(headingMatch[1]) - 1;
        return withKey(acc, "headings", concat(acc.headings, [{ index: i, level: level, title: headingMatch[1] }]));
      }
    }
    return acc;
  });
  if (length(result.headings) === 0) {
    return fail(400, { error: "note has no headings to split on" });
  }
  const minLevel = fold(result.headings, Infinity, (acc, heading) => min(acc, heading.level));
  const boundaries = filter(result.headings, (heading) => heading.level === minLevel);
  const sections = fold(boundaries, [], (acc, boundary, i) => {
    const start = boundary.index + 1;
    const end = i + 1 < length(boundaries) ? boundaries[i + 1].index : length(lines);
    const sectionContent = join(slice(lines, start, end), "\n");
    return concat(acc, [{ title: boundary.title, content: trim(sectionContent) }]);
  });
  return sections;
}

function splitNotes() {
  const source = (state.items ?? {})[params.slug];
  if (!source) {
    return fail(404, "not found");
  }
  const tag = body.tag ?? "";
  if (tag && !includes(state.tags, tag)) {
    return fail(400, { error: "unknown tag: " + tag });
  }
  const sectionsList = sections(source.content);
  if (type(sectionsList) === "object" && has(sectionsList, "status")) {
    return sectionsList;
  }
  const catalog = state.catalog ?? {};
  const reservedSlugs = fold(sectionsList, {}, (acc, section) => {
    const slug = freeSlug(section.title, catalog);
    return withKey(acc, slug, true);
  });
  const summaries = fold(sectionsList, [], (acc, section) => {
    const slug = freeSlug(section.title, concat(catalog, reservedSlugs));
    const summary = {
      slug: slug,
      title: section.title,
      created_at: source.created_at,
      updated_at: source.updated_at,
      excerpt: excerpt(section.content),
      tags: tag ? [{ slug: tag }] : [],
      incoming_links: [],
      outgoing_links: []
    };
    return concat(acc, [summary]);
  });
  return respond(201, { notes: summaries }, []);
}

route("POST", "/api/v1/notes/:slug/split", splitNotes);
function freeSlug(title, catalog) {
  const trimmedTitle = trim(title);
  const baseSlug = normalize(lower(trimmedTitle), "NFKD");
  const slug = replace(baseSlug, "[^a-z0-9]+", "gu", "-");
  const existingSlugs = values(catalog).map(record => record.slug);
  const isUnique = (s) => !includes(existingSlugs, s);
  const trySlug = (s, i) => isUnique(s) ? s : null;
  const attemptSlugs = concat([slug], map(range(2, 100), i => slug + "-" + i));
  const availableSlug = fold(attemptSlugs, null, (acc, s) => acc || trySlug(s));
  if (!availableSlug) return fail(409, "slug already exists");
  return availableSlug;
}

function createNote() {
  const notes = state.notes ?? {};
  const tags = state.tags ?? {};
  const slug = body.slug ?? freeSlug(body.title, notes);
  if (has(notes, slug)) return fail(409, "slug already exists");
  const trimmedTitle = trim(body.title);
  const canonicalTags = sortBy(unique(body.tags ?? []), tag => tag, 1);
  const unknownTags = filter(canonicalTags, tag => !has(tags, tag));
  if (length(unknownTags) > 0) return fail(400, "unknown tag: " + join(unknownTags, ", "));
  const newNote = {
    slug: slug,
    title: trimmedTitle,
    content: body.content ?? "",
    created_at: iso(now),
    updated_at: iso(now),
    tags: map(canonicalTags, tag => ({ slug: tag }))
  };
  const updatedNotes = withKey(notes, slug, newNote);
  const effects = [put("notes", slug, newNote)];
  return respond(201, projectNote(newNote, updatedNotes), effects);
}

function updateNote() {
  const notes = state.notes ?? {};
  const tags = state.tags ?? {};
  const slug = params.slug;
  const note = notes[slug];
  if (!note) return fail(404, "not found");
  const ifMatch = headers["if-match"];
  if (ifMatch && ifMatch !== "\"" + note.updated_at + "\"") return fail(412, "version mismatch");
  const trimmedTitle = trim(body.title ?? note.title);
  const canonicalTags = sortBy(unique(body.tags ?? map(note.tags, tag => tag.slug)), tag => tag, 1);
  const unknownTags = filter(canonicalTags, tag => !has(tags, tag));
  if (length(unknownTags) > 0) return fail(400, "unknown tag: " + join(unknownTags, ", "));
  const newNote = {
    slug: slug,
    title: trimmedTitle,
    content: body.content ?? note.content,
    created_at: note.created_at,
    updated_at: iso(max(now, millis(note.updated_at) + 1)),
    tags: map(canonicalTags, tag => ({ slug: tag }))
  };
  const effects = [];
  if (note.title !== newNote.title || note.content !== newNote.content || !includes(sortBy(note.tags, tag => tag.slug, 1), sortBy(newNote.tags, tag => tag.slug, 1))) {
    effects.push(put("notes", slug, newNote));
  }
  return respond(200, projectNote(newNote, notes), effects, { "etag": "\"" + newNote.updated_at + "\"" });
}

function deleteNote() {
  const notes = state.notes ?? {};
  const slug = params.slug;
  const note = notes[slug];
  if (!note) return fail(404, "not found");
  const effects = [del("notes", slug)];
  return respond(204, null, effects);
}

route("POST", "/api/v1/notes", createNote);
route("PATCH", "/api/v1/notes/:slug", updateNote);
route("DELETE", "/api/v1/notes/:slug", deleteNote);
function tagList() {
  const tags = state.tags ?? {};
  const notes = state.notes ?? {};
  const tagCounts = map(values(tags), (tag) => {
    const count = fold(values(notes), 0, (acc, note) => includes(note.tags, tag.slug) ? acc + 1 : acc);
    return { slug: tag.slug, note_count: count };
  });
  return sortBy(tagCounts, (tag) => tag.slug, 1);
}

function getTags() {
  const tags = tagList();
  return respond(200, { tags: tags });
}

function postTags() {
  const slug = body.slug;
  const tags = state.tags ?? {};
  if (has(tags, slug)) return fail(409, { error: "slug already exists" });
  const newTags = withKey(tags, slug, { slug: slug });
  return respond(201, { slug: slug }, [put("tags", slug, { slug: slug })]);
}

function deleteTag() {
  const slug = params.slug;
  const tags = state.tags ?? {};
  if (!has(tags, slug)) return fail(404, { error: "not found" });
  const notes = state.notes ?? {};
  const updatedNotes = fold(values(notes), {}, (acc, note) => {
    const newTags = filter(note.tags, (tag) => tag !== slug);
    return withKey(acc, note.slug, { ...note, tags: newTags });
  });
  const effects = concat(
    [del("tags", slug)],
    map(values(updatedNotes), (note) => put("notes", note.slug, note))
  );
  return respond(204, null, effects);
}

function getNotes() {
  const notes = state.notes ?? {};
  const tags = query.tag ?? [];
  const search = query.search ?? "";
  const prefix = query.prefix ?? "";
  const sort = query.sort ?? "title";
  const order = query.order ?? "asc";
  const offset = number(query.offset, 0);
  const limit = number(query.limit, 10);

  const filteredNotes = filter(values(notes), (note) => {
    const tagMatch = tags.length === 0 || tags.every((tag) => includes(note.tags, tag));
    const searchMatch = search === "" || includes(lower(note.content), lower(search));
    const prefixMatch = prefix === "" || starts(lower(note.title), lower(prefix));
    return tagMatch && searchMatch && prefixMatch;
  });

  const sortedNotes = sortBy(filteredNotes, (note) => note[sort], order === "asc" ? 1 : -1);
  const paginatedNotes = slice(sortedNotes, offset, offset + limit);
  const total = length(filteredNotes);

  const summaries = map(paginatedNotes, (note) => {
    const summary = projectNote(note, notes);
    return { ...summary, excerpt: excerpt(summary.content), content: undefined };
  });

  return respond(200, { total: total, notes: summaries });
}

function getNote() {
  const slug = params.slug;
  const notes = state.notes ?? {};
  const note = notes[slug];
  if (!note) return fail(404, { error: "not found" });
  const summary = projectNote(note, notes);
  return respond(200, summary, [], { ETag: `"${summary.updated_at}"` });
}

route("GET", "/api/v1/tags", getTags);
route("POST", "/api/v1/tags", postTags);
route("DELETE", "/api/v1/tags/:slug", deleteTag);
route("GET", "/api/v1/notes", getNotes);
route("GET", "/api/v1/notes/:slug", getNote);
