// Browser execution of the collection shop. Only this verifier reads expected results.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');

const [, , base, reference, output, screenshots = output] = process.argv;
const url = new URL(base);
assert.equal(url.hostname, '127.0.0.1');
const rows = fs.readFileSync(reference, 'utf8').trim().split('\n').map(JSON.parse);
const format = new Intl.NumberFormat('de-DE', {style:'currency', currency:'EUR'});
const money = cents => format.format(cents / 100);
const plans = [
  ['uferkauf', 'Uferkauf', 1, 'UFER18', 'Bestellliste mit Positionen, Zahlung und Lieferung, bitte nach Betrag sortierbar.'],
  ['heidewerk', 'Heidewerk', 5, 'HEIDE855', 'Öffne meine Aufträge mit Lieferstatus und Versandaktion.']
];
const taxRequest = ' Ergänze die Mehrwertsteuer je Steuersatz.';
const composeAdmin = process.env.HOLODECK_COMPOSE_ADMIN === '1';

async function postThrough(page, route, click, status = 200) {
  const waiting = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/' + route && response.request().method() === 'POST');
  await click();
  const response = await waiting;
  const result = await response.json();
  assert.equal(response.status(), status, JSON.stringify(result));
  await page.locator('#surface[aria-busy=false]').waitFor();
  return result;
}

async function stage(page, scope) {
  return postThrough(page, 'project', () => page.locator('nav [data-scope=' + scope + ']').click());
}
async function action(page, id, status) {
  return postThrough(page, 'action', () => page.locator('[data-action=' + id + ']').click(), status);
}
async function ask(page, text) {
  await page.getByLabel('Was möchtest du sehen?').fill(text);
  return postThrough(page, 'project', () => page.getByRole('button', {name:'Ansicht erzeugen'}).click());
}
async function shot(page, name, fullPage = true) {
  await page.screenshot({path:join(screenshots, name + '.png'), fullPage});
}
async function fitViewport(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
}
async function extraOrder(browser, slug, sku) {
  const page = await browser.newPage();
  try {
    await page.goto(new URL('/?shop=' + slug, url).href);
    await page.locator('input[name="' + sku + '"]').fill('1');
    await action(page, 'update');
    await stage(page, 'checkout');
    const order = await action(page, 'place');
    await page.getByRole('heading', {name:'Bestellung eingegangen'}).waitFor();
    return {id:order.session, gross:order.data.receipt.gross};
  } finally {
    await page.close();
  }
}

function gridPlan(projection, columns, controls, filters) {
  const grid = projection.nodes.find(node => node.id === 'orders');
  assert.deepEqual(grid.columns, columns);
  assert.deepEqual(grid.controls, controls);
  assert.deepEqual(grid.filters, filters);
}

function actionIds(projection) {
  return projection.nodes.flatMap(node => node.actions.map(action => action.id));
}

async function rejectAction(page, projection, action, row, pattern) {
  const response = await page.request.post(new URL('/api/action', url).href, {
    headers:{Origin:url.origin}, data:{session:projection.session, scope:'admin',
      action, row, projection:projection.projection, revision:projection.revision, request:'negative-control'}
  });
  assert.equal(response.status(), 400);
  assert.match((await response.json()).error, pattern);
}

async function variableAdmin(browser, page, slug, row, orderId) {
  const decoy = await extraOrder(browser, slug, row.cart_input[0].id);
  assert(decoy.gross < row.order.gross);
  await stage(page, 'admin');
  const finance = await ask(page, 'Ich will die Bruttosummen sehen. Mach die Spalten sortierbar. '
    + 'Ein Suchfeld nach Auftragsnummer. Filtere nach Zahlungszustand. '
    + 'Zahlung als eingegangen verbuchen. Ich will den Bezahlstatus wieder öffnen.');
  gridPlan(finance, ['number', 'gross', 'payment'], ['sort', 'select', 'search'], ['payment']);
  assert.deepEqual(actionIds(finance), ['pay', 'reopen']);
  assert.equal(finance.data.orders.length, 2);

  const rowIds = () => page.locator('[data-order]').evaluateAll(nodes => nodes.map(node => node.dataset.order));
  const sort = page.locator('[data-sort=gross]');
  await sort.click();
  assert.deepEqual(await rowIds(), [decoy.id, orderId]);
  await sort.click();
  assert.deepEqual(await rowIds(), [orderId, decoy.id]);
  const order = finance.data.orders.find(order => order.id === orderId);
  await page.getByRole('searchbox', {name:'Bestellnummer suchen'}).fill(order.number);
  assert.deepEqual(await rowIds(), [orderId]);
  await page.getByRole('searchbox', {name:'Bestellnummer suchen'}).fill('');
  const target = page.locator('[data-order="' + orderId + '"]');
  const selected = await postThrough(page, 'project', () => target.click());
  await rejectAction(page, selected, 'ship', orderId, /nicht verfügbar/);
  await rejectAction(page, selected, 'pay', decoy.id, /Bestellung auswählen/);
  await shot(page, slug + '-zahlung');

  const paid = await action(page, 'pay');
  assert.equal(paid.data.selection.id, orderId);
  assert.equal(paid.data.selection.payment, 'paid');
  const paidFilter = page.getByLabel('Zahlungsstatus filtern');
  await paidFilter.selectOption('paid');
  assert.deepEqual(await rowIds(), [orderId]);
  await paidFilter.selectOption('open');
  assert.deepEqual(await rowIds(), [decoy.id]);
  await paidFilter.selectOption('');
  const reopened = await action(page, 'reopen');

  const tax = await ask(page, 'Welche Umsatzsteuer ist bei den einzelnen Sätzen angefallen?');
  gridPlan(tax, ['number'], ['select'], []);
  assert.deepEqual(actionIds(tax), []);
  assert.deepEqual(tax.data.selection.taxes, row.order.taxes);
  assert.equal(await page.locator('[data-panel=order_taxes] dd').count(), row.order.taxes.length);
  await rejectAction(page, tax, 'pay', orderId, /nicht verfügbar/);
  await shot(page, slug + '-steuer');

  const readOnly = await ask(page, 'Die Lieferung soll nur sichtbar sein. Versand darf man hier nicht auslösen.');
  assert.deepEqual(actionIds(readOnly), []);
  assert(readOnly.nodes.find(node => node.id === 'orders').columns.includes('delivery'));

  const shippingRequest = 'Zeige den Versandstatus. Ich möchte nach Lieferzustand filtern. '
    + 'Ich brauche die Artikel des Auftrags. Lass mich die Sendung als verschickt markieren. '
    + 'Ich möchte die Ablaufmarkierung sehen.';
  const shipping = await ask(page, shippingRequest);
  gridPlan(shipping, ['number', 'delivery'], ['select'], ['delivery']);
  assert.deepEqual(actionIds(shipping), ['ship']);
  assert.equal(await page.getByRole('searchbox').count(), 0);
  assert.equal(await page.locator('[data-sort]').count(), 0);
  assert.deepEqual(shipping.data.selection.items, row.order.lines);
  await rejectAction(page, selected, 'pay', orderId, /abgelaufen/);
  const shipped = await action(page, 'ship');
  assert.equal(shipped.data.workflow.records.length, 1);
  assert.equal(shipped.data.workflow.records[0].root, orderId);
  const untouched = shipped.data.orders.find(order => order.id === decoy.id);
  assert.equal(untouched.payment, 'open');
  assert.equal(untouched.delivery, 'open');
  assert.equal(untouched.revision, 2);
  const deliveryFilter = page.getByLabel('Lieferstatus filtern');
  await deliveryFilter.selectOption('shipped');
  assert.deepEqual(await rowIds(), [orderId]);
  await deliveryFilter.selectOption('open');
  assert.deepEqual(await rowIds(), [decoy.id]);
  await deliveryFilter.selectOption('');
  await fitViewport(page);
  await shot(page, slug + '-versand');
  const mobile = await browser.newPage({viewport:{width:390,height:844}});
  try {
    await mobile.goto(new URL('/?shop=' + slug, url).href);
    await mobile.locator('[data-panel=products]').waitFor();
    await stage(mobile, 'admin');
    await ask(mobile, shippingRequest);
    await postThrough(mobile, 'project', () => mobile.locator('[data-order="' + orderId + '"]').click());
    await fitViewport(mobile);
    await shot(mobile, slug + '-versand-mobil', false);
  } finally {
    await mobile.close();
  }
  const results = [paid, reopened, shipped].map((actual, index) => {
    const expected = row.events[index];
    assert.deepEqual(actual.receipt.state_events.map(event => event.kind), [expected.event]);
    for (const [key, value] of Object.entries(expected.state)) assert.equal(actual.data.selection[key], value);
    assert.equal(Number(actual.data.workflow.ready), expected.markers.both);
    return {command:expected.command, revision:actual.revision, event:expected.event,
      workflowRecords:actual.data.workflow.records.length};
  });
  return {order:orderId, decoy:decoy.id, plans:{finance:finance.nodes, tax:tax.nodes, shipping:shipping.nodes},
    twoRowSorting:true, filters:true, readOnlyRequest:true, rejectedUnprojectedAction:true,
    rejectedWrongOrder:true, rejectedStaleView:true, results};
}
async function journey(browser, [slug, shop, id, code, request]) {
  const row = rows.find(row => row.shop === shop && row.case === id);
  const page = await browser.newPage({viewport:{width:1440,height:1000}, locale:'de-DE'});
  const errors = [];
  const external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (new URL(request.url()).origin !== url.origin) external.push(request.url());
  });
  try {
    await page.goto(new URL('/?shop=' + slug, url).href);
    await page.getByRole('heading', {name:shop, exact:true}).waitFor();
    for (const item of row.cart_input) {
      await page.locator('input[name="' + item.id + '"]').fill(String(item.quantity));
    }
    const cart = await action(page, 'update');
    assert.deepEqual(cart.data.cart, row.before);
    assert((await page.locator('[data-panel=cart_totals]').innerText()).includes(money(row.before.gross)));
    await page.getByLabel('Gutscheincode').fill('UNGUELTIG');
    const invalid = await action(page, 'discount', 400);
    assert.match(invalid.error, /ungültig/);
    await page.getByLabel('Gutscheincode').fill(code);
    const promo = await action(page, 'discount');
    assert.deepEqual(promo.data.cart, row.after);
    assert.equal(promo.revision, cart.revision + 1);
    await fitViewport(page);
    await shot(page, slug + '-storefront');

    const checkout = await stage(page, 'checkout');
    assert.deepEqual(checkout.data.cart, row.after);
    await shot(page, slug + '-checkout');
    const placed = await action(page, 'place');
    assert.deepEqual(placed.data.receipt, row.order);
    await page.getByRole('heading', {name:'Bestellung eingegangen'}).waitFor();
    await page.reload();
    await page.getByRole('heading', {name:shop, exact:true}).waitFor();

    if (composeAdmin) {
      const result = await variableAdmin(browser, page, slug, row, cart.session);
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      return {shop, case:id, lines:row.cart_input.length, grossCents:row.order.gross, exact:true, ...result};
    }
    await stage(page, 'admin');
    const plain = await ask(page, request);
    assert(!plain.nodes.some(node => node.id === 'order_taxes'));
    const withTax = await ask(page, request + taxRequest);
    assert(withTax.nodes.some(node => node.id === 'order_taxes'));
    assert.deepEqual(withTax.nodes.filter(node => node.id !== 'order_taxes'), plain.nodes);
    assert.equal(withTax.data.orders.length, 1);
    const order = withTax.data.orders[0];
    assert.equal(order.id, cart.session);
    const sort = page.locator('[data-sort=gross]');
    await sort.click();
    assert.equal(await sort.locator('..').getAttribute('aria-sort'), 'ascending');
    await sort.click();
    assert.equal(await sort.locator('..').getAttribute('aria-sort'), 'descending');
    await page.getByRole('searchbox', {name:'Bestellnummer suchen'}).fill('KEIN TREFFER');
    await page.getByText('Keine passenden Bestellungen.').waitFor();
    await page.getByRole('searchbox', {name:'Bestellnummer suchen'}).fill(order.number);
    const detail = await postThrough(page, 'project', () => page.locator('[data-order="' + order.id + '"]').click());
    assert.deepEqual(detail.data.selection.items, row.order.lines);
    assert.equal(detail.data.selection.id, order.id);
    assert.equal(await page.locator('[data-panel=details] li').count(), Object.keys(row.order.lines).length);
    assert.equal(await page.locator('[data-panel=order_taxes] dd').count(), row.order.taxes.length);
    const results = [];
    for (const [index, id] of ['pay', 'reopen', 'ship'].entries()) {
      const actual = await action(page, id);
      const expected = row.events[index];
      assert.deepEqual(actual.receipt.state_events.map(event => event.kind), [expected.event]);
      for (const [key, value] of Object.entries(expected.state)) assert.equal(actual.data.selection[key], value);
      assert.equal(Number(actual.data.workflow.ready), expected.markers.both);
      results.push({command:expected.command, revision:actual.revision, event:expected.event,
        workflowRecords:actual.data.workflow.records.length, receipt:actual.receipt.event});
    }
    const final = results.at(-1);
    assert.equal(final.workflowRecords, 1);
    assert.equal(await page.locator('[data-ready=true]').count(), 1);
    await page.getByLabel('Zahlungsstatus filtern').selectOption('paid');
    await page.getByText('Keine passenden Bestellungen.').waitFor();
    await page.getByLabel('Zahlungsstatus filtern').selectOption('open');
    await page.locator('[data-order="' + order.id + '"]').waitFor();
    await fitViewport(page);
    await shot(page, slug + '-admin');
    await page.setViewportSize({width:390,height:844});
    await fitViewport(page);
    await page.locator('[data-panel=orders]').scrollIntoViewIfNeeded();
    await shot(page, slug + '-admin-mobile', false);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    return {shop, case:id, lines:row.cart_input.length, rates:row.order.taxes.map(entry => entry[0]),
      promo:row.config.mode, grossCents:row.order.gross, order:order.id, exact:true,
      invalidCouponRejected:true, extraTaxComponent:true, sortDirections:true, textAndStatusFilters:true,
      reloadRetainsOrder:true, outboundBrowserRequests:external, results};
  } finally {
    await page.close();
  }
}
async function main() {
  fs.mkdirSync(output, {recursive:true});
  fs.mkdirSync(screenshots, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  try {
    const results = [];
    for (const plan of plans) results.push(await journey(browser, plan));
    fs.writeFileSync(join(output, composeAdmin ? 'admin-browser-report.json' : 'browser-report.json'), JSON.stringify(results, null, 2) + '\n');
    console.log(JSON.stringify(results));
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
