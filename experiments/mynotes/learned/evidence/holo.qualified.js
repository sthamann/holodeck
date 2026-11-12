function holo() {
 const view = query.view[0] ?? "index";
 const slug = query.slug[0];
 const tags = tagList();
 const notes = state.notes ?? {};
 const record = notes[slug];
 const note = record ? projectNote(record, notes) : null;

 if (view === "index") {
  const filteredNotes = filterNotes(notes, query.search[0], query.title[0], query.tag[0]);
  const sidebar = {
   kind: "notelist",
   children: map(values(filteredNotes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const main = {
   kind: "form",
   label: "Search and Filter",
   method: "GET",
   path: "/",
   fields: [
    { kind: "input", label: "Full-text search", name: "search" },
    { kind: "input", label: "Filter by title", name: "title" },
    { kind: "select", label: "Add tag filter", name: "tag", options: map(tags, (tag) => ({ label: tag.slug, value: tag.slug })) }
   ]
  };
  return respond(200, { kind: "workspace", title: "Holo", home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 if (view === "note") {
  if (!note) return fail(404, "Note not found");
  const sidebar = {
   kind: "notelist",
   children: map(values(notes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const main = {
   kind: "stack",
   children: [
    { kind: "heading", value: note.title, level: 1 },
    { kind: "document", value: note.content, inline: [] },
    { kind: "linklist", label: "Links", children: map(note.outgoing_links, (link) => ({ kind: "link", label: link.title, href: "/?view=note&slug=" + link.slug })) },
    { kind: "linklist", label: "Linked from", children: map(note.incoming_links, (link) => ({ kind: "link", label: link.title, href: "/?view=note&slug=" + link.slug })) },
    { kind: "link", label: "Split by headings", href: "/?view=split&slug=" + slug },
    { kind: "link", label: "Edit", href: "/?view=edit&slug=" + slug }
   ]
  };
  return respond(200, { kind: "workspace", title: note.title, home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 if (view === "new") {
  const sidebar = {
   kind: "notelist",
   children: map(values(notes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const main = {
   kind: "stack",
   children: [
    { kind: "heading", value: "New note", level: 1 },
    { kind: "form",
      label: "Create New Note",
      method: "POST",
      path: "/api/v1/notes",
      next: { prefix: "/?view=note&slug=", field: "slug" },
      fields: [
       { kind: "input", label: "Title", name: "title", required: true },
       { kind: "textarea", label: "Content", name: "content", required: true },
       { kind: "checks", label: "Add tag…", name: "tags", value: [], options: map(tags, (tag) => ({ label: tag.slug, value: tag.slug })) }
      ]
    }
   ]
  };
  return respond(200, { kind: "workspace", title: "New note", home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 if (view === "edit") {
  if (!note) return fail(404, "Note not found");
  const sidebar = {
   kind: "notelist",
   children: map(values(notes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const main = {
   kind: "stack",
   children: [
    { kind: "heading", value: "Edit note", level: 1 },
    { kind: "form",
      label: "Edit Note",
      method: "PATCH",
      path: "/api/v1/notes/" + slug,
      headers: { "if-match": "\"" + note.updated_at + "\"" },
      next: { prefix: "/?view=note&slug=", field: "slug" },
      fields: [
       { kind: "input", label: "Title", name: "title", value: note.title, required: true },
       { kind: "textarea", label: "Content", name: "content", value: note.content, required: true },
       { kind: "checks", label: "Add tag…", name: "tags", value: record.tags, options: map(tags, (tag) => ({ label: tag.slug, value: tag.slug })) }
      ]
    }
   ]
  };
  return respond(200, { kind: "workspace", title: "Edit note", home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 if (view === "split") {
  if (!note) return fail(404, "Note not found");
  const sidebar = {
   kind: "notelist",
   children: map(values(notes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const preview = sections(note.content);
  const main = {
   kind: "stack",
   children: [
    { kind: "heading", value: "Split by headings", level: 1 },
    { kind: "ordered", children: map(preview, (section) => ({ kind: "text", value: section.title })) },
    { kind: "form",
      label: "Split Note by Headings",
      method: "POST",
      path: "/api/v1/notes/" + slug + "/split",
      next: "/?view=note&slug=" + slug,
      fields: [
       { kind: "select", label: "Add tag…", name: "tag", options: concat([{ label: "", value: "" }], map(tags, (tag) => ({ label: tag.slug, value: tag.slug }))) }
      ]
    }
   ]
  };
  return respond(200, { kind: "workspace", title: "Split by headings", home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 if (view === "tags") {
  const sidebar = {
   kind: "notelist",
   children: map(values(notes), (note) => ({
    kind: "item",
    label: note.title,
    href: "/?view=note&slug=" + note.slug,
    active: note.slug === slug
   }))
  };
  const main = {
   kind: "stack",
   children: [
    { kind: "heading", value: "Tags", level: 1 },
    { kind: "form",
      label: "Manage Tags",
      method: "POST",
      path: "/api/v1/tags",
      next: "/?view=tags",
      fields: [
       { kind: "input", label: "Create new tag", name: "slug", required: true }
      ]
    }
   ]
  };
  return respond(200, { kind: "workspace", title: "Tags", home: "/", action: { kind: "link", label: "New note", href: "/?view=new" }, sidebar, main });
 }

 return fail(400, "Invalid view");
}


function filterNotes(notes, search, title, tag) {
 return values(notes).filter(note => {
  const matchesSearch = !search || includes(lower(note.content), lower(search)) || includes(lower(note.title), lower(search));
  const matchesTitle = !title || includes(lower(note.title), lower(title));
  const matchesTag = !tag || note.tags.includes(tag);
  return matchesSearch && matchesTitle && matchesTag;
 });
}

route("GET", "/holo", holo);
