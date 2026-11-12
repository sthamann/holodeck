// This renderer consumes a component tree. It knows no application routes or rules.
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function local(path) {
  const url = new URL(path, location.origin);
  if (url.origin !== location.origin || !String(path).startsWith('/')) throw new Error('Invalid link');
  return url;
}

function anchor(label, path, className) {
  const a = element('a', label, className);
  a.href = local(path);
  return a;
}

let fieldId = 0;
function fields(form, descriptions) {
  const bindings = [];
  for (const field of descriptions) {
    if (field.kind === 'checks') {
      const group = element('fieldset');
      group.append(element('legend', field.label));
      const inputs = field.options.map(({ value, label }) => {
        const control = element('input');
        control.type = 'checkbox';
        control.name = field.name;
        control.value = value;
        control.checked = (field.value ?? []).includes(value);
        const item = element('label', null, 'check');
        item.append(control, document.createTextNode(label));
        group.append(item);
        return control;
      });
      if (!inputs.length) group.append(element('small', 'No options available.'));
      bindings.push([field.name, () => inputs.filter((input) => input.checked).map((input) => input.value)]);
      form.append(group);
      continue;
    }
    const label = element('label', null, 'field');
    const caption = element('span', field.label);
    caption.id = 'caption-' + ++fieldId;
    const control = element(field.kind === 'input' ? 'input' : field.kind);
    control.name = field.name;
    if (field.kind === 'select') {
      for (const { label, value } of field.options) {
        const option = element('option', label);
        option.value = value;
        control.append(option);
      }
    }
    if (field.kind === 'textarea') control.rows = field.rows ?? 6;
    if (field.required) control.required = true;
    if (field.limit) control.maxLength = field.limit;
    if (field.placeholder) control.placeholder = field.placeholder;
    control.value = field.value ?? '';
    control.setAttribute('aria-labelledby', caption.id);
    label.append(caption, control);
    form.append(label);
    bindings.push([field.name, () => control.value]);
  }
  return () => Object.fromEntries(bindings.map(([name, value]) => [name, value()]));
}

function renderForm(spec) {
  const form = element('form');
  const values = fields(form, spec.fields);
  const button = element('button', spec.label, spec.tone === 'danger' ? 'danger' : null);
  const status = element('p', null, 'form-status');
  status.setAttribute('role', 'status');
  form.append(button, status);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    const url = local(spec.path);
    const input = values();
    if (spec.method === 'GET') {
      for (const [name, value] of Object.entries(input)) if (value !== '') url.searchParams.set(name, value);
      location.assign(url);
      return;
    }
    status.textContent = '';
    button.disabled = true;
    try {
      const response = await fetch(url, { method: spec.method,
        headers: { 'content-type': spec.mediaType ?? 'application/json', ...spec.headers },
        body: spec.payload ?? JSON.stringify(input) });
      const body = response.status === 204 ? null : await response.json();
      if (!response.ok) {
        status.textContent = body?.error ?? 'Request failed';
        if (spec.recovery?.status === response.status) {
          status.append(document.createTextNode(' · '), anchor(spec.recovery.label, spec.recovery.href));
        }
        return;
      }
      const next = typeof spec.next === 'object' && spec.next !== null
        ? spec.next.prefix + encodeURIComponent(body?.[spec.next.field] ?? '') : spec.next;
      if (next) location.assign(local(next));
      else status.textContent = 'Saved';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return form;
}

function prose({ value, inline }) {
  const article = element('article', null, 'prose');
  const bindings = new Map(inline.map((binding) => [binding.token, binding]));
  const escaped = [...bindings.keys()].map((token) => token.replace(/[.*+?^$\{\}()|[\]\\]/gu, '\\$&'));
  const tokens = new RegExp(['(\x60+)([^\\n]*?)\\1', ...escaped].join('|'), 'gu');
  function appendInline(tag, value) {
    const item = element(tag);
    let offset = 0;
    for (const match of value.matchAll(tokens)) {
      if (value[match.index - 1] === '\\') continue;
      item.append(document.createTextNode(value.slice(offset, match.index)));
      const binding = bindings.get(match[0]);
      item.append(binding ? anchor(binding.label, binding.href) : element('code', match[2]));
      offset = match.index + match[0].length;
    }
    item.append(document.createTextNode(value.slice(offset)));
    article.append(item);
  }
  let buffer = [];
  let fence = null;
  function paragraph() {
    if (buffer.length) appendInline('p', buffer.join('\n'));
    buffer = [];
  }
  function codeBlock() {
    const pre = element('pre');
    pre.append(element('code', buffer.join('\n')));
    article.append(pre);
    buffer = [];
  }
  for (const line of value.split('\n')) {
    const delimiter = /^(\x60{3,}|~{3,})(.*)$/u.exec(line);
    if (fence) {
      if (delimiter && delimiter[1][0] === fence[0] && delimiter[1].length >= fence.length && !delimiter[2].trim()) {
        codeBlock();
        fence = null;
      } else buffer.push(line);
      continue;
    }
    const heading = /^(#{1,6})[ \t]+(.+)$/u.exec(line);
    if (delimiter || heading || !line.trim()) {
      paragraph();
      if (delimiter) fence = delimiter[1];
      if (heading) appendInline('h' + Math.min(6, heading[1].length + 1), heading[2]);
    } else buffer.push(line);
  }
  if (fence) codeBlock();
  else paragraph();
  return article;
}

function render(spec) {
  if (!spec) return document.createTextNode('');
  switch (spec.kind) {
    case 'workspace': {
      document.title = spec.title;
      const root = element('div', null, 'workspace');
      const header = element('header', null, 'topbar');
      header.append(anchor(spec.title, spec.home, 'brand'), render(spec.action));
      const frame = element('div', null, 'frame');
      const side = element('aside');
      const main = element('main');
      side.setAttribute('aria-label', 'Workspace navigation');
      side.append(render(spec.sidebar));
      main.append(render(spec.main));
      frame.append(side, main);
      root.append(header, frame);
      return root;
    }
    case 'heading': return element('h' + (spec.level === 2 ? '2' : '1'), spec.value);
    case 'text': return element('p', spec.value);
    case 'document': return prose(spec);
    case 'link': return anchor(spec.label, spec.href, spec.tone === 'primary' ? 'primary' : undefined);
    case 'copy': {
      const button = element('button', spec.label);
      button.type = 'button';
      button.addEventListener('click', async () => {
        const url = local(spec.href);
        try {
          await navigator.clipboard.writeText(String(url));
          button.textContent = 'Copied';
        } catch {
          location.assign(url);
        }
      });
      return button;
    }
    case 'item': {
      const link = anchor(spec.label, spec.href, 'note-item');
      if (spec.active) link.setAttribute('aria-current', 'page');
      if (spec.excerpt) link.append(element('small', spec.excerpt.replace(/[\u0002\u0003]/gu, '')));
      return link;
    }
    case 'form': return renderForm(spec);
    case 'linklist':
    case 'ordered': {
      const list = element(spec.kind === 'ordered' ? 'ol' : 'ul', null, spec.kind);
      for (const child of spec.children) {
        const item = element('li');
        item.append(render(child));
        list.append(item);
      }
      return list;
    }
    case 'row':
    case 'stack':
    case 'tags':
    case 'notelist': {
      const group = element('div', null, spec.kind);
      group.append(...spec.children.filter(Boolean).map(render));
      return group;
    }
    default: throw new Error('Unknown component');
  }
}

const surface = document.querySelector('#surface');
try {
  const response = await fetch('/holo' + location.search);
  if (!response.ok) throw new Error('The workspace could not open.');
  surface.replaceChildren(render(await response.json()));
} catch (error) {
  surface.replaceChildren(element('p', error.message, 'message'));
}
