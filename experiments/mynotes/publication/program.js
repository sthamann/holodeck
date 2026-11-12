// Typed adapters for the learned machine. This file is compiled, never served.
// Action/status/effect rules and observed UI actions come from recurrent readouts.
function publicationNote(slug) {
  return (state.notes ?? {})[slug];
}

function publicationSnapshot(slug) {
  return (state.publications ?? {})[slug];
}

function publicationHistory(slug) {
  const stream = (state.publicationHistory ?? {})[slug];
  return stream ?? (publicationNote(slug) ? [] : missingHistory());
}

function publicationDecision(slug) {
  return sequence(publicationHistory(slug), "publication");
}

function publishedURL(slug) {
  return "/public/notes/" + slug;
}

function addPublication(value, slug) {
  const snapshot = publicationSnapshot(slug);
  if (!snapshot) return value;
  return withKey(value, "published_at", snapshot.published_at);
}

function addPublicationResponse(response, slug) {
  if (response.status !== 200) return response;
  return withKey(response, "body", addPublication(response.body, slug));
}

function listWithPublication() {
  const response = basePublicationList();
  return withKey(response, "body", withKey(response.body, "notes",
    map(response.body.notes, item => addPublication(item, item.slug))));
}

function readWithPublication() {
  return addPublicationResponse(basePublicationGet(), params.slug);
}

function createWithHistory() {
  const response = basePublicationCreate();
  if (response.status !== 201) return response;
  return withKey(response, "effects", concat(response.effects, [del("publicationHistory", response.body.slug)]));
}

function escapeText(value) {
  return replace(replace(replace(replace(replace(value, "&", "gu", "&amp;"), "<", "gu", "&lt;"),
    ">", "gu", "&gt;"), '"', "gu", "&quot;"), "'", "gu", "&#39;");
}

function formatPlainText(value) {
  const paragraphs = split(value, "\n\n");
  return join(map(paragraphs, paragraph => "<p>" + escapeText(paragraph) + "</p>"), "\n");
}

function supportedFragment(value) {
  // This prototype's transport admits attribute-free, escaped text fragments.
  return type(value) === "string" && length(value) <= 2000000
    && match(value, "^(?:\\s*<(p|h[1-6])>[^<>]*</\\1>\\s*)+$", "u");
}

function publicationEffects(effect, slug, raw) {
  if (effect === "delete") return [del("publications", slug)];
  if (effect === "put") {
    const timestamp = replace(time(now), "\\.\\d{3}Z$", "u", "Z");
    return [put("publications", slug, learnedSnapshot(publicationNote(slug), raw, timestamp))];
  }
  return [];
}

function executePublication(action, common, raw) {
  const slug = params.slug;
  const token = eventToken(action);
  const decision = publicationDecision(slug)[token + 1];
  if (decision.status >= 400) return fail(decision.status, decision.error);
  if (common && (common.status >= 400 || length(common.effects) === 0)) {
    return addPublicationResponse(common, slug);
  }
  if (decision.snapshotEffect === "put" && (mediaType !== "text/html" || !supportedFragment(raw))) {
    return fail(400, "expected an escaped paragraph or heading fragment");
  }
  const changed = publicationEffects(decision.snapshotEffect, slug, raw);
  const history = put("publicationHistory", slug, concat(publicationHistory(slug), [token]));
  const effects = concat(concat(common ? common.effects : [], changed), [history]);
  if (common) return withKey(addPublicationResponse(common, slug), "effects", effects);
  const output = decision.snapshotEffect === "put"
    ? { url: publishedURL(slug), published_at: changed[0].value.published_at } : null;
  return respond(decision.status, output, effects);
}

function publishNote() { return executePublication("publish", null, body); }
function unpublishNote() { return executePublication("unpublish", null, null); }
function editPublicationNote() { return executePublication("edit", basePublicationPatch(), null); }
function deletePublicationNote() { return executePublication("delete", basePublicationDelete(), null); }

function publicNote() {
  const slug = params.slug;
  const publication = publicationSnapshot(slug);
  const decision = publicationDecision(slug)[0];
  const headers = { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" };
  if (decision.publicStatus !== 200 || !publication) {
    return respond(404, "<!doctype html><title>Not found</title><main><h1>Not found</h1></main>", [], headers);
  }
  const title = escapeText(publication.title);
  return respond(200, "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>"
    + title + "</title><link rel=\"stylesheet\" href=\"/surface.css\"></head><body><main><h1>" + title
    + "</h1>\n" + publication.html + "\n</main></body></html>", [], headers);
}

function publicationView(slug, decision) {
  const note = publicationNote(slug);
  const next = "/?view=publication&slug=" + slug;
  const actions = map(decision.controls, label => {
    const binding = learnedControl(label);
    if (binding) {
      const form = {
        kind: "form", label, method: binding.method, path: replace(binding.path, ":slug", "u", slug),
        next, fields: [],
      };
      return binding.mediaType === "text/html"
        ? withKey(withKey(form, "mediaType", binding.mediaType), "payload", formatPlainText(note.content))
        : form;
    }
    if (label === "Close") return { kind: "link", label, href: "/?view=note&slug=" + slug };
    if (label === "Copy link") return { kind: "copy", label, href: publishedURL(slug) };
    return null;
  });
  return { kind: "stack", children: [
    { kind: "heading", value: decision.heading },
    { kind: "heading", level: 2, value: note.title },
    decision.publicStatus === 200 ? { kind: "link", label: "Open public page", href: publishedURL(slug) } : null,
    decision.matchesRevision === false ? { kind: "text", value: "The private draft changed. The public page still shows the last published version." } : null,
    { kind: "row", children: actions },
  ] };
}

function publicationHolo() {
  const standard = basePublicationHolo();
  const slug = query.slug[0];
  const view = query.view[0];
  if (!slug) return standard;
  const decision = publicationDecision(slug)[0];
  if (!decision.trigger || !publicationNote(slug)) return standard;
  if (view === "publication") return withKey(standard, "body", withKey(standard.body, "main", publicationView(slug, decision)));
  if (view !== "note") return standard;
  const action = { kind: "link", label: decision.trigger, href: "/?view=publication&slug=" + slug };
  return withKey(standard, "body", withKey(standard.body, "main", {
    kind: "stack", children: [standard.body.main, action],
  }));
}

route("GET", "/api/v1/notes", listWithPublication);
route("GET", "/api/v1/notes/:slug", readWithPublication);
route("POST", "/api/v1/notes", createWithHistory);
route("PATCH", "/api/v1/notes/:slug", editPublicationNote);
route("DELETE", "/api/v1/notes/:slug", deletePublicationNote);
route("PUT", "/api/v1/notes/:slug/publish", publishNote);
route("DELETE", "/api/v1/notes/:slug/publish", unpublishNote);
route("GET", "/public/notes/:slug", publicNote);
route("GET", "/holo", publicationHolo);
