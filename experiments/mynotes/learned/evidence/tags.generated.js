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


route("GET", "/api/v1/tags", getTags);
route("POST", "/api/v1/tags", postTags);
route("DELETE", "/api/v1/tags/:slug", deleteTag);
