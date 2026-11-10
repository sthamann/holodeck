'use strict';
const $ = (s, root = document) => root.querySelector(s);
const surface = $('#surface');
const params = new URLSearchParams(location.search);
const requestedShop = params.get('shop');
const demoRequested = params.get('demo') === '1';
const format = new Intl.NumberFormat('de-DE', {style: 'currency', currency: 'EUR'});
const money = cents => format.format(cents / 100);
const title = {storefront: 'Sortiment', checkout: 'Zur Kasse', receipt: 'Bestellung eingegangen', admin: 'Bestellungen'};
const defaultIntent = {
  storefront: 'Ich suche Artikel aus dem Sortiment und möchte Mengen und Gutschein im Warenkorb ändern.',
  checkout: 'Bitte vor dem Bestellen alle Warenkorbpositionen und die Gesamtsumme zur Prüfung anzeigen.',
  receipt: 'Zeig mir die Bestätigung für meinen Einkauf.',
  admin: 'Ich will Bestellungen durchsuchen, die Positionen aufklappen und Zahlung und Versand bearbeiten.'
};
const headings = {products:'Artikel auswählen', cart:'Warenkorb', cart_totals:'Zusammenfassung',
  coupon:'Gutschein', receipt:'Bestellübersicht', orders:'Aufträge', details:'Positionen',
  workflow:'Workflow', taxes:'Steuer je Satz', order_taxes:'Steuer je Satz'};
const columns = {number:'Bestellung', gross:'Brutto', payment:'Zahlung', delivery:'Lieferung', stage:'Auftrag'};
const states = {open:'Offen', paid:'Bezahlt', shipped:'Versandt', returned:'Retourniert',
  returned_partially:'Teilweise retourniert', refunded:'Erstattet', refunded_partially:'Teilweise erstattet'};
const actionLabels = {update:'Warenkorb aktualisieren', discount:'Gutschein einlösen',
  place:'Testbestellung abschicken', pay:'Zahlung als bezahlt markieren',
  reopen:'Zahlung wieder öffnen', ship:'Versand bestätigen'};
let session, view, selected, scope;
let intent = {...defaultIntent};
let sorting = {key:'number', reverse:false};
let filter = {text:'', payment:'', delivery:''};
let busy = false;
let showcase = false;

function node(tag, props = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'text') element.textContent = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else if (value !== false) element.setAttribute(key, value === true ? '' : value);
  }
  element.append(...children.filter(child => child != null));
  return element;
}
function notice(text = '', error = false) {
  const target = $('#message');
  target.textContent = text;
  target.toggleAttribute('data-error', error);
}
async function api(method, body) {
  const response = await fetch('/api/' + method, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error);
  return data;
}
async function operation(run) {
  if (busy) return;
  busy = true;
  surface.setAttribute('aria-busy', 'true');
  try {
    await run();
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
    surface.setAttribute('aria-busy', 'false');
  }
}
async function load(next, target = selected) {
  const output = await api('project', {
    session:session.session, scope:next, intent:intent[next], selected:next === 'admin' ? target : null
  });
  scope = next;
  selected = next === 'admin' ? target : null;
  view = output;
  notice();
  render();
}
function go(next) {
  return operation(() => load(next));
}
async function dispatch(action, payload) {
  await operation(async () => {
    view = await api('action', {
      session:session.session, scope, action:action.id, projection:view.projection,
      revision:view.revision, request:crypto.randomUUID(), ...payload
    });
    if (action.id === 'place') return load('receipt');
    render();
    if (view.receipt.writes['result.accepted'] === false) {
      notice('Der aktuelle Bestellstatus erlaubt diese Aktion nicht.', true);
      return;
    }
    notice(action.id === 'discount' ? 'Gutschein angewendet.' :
      action.id === 'update' ? 'Warenkorb aktualisiert.' : 'Änderung gespeichert.');
  });
}
function actionButton(action, payload, disabled = false) {
  return node('button', {type:'button', 'data-action':action.id, disabled,
    text:action.label || actionLabels[action.id], onclick:() => dispatch(action, payload())});
}
function empty(text) { return node('p', {class:'muted', text}); }

function quantities(data, spec) {
  const grid = node('div', {class:'grid'}, data.map((item, index) => {
    const name = view.names[item.id] || item.id;
    const current = view.data.quantities[item.id] || 0;
    const choices = spec.quantity_options?.[item.id];
    const props = {'aria-label':'Menge ' + name, name:item.id, disabled:!spec.actions.length};
    const field = choices === undefined
      ? node('input', {...props, type:'number', min:0, max:999, value:current})
      : node('select', props, [
          node('option', {value:0, text:'0'}),
          ...choices.map(value => node('option', {value, selected:value === current, text:String(value)}))
        ]);
    const photo = showcase && index < 5 ? node('img', {
      class:'product-photo', src:'/showcase/' + (index + 1) + '.webp', alt:name, width:512, height:512,
      onerror:event => event.currentTarget.hidden = true
    }) : null;
    return node('div', {class:'product'}, [
      photo,
      node('div', {class:'index', 'aria-hidden':'true'}, [
        node('strong', {text:String(index + 1).padStart(2, '0')}),
        node('span', {text:item.rate + ' % MwSt.'})
      ]),
      node('h3', {text:name}),
      node('p', {class:'num', text:money(item.unit)}),
      choices?.length === 0 ? empty('Nicht verfügbar') : node('label', {text:'Menge'}, [field])
    ]);
  }));
  const form = node('form', {}, [grid]);
  for (const action of spec.actions) {
    form.append(node('div', {class:'actions'}, [node('button', {
      type:'submit', 'data-action':action.id, text:actionLabels[action.id]
    })]));
    form.addEventListener('submit', event => {
      event.preventDefault();
      const quantities = Object.fromEntries([...new FormData(form)].map(([key, value]) =>
        [key, Number(value)]).filter(([, value]) => value > 0));
      dispatch(action, {quantities});
    });
  }
  return form;
}
function lines(data) {
  if (!data) return empty('Hier erscheinen die Artikel aus deinem Warenkorb.');
  return node('ul', {class:'list'}, Object.entries(data.items).map(([key, item]) =>
    node('li', {}, [node('span', {text:item.quantity + ' × ' + (view.names[key] || key)}),
      node('strong', {class:'num', text:money(item.gross)})])));
}
function facts(rows) {
  return node('dl', {}, rows.map(([key, value, grand]) => node('div', {class:grand ? 'grand' : ''}, [
    node('dt', {text:key}), node('dd', {text:value})
  ])));
}
function totals(data, spec) {
  if (!data) return empty('Wähle zuerst Artikel und aktualisiere den Warenkorb.');
  const list = [];
  if (data.goods != null) list.push(['Artikel', money(data.goods)]);
  if (data.discount) list.push(['Gutschein', money(data.discount.gross)]);
  if (data.shipping) list.push(['Versand', money(data.shipping.gross)]);
  list.push(['Netto', money(data.net)], ['Brutto', money(data.gross), true]);
  return node('div', {}, [facts(list), node('div', {class:'actions'},
    spec.actions.map(action => actionButton(action, () => ({}))))]);
}
function coupon(data, spec) {
  const action = spec.actions[0];
  if (!action) return empty(data?.discount ? 'Gutschein angewendet.' : 'Kein Gutschein verfügbar.');
  const input = node('input', {name:'code', id:'coupon-code', required:true, maxlength:40});
  return node('form', {onsubmit:event => {
    event.preventDefault();
    dispatch(action, {code:input.value});
  }}, [
    node('label', {for:'coupon-code', text:'Gutscheincode'}),
    node('div', {class:'input-row'}, [input, node('button', {
      class:'secondary', disabled:!data, 'data-action':action.id, text:'Einlösen'
    })]),
    node('p', {class:'muted', text:'Testcode ' + session.demoCode})
  ]);
}
function orders(data, spec) {
  const root = node('div');
  const table = node('table', {'aria-label':'Bestellungen'});
  const body = node('tbody');
  const sortable = spec.controls.includes('sort');
  const searchable = spec.controls.includes('search') || spec.controls.includes('filter');
  const filters = spec.filters ?? (spec.controls.includes('filter') ? ['payment'] : []);
  if (!spec.columns.includes(sorting.key)) sorting = {key:'number', reverse:false};
  function draw() {
    const matches = data.filter(row => (!searchable || row.number.toLowerCase().includes(filter.text.toLowerCase()))
      && filters.every(key => !filter[key] || row[key] === filter[key]));
    if (sortable) matches.sort((a, b) => (typeof a[sorting.key] === 'number' ? a[sorting.key] - b[sorting.key]
      : a[sorting.key].localeCompare(b[sorting.key])) * (sorting.reverse ? -1 : 1));
    body.replaceChildren(...matches.map(row => node('tr', {'aria-selected':String(selected === row.id)},
      spec.columns.map(key => node('td', {'data-number':key === 'gross'}, [
        key === 'number' && spec.controls.includes('select') ?
          node('button', {class:'text-button', type:'button', 'data-order':row.id,
            text:row.number, onclick:() => operation(() => load('admin', row.id))}) :
          document.createTextNode(key === 'gross' ? money(row[key]) : states[row[key]] || row[key])
      ])))));
    if (!matches.length) body.append(node('tr', {}, [
      node('td', {colspan:spec.columns.length, text:'Keine passenden Bestellungen.'})
    ]));
  }
  const tools = [];
  if (searchable) {
    const search = node('input', {type:'search', 'aria-label':'Bestellnummer suchen', placeholder:'Bestellnummer', value:filter.text});
    search.oninput = () => { filter.text = search.value; draw(); };
    tools.push(search);
  }
  const statuses = {payment:['paid', 'Zahlungsstatus filtern', 'Alle Zahlungen'],
    delivery:['shipped', 'Lieferstatus filtern', 'Alle Lieferungen']};
  for (const key of filters) {
    const [closed, label, all] = statuses[key];
    const state = node('select', {'aria-label':label}, [
      node('option', {value:'', text:all}), node('option', {value:'open', text:'Offen'}),
      node('option', {value:closed, text:states[closed]})
    ]);
    state.value = filter[key];
    state.onchange = () => { filter[key] = state.value; draw(); };
    tools.push(state);
  }
  if (tools.length) root.append(node('div', {class:'filter'}, tools));
  const headers = spec.columns.map(key => {
    const th = node('th', {scope:'col'});
    if (!sortable) th.textContent = columns[key];
    else th.append(node('button', {class:'text-button', type:'button', 'data-sort':key,
      text:columns[key], onclick:() => {
        sorting = {key, reverse:sorting.key === key && !sorting.reverse};
        for (const other of th.parentNode.children) other.removeAttribute('aria-sort');
        th.setAttribute('aria-sort', sorting.reverse ? 'descending' : 'ascending');
        draw();
      }}));
    if (sortable && sorting.key === key) th.setAttribute('aria-sort', sorting.reverse ? 'descending' : 'ascending');
    return th;
  });
  table.append(node('thead', {}, [node('tr', {}, headers)]), body);
  root.append(node('div', {class:'table-wrap'}, [table]));
  draw();
  return root;
}
function detail(data, spec) {
  if (!data) return empty('Wähle eine Bestellung, um ihre Artikel und Aktionen zu sehen.');
  return node('div', {}, [lines(data), node('div', {class:'actions'}, spec.actions.map(action =>
    actionButton(action, () => ({row:data.id}))))]);
}
function taxes(data) {
  return data ? facts(data.taxes.map(([rate, , amount]) => [rate + ' %', money(amount)]))
    : empty('Die Steuern erscheinen nach der Berechnung.');
}
function workflow(data) {
  if (!data) return empty('Wähle eine Bestellung.');
  return node('p', {class:'status', 'data-ready':String(data.ready),
    text:data.ready ? 'Workflow markiert · ' + data.records.length + ' Eintrag' : 'Keine Workflow-Markierung'});
}
const components = {
  'quantity-grid':quantities, lines:(data, spec) => spec.source === 'selection' ? detail(data, spec) : lines(data),
  totals, form:coupon, grid:orders, taxes, status:workflow
};

async function activateService() {
  await operation(async () => {
    await api('activate', {session:selected, revision:view.revision, request:crypto.randomUUID()});
    await load('admin', selected);
    notice('Servicespezialist für diese Bestellung aktiviert.');
  });
}

function renderInspector() {
  const inspector = $('#deck-inspector');
  const proof = view.provenance;
  if (!showcase || !proof) {
    inspector.hidden = true;
    return;
  }
  const bound = scope === 'receipt' || view.data.selection;
  const description = bound ? 'Bestellung ' + proof.root.slice(0, 8).toUpperCase() : title[scope];
  const facts = node('div', {class:'deck-facts'}, [
    node('strong', {text:'Holodeck'}),
    node('span', {class:'deck-mode', 'data-service':String(proof.serviceActive),
                  text:proof.serviceActive ? 'Service aktiv' : 'Basisdeck'}),
    node('span', {class:'deck-record', text:description + ' · Revision ' + view.revision}),
    proof.teacherOffline ? node('span', {class:'deck-offline', text:'Shopware aus'}) : null
  ]);
  const actions = node('div', {class:'deck-controls'}, [
    node('details', {}, [
      node('summary', {text:'Projektion ansehen'}),
      node('div', {class:'deck-trace'}, [
        node('p', {text:'Checkpoint ' + proof.checkpoint.slice(0, 12)}),
        node('ul', {}, view.nodes.map(spec => {
          const bindings = spec.actions.map(action => action.id + ' → ' + action.binding);
          return node('li', {text:headings[spec.id] + (bindings.length ? ' · ' + bindings.join(', ') : '')});
        }))
      ])
    ]),
    proof.activationAvailable ? node('button', {type:'button', id:'activate-service', text:'Service aktivieren',
      onclick:activateService}) : null
  ]);
  inspector.replaceChildren(facts, actions);
  inspector.hidden = false;
}

function render() {
  const label = scope === 'storefront' ? session.name : title[scope];
  document.body.dataset.scope = scope;
  $('#heading').textContent = label;
  $('#caption').textContent = scope === 'admin' ? session.name + ' · Bestellungen im Testshop' :
    scope === 'storefront' ? 'Das Sortiment und dein Warenkorb' : session.name + ' · Testshop';
  $('#ask').hidden = !['storefront', 'checkout', 'admin'].includes(scope);
  const request = $('#intent');
  request.value = intent[scope];
  request.scrollTop = 0;
  for (const button of document.querySelectorAll('nav button')) {
    button.removeAttribute('aria-current');
    if (button.dataset.scope === scope) button.setAttribute('aria-current', 'page');
    if (button.dataset.scope === 'checkout') button.disabled = !view.data.cart || !!view.data.receipt;
  }
  const main = node('div', {class:'lane'});
  const side = node('div', {class:'lane'});
  for (const spec of view.nodes) {
    const wide = ['quantity-grid', 'grid'].includes(spec.component)
      || (spec.component === 'lines' && scope === 'checkout');
    (wide ? main : side).append(node('section', {class:'panel', 'data-panel':spec.id}, [
      node('h2', {text:headings[spec.id]}), components[spec.component](view.data[spec.source], spec)
    ]));
  }
  surface.toggleAttribute('data-wide-only', main.hasChildNodes() && !side.hasChildNodes());
  surface.replaceChildren(...[main, side].filter(lane => lane.hasChildNodes()));
  renderInspector();
  if (showcase && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    surface.animate([{opacity:.5, transform:'translateY(5px)'}, {opacity:1, transform:'translateY(0)'}],
                    {duration:240, easing:'cubic-bezier(.16, 1, .3, 1)'});
  }
}
$('#ask').addEventListener('submit', event => {
  event.preventDefault();
  const requested = $('#intent').value.trim();
  if (busy) return;
  intent[scope] = requested;
  if (scope === 'admin') {
    filter = {text:'', payment:'', delivery:''};
    sorting = {key:'number', reverse:false};
  }
  go(scope);
});
for (const button of document.querySelectorAll('nav button')) button.onclick = () => go(button.dataset.scope);

operation(async () => {
  const sessionKey = 'holodeck-session-' + (requestedShop || 'default');
  const resume = sessionStorage.getItem(sessionKey);
  try {
    session = await api('session', {shop:requestedShop, resume});
  } catch (error) {
    if (requestedShop || error.message !== 'None') throw error;
    session = await api('session', {shop:'uferkauf', resume});
  }
  sessionStorage.setItem(sessionKey, session.session);
  const active = session.shop || requestedShop || 'uferkauf';
  const shops = session.shops || [{key:'uferkauf', name:'Uferkauf'}, {key:'heidewerk', name:'Heidewerk'}];
  showcase = Boolean(demoRequested && session.showcase);
  document.body.classList.toggle('showcase', showcase);
  document.documentElement.dataset.shop = active;
  const demoSuffix = showcase ? '&demo=1' : '';
  $('.shop-links').replaceChildren(...shops.map(item => node('a', {
    href:'/?shop=' + encodeURIComponent(item.key) + demoSuffix, text:item.name, 'aria-current':item.key === active ? 'true' : false
  })));
  if (session.adminIntent) intent.admin = session.adminIntent;
  $('#brand').textContent = session.name;
  $('#brand').href = '/?shop=' + encodeURIComponent(active) + demoSuffix;
  document.title = session.name + ' · Holodeck';
  await load('storefront');
});
