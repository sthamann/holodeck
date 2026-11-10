// The browser interprets primitives and bindings; it contains no page definitions.
const surface = document.querySelector('#surface');
const title = document.querySelector('#title');
const notice = document.querySelector('#notice');
const nav = document.querySelector('nav');
const composer = document.querySelector('#composer');
const brand = document.querySelector('.brand');
const tasks = {storefront:'Sortiment', checkout:'Kasse', order:'Bestellung', admin:'Verwaltung'};
const money = new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR'});
const compare = new Intl.Collator('de-DE',{numeric:true,sensitivity:'base'});
const statusLabels = {
  stage:{open:'Offen',in_progress:'In Bearbeitung',completed:'Abgeschlossen'},
  payment:{open:'Zahlung offen',paid:'Bezahlt',refunded:'Erstattet',failed:'Fehlgeschlagen',in_progress:'In Bearbeitung',paid_partially:'Teilbezahlt',authorized:'Autorisiert',cancelled:'Storniert',refunded_partially:'Teilweise erstattet'},
  delivery:{open:'Versand offen',shipped:'Versendet',returned:'Retourniert',shipped_partially:'Teilweise versendet',cancelled:'Storniert',returned_partially:'Teilweise retourniert'},
  tag:{in_stock:'Verfügbar',sold_out:'Ausverkauft'}
};
let view = {}, sequence = 0, navigation = 0;
const defaults = new Map();
const local = new Map();

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function message(text = '', error = false) {
  notice.hidden = !text;
  notice.textContent = text;
  notice.classList.toggle('error', error);
}
function url(destination) {
  return '?' + new URLSearchParams(destination);
}
function link(destination, text, cls) {
  const a = el('a',text,cls);
  a.href = url(destination);
  const current = destination.task === view.task && (!destination.intent || view.intent === destination.intent);
  if (current) a.setAttribute('aria-current','page');
  a.addEventListener('click', event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    go(destination);
  });
  return a;
}
function go(destination) {
  navigation++;
  history.pushState({},'',url(destination));
  message();
  load();
}
async function request(path, body) {
  const headers = {'X-Deck-Session':sessionStorage.getItem('deck-session') ?? '',
                   'X-Deck-Admin':sessionStorage.getItem('deck-admin') ?? ''};
  const options = {headers};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.method = 'POST';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path,options);
  const data = await response.json();
  if (data.session) sessionStorage.setItem('deck-session',data.session);
  if (data.admin_session) sessionStorage.setItem('deck-admin',data.admin_session);
  if (!response.ok) {
    const error = new Error(data.error);
    error.status = response.status;
    throw error;
  }
  return data;
}
function inputField(field) {
  const label = el('label',field.label);
  const input = el('input');
  input.name = field.name;
  input.type = field.type;
  input.required = true;
  if (field.type === 'checkbox') {
    input.checked = !!field.value;
    label.classList.add('check');
    label.prepend(input);
  } else {
    input.value = field.value;
    if (field.type === 'number') {
      input.min = '1';
      input.step = '1';
    }
    label.append(input);
  }
  return label;
}
function action(spec) {
  const {task,workspace} = view;
  const form = el('form');
  if (spec.fields.length) {
    const fields = el('div',undefined,'fields');
    fields.append(...spec.fields.map(inputField));
    form.append(fields);
  }
  const button = el('button',spec.title);
  form.append(button);
  let eventId, previous;
  form.addEventListener('submit',async event => {
    event.preventDefault();
    const fields = new FormData(form);
    const args = {...spec.args};
    for (const field of spec.fields) {
      const value = fields.get(field.name);
      args[field.name] = field.type === 'checkbox' ? value !== null : field.type === 'number' ? Number(value) : value;
    }
    const signature = JSON.stringify(args);
    if (signature !== previous) {
      eventId = crypto.randomUUID();
      previous = signature;
    }
    button.disabled = true;
    const at = navigation;
    try {
      await request('/command',{task,event:spec.event,id:eventId,args,workspace});
      if (at !== navigation) return;
      if (spec.navigate) go(spec.navigate);
      else { message('Gespeichert.'); await load(); }
    } catch (error) {
      message(error.message,true);
    } finally {
      button.disabled = false;
    }
  });
  return form;
}
function actions(list) {
  const group = el('div',undefined,'actions');
  group.append(...list.map(action));
  return group;
}
function links(list) {
  const group = el('div',undefined,'links');
  group.append(...list.map(({title,...dest}) => link(dest,title)));
  return group;
}
function readable(field, value) {
  if (field.format === 'money') return money.format(value / 100);
  if (field.format === 'order-id') return value.slice(0,8).toUpperCase();
  if (field.format === 'items') return value.map(item => item.quantity + ' × ' + item.name).join(', ');
  return statusLabels[field.format]?.[value] ?? String(value ?? '');
}
function valueNode(field, value, alt = '') {
  if (field.format === 'image') {
    const img = el('img');
    img.src = '/media/' + encodeURIComponent(value);
    img.alt = alt;
    img.loading = 'lazy';
    return img;
  }
  const cls = statusLabels[field.format] ? 'tag' : field.format === 'money' ? 'money' : '';
  return el('span',readable(field,value),cls);
}
function values(fields, row, cls = 'values') {
  const dl = el('dl',undefined,cls);
  for (const field of fields) {
    const item = el('div');
    const dd = el('dd');
    dd.append(valueNode(field,row.values[field.key],row.values.name));
    item.append(el('dt',field.label),dd);
    dl.append(item);
  }
  return dl;
}
function visibleRows(node, state) {
  const query = state.query?.trim().toLocaleLowerCase('de-DE');
  const rows = node.rows.filter(row =>
    (!query || node.fields.some(field => field.format !== 'image' &&
      readable(field,row.values[field.key]).toLocaleLowerCase('de-DE').includes(query))) &&
    Object.entries(state.filters).every(([key,value]) => !value || String(row.values[key]) === value));
  if (state.sort) {
    const field = node.fields.find(field => field.key === state.sort);
    if (field) rows.sort((a,b) => {
      const left = a.values[field.key], right = b.values[field.key];
      return state.direction * (typeof left === 'number' ? left - right : compare.compare(readable(field,left),readable(field,right)));
    });
  }
  return rows;
}
function table(node, rows, state, refresh, queryChanged) {
  const wrapper = el('div',undefined,'table-box');
  const table = el('table');
  table.setAttribute('aria-label',node.title);
  const headings = el('tr');
  const expandable = node.tools.includes('expand');
  const main = node.fields.filter(field => !expandable || !node.details.includes(field.key));
  const withActions = expandable || rows.some(row => row.actions.length);
  for (const field of main) {
    const th = el('th');
    th.scope = 'col';
    if (node.tools.includes('sort') && node.sorts.includes(field.key)) {
      const ordered = state.sort === field.key;
      th.setAttribute('aria-sort', ordered ? state.direction === 1 ? 'ascending' : 'descending' : 'none');
      const button = el('button',field.label,'secondary');
      button.setAttribute('aria-label','Nach ' + field.label + ' sortieren');
      button.onclick = () => {
        state.direction = ordered ? -state.direction : 1;
        state.sort = field.key;
        queryChanged(field.key);
      };
      button.dataset.sort = field.key;
      th.append(button);
    } else th.textContent = field.label;
    headings.append(th);
  }
  if (withActions) headings.append(el('th',expandable ? 'Bearbeitung' : 'Auswahl'));
  const thead = el('thead');
  thead.append(headings);
  const tbody = el('tbody');
  for (const [index,row] of rows.entries()) {
    const tr = el('tr');
    for (const field of main) {
      const cell = el('td');
      cell.append(valueNode(field,row.values[field.key],row.values.name));
      tr.append(cell);
    }
    const key = row.values.id ?? row.values.sku ?? index;
    if (withActions) {
      const cell = el('td');
      if (expandable) {
        const button = el('button',state.open.has(key) ? 'Schließen' : 'Details','secondary');
        button.setAttribute('aria-expanded',String(state.open.has(key)));
        button.dataset.expand = key;
        button.setAttribute('aria-label',(state.open.has(key) ? 'Schließen ' : 'Details ') +
          readable({format:'order-id'}, String(key)));
        button.onclick = () => {
          if (state.open.has(key)) state.open.delete(key);
          else state.open.add(key);
          const parent = wrapper.parentNode;
          refresh();
          parent?.querySelector('[data-expand="' + CSS.escape(key) + '"]')?.focus();
        };
        cell.append(button);
      } else cell.append(actions(row.actions));
      tr.append(cell);
    }
    tbody.append(tr);
    if (expandable && state.open.has(key)) {
      const detail = el('tr',undefined,'expanded');
      const cell = el('td');
      cell.colSpan = headings.children.length;
      const fields = node.details.map(key => node.fields.find(field => field.key === key));
      cell.append(values(fields,row),actions(row.actions));
      detail.append(cell);
      tbody.append(detail);
    }
  }
  table.append(thead,tbody);
  wrapper.append(table);
  return wrapper;
}
function collection(node,path) {
  const section = el('section',undefined,node.component === 'grid' ? '' : 'panel');
  section.append(el('h2',node.title));
  if (node.criteria?.length) {
    const criteria = el('div',undefined,'criteria');
    const symbols = {eq:'=',lt:'<',lte:'≤',gt:'>',gte:'≥'};
    for (const item of node.criteria) {
      criteria.append(el('span',item.label + ' ' + symbols[item.op] + ' ' + readable(item,item.value),'tag'));
    }
    section.append(criteria);
  }
  const tools = el('div',undefined,'tools');
  const content = el('div');
  const key = view.task + '/' + view.intent + '/' + (view.workspace ?? '') + '/' + path;
  if (!local.has(key)) local.set(key,{filters:{},direction:1,open:new Set()});
  const state = local.get(key);
  section.dataset.node = path;
  if (node.paging) state.source = node.source;
  function refresh(focus) {
    const rows = node.paging ? node.rows : visibleRows(node,state);
    let body;
    if (!rows.length) body = el('p','Keine passenden Einträge.','empty');
    else if (node.component === 'table') body = table(node,rows,state,refresh,changed);
    else if (node.component === 'grid') {
      body = el('div',undefined,'cards');
      for (const row of rows) {
        const card = el('article',undefined,'card');
        const inside = el('div',undefined,'card-body');
        for (const field of node.fields) {
          const value = row.values[field.key];
          if (field.format === 'image') card.append(valueNode(field,value,row.values.name));
          else if (field.key === 'name') inside.append(el('h3',value));
          else inside.append(valueNode(field,value));
        }
        inside.append(actions(row.actions));
        card.append(inside);
        body.append(card);
      }
    } else {
      body = el('div');
      for (const row of rows) {
        const line = el('div',undefined,'list-row');
        line.append(values(node.fields,row),actions(row.actions));
        body.append(line);
      }
    }
    content.replaceChildren(body);
    if (focus) content.querySelector('[data-sort="' + CSS.escape(focus) + '"]')?.focus();
  }
  function changed(focus) {
    if (!node.paging) return refresh(focus);
    state.page = 0;
    load(focus ? {path, field:focus} : {path, search:true});
  }
  if (node.tools.includes('search')) {
    const label = el('label','Suchen');
    const input = el('input');
    input.type = 'search';
    input.placeholder = 'In ' + node.title.toLowerCase() + ' suchen';
    input.value = state.query ?? '';
    input.dataset.search = path;
    input.oninput = () => {
      state.query = input.value;
      clearTimeout(state.timer);
      const scheduled = navigation;
      if (node.paging) state.timer = setTimeout(() => { if (scheduled === navigation) changed(); },180);
      else refresh();
    };
    label.append(input);
    tools.append(label);
  }
  if (node.tools.includes('filter')) {
    for (const filter of node.filters) {
      const field = node.fields.find(field => field.key === filter);
      const label = el('label',field.label);
      const select = el('select');
      select.append(el('option','Alle'));
      select.firstChild.value = '';
      const distinct = new Set(node.paging?.options[filter] ?? node.rows.map(row => row.values[filter]));
      for (const value of distinct) {
        const option = el('option',readable(field,value));
        option.value = value;
        select.append(option);
      }
      if (!distinct.has(state.filters[filter])) delete state.filters[filter];
      select.value = state.filters[filter] ?? '';
      select.onchange = () => { state.filters[filter] = select.value; changed(); };
      label.append(select);
      tools.append(label);
    }
  }
  if (tools.children.length) section.append(tools);
  section.append(content);
  if (node.paging && node.paging.total > node.paging.size) {
    const {page,size,total} = node.paging;
    const pager = el('div',undefined,'links');
    for (const [label,delta,disabled] of [
      ['Zurück',-1,page===0], ['Weiter',1,(page+1)*size>=total]
    ]) {
      const button = el('button',label,'secondary');
      button.disabled = disabled;
      button.onclick = () => { state.page = page + delta; load(); };
      pager.append(button);
    }
    pager.append(el('span',(page*size+1)+'–'+Math.min(total,(page+1)*size)+' von '+total,'subtle'));
    section.append(pager);
  }
  if (node.links.length) section.append(links(node.links));
  refresh();
  return section;
}
function render(node,path = 'root') {
  if (node.children) {
    const box = el('div',undefined,node.component);
    box.append(...node.children.map((child,index) => render(child,path + '.' + index)));
    return box;
  }
  if (['table','grid','list'].includes(node.component)) return collection(node,path);
  if (node.component === 'nav') return links(node.links);
  const box = el('section',undefined,node.component === 'hero' ? 'hero' : node.component === 'stats' ? 'stats' : 'panel');
  if (node.title !== '_') box.append(el('h2',node.title));
  if (node.text) box.append(el('p',node.text));
  if (node.component === 'stats' || node.component === 'detail') {
    const strip = node.component === 'stats' && node.fields.every(field => field.format === 'number');
    for (const row of node.rows) box.append(values(node.fields,row,strip ? 'metrics-strip' : 'values'));
  }
  if (node.actions?.length) box.append(...node.actions.map(action));
  if (node.links?.length) box.append(links(node.links));
  return box;
}
function login() {
  const form = el('form',undefined,'panel');
  const input = el('input');
  input.type = 'password';
  input.required = true;
  input.autocomplete = 'current-password';
  const label = el('label','Admin-Passwort');
  label.append(input);
  form.append(el('h2','Admin anmelden'),label,el('button','Anmelden'));
  form.addEventListener('submit',async event => {
    event.preventDefault();
    try { await request('/admin-login',{password:input.value}); message(); await load(); }
    catch (error) { message(error.message,true); }
  });
  surface.replaceChildren(form);
}
function workspaceComposer() {
  const box = el('section',undefined,'composer');
  box.append(el('h2','Admin-Ansicht beschreiben'));
  const form = el('form');
  const label = el('label','Welche Daten und Aktionen brauchst du?');
  const input = el('textarea');
  input.name = 'request';
  input.required = true;
  input.maxLength = 1600;
  input.rows = 2;
  label.append(input);
  const button = el('button','Ansicht erzeugen');
  form.append(label,button);
  form.addEventListener('submit',async event => {
    event.preventDefault();
    const at = navigation;
    button.disabled = true;
    button.textContent = 'Ansicht wird erzeugt …';
    message();
    try {
      const result = await request('/workspace',{prompt:input.value});
      if (at === navigation) go({task:'admin',workspace:result.workspace});
    } catch (error) {
      if (at === navigation) message(error.message,true);
    } finally {
      button.disabled = false;
      button.textContent = 'Ansicht erzeugen';
    }
  });
  box.append(form);
  composer.replaceChildren(box);
  composer.hidden = false;
}
async function load(focus) {
  const current = ++sequence;
  const query = new URLSearchParams(location.search);
  const task = Object.hasOwn(tasks,query.get('task')) ? query.get('task') : 'storefront';
  const intent = query.get('intent') ?? '';
  const workspace = task === 'admin' ? query.get('workspace') : null;
  view = workspace ? {task,intent,workspace} : {task,intent};
  if (task !== 'admin') composer.hidden = true;
  nav.replaceChildren(...Object.entries(tasks).map(([task,label]) => link({task},label)));
  surface.inert = !focus?.search;
  surface.setAttribute('aria-busy','true');
  if (!surface.children.length) surface.append(el('p','Ansicht wird vorbereitet …','empty'));
  try {
    const mode = workspace ? 'workspace' : intent || defaults.get(task) || '';
    const prefix = task + '/' + mode + '/' + (workspace ?? '') + '/';
    const ops = {}, sent = [];
    for (const [key,state] of local) if (key.startsWith(prefix) && state.source) {
      ops[state.source] = {q:state.query,sort:state.sort,direction:state.direction,filters:state.filters,page:state.page};
      sent.push([state,state.query]);
    }
    const params = new URLSearchParams({...view,ops:JSON.stringify(ops)});
    const data = await request('/projection?' + params);
    if (current !== sequence || sent.some(([state,query]) => state.query !== query)) return;
    view.intent = data.context[1];
    if (!intent && !workspace) defaults.set(task,view.intent);
    document.documentElement.dataset.theme = data.brand.theme;
    brand.textContent = data.brand.name;
    title.textContent = data.title;
    title.hidden = data.program.some(node => node.kind === 'hero');
    document.title = data.title + ' · ' + data.brand.name;
    surface.replaceChildren(render(data.tree));
    if (data.workspaces) {
      if (composer.hidden) workspaceComposer();
    } else composer.hidden = true;
    surface.inert = false;
    if (focus) {
      const parent = surface.querySelector('[data-node="' + CSS.escape(focus.path) + '"]');
      const control = focus.search ? parent?.querySelector('input[type=search]')
        : parent?.querySelector('[data-sort="' + CSS.escape(focus.field) + '"]');
      control?.focus();
      if (focus.search && control) control.setSelectionRange(control.value.length,control.value.length);
    }
  } catch (error) {
    if (current !== sequence) return;
    composer.hidden = true;
    title.hidden = false;
    title.textContent = tasks[task];
    if (task === 'admin' && error.status === 403) login();
    else { message(error.message,true); surface.replaceChildren(); }
  } finally {
    if (current === sequence) {
      surface.inert = false;
      surface.removeAttribute('aria-busy');
    }
  }
}
brand.addEventListener('click',event => {
  if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    go({task:'storefront'});
  }
});
window.addEventListener('popstate',() => { navigation++; message(); load(); });
load();
