// Real browser workflow for the joint checkpoint. The reference is test-only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');

const [, , base, reference, output, screenshots = output] = process.argv;
const url = new URL(base);
assert.equal(url.hostname, '127.0.0.1');
const testCase = Number(process.env.HOLODECK_CASE || 5);
const rows = fs.readFileSync(reference, 'utf8').trim().split('\n').map(JSON.parse)
  .filter(row => row.case === testCase);
const control = process.env.HOLODECK_DELIVERY_CONTROL === '1';
const finance = 'Den Stand der Zahlungen sehen. Zahlung als eingegangen verbuchen. Ich will den Bezahlstatus wieder öffnen.';
const shipping = 'Zeige den Versandstatus. Ich möchte nach Lieferzustand filtern. '
  + 'Ich brauche die Artikel des Auftrags. Lass mich die Sendung als verschickt markieren. '
  + 'Ich möchte die Ablaufmarkierung sehen. Welche Umsatzsteuer ist bei den einzelnen Sätzen angefallen?';

async function through(page, route, click) {
  const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/' + route);
  await click();
  const response = await waiting;
  const result = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(result));
  await page.locator('#surface[aria-busy=false]').waitFor();
  return result;
}
const action = (page, id) => through(page, 'action', () => page.locator('[data-action=' + id + ']').click());
const stage = (page, id) => through(page, 'project', () => page.locator('nav [data-scope=' + id + ']').click());
const rowSelector = root => '[data-order="' + root + '"]';
const present = (page, id) => page.locator('[data-action=' + id + ']').count();
const select = (page, root) => through(page, 'project', () => page.locator(rowSelector(root)).click());

async function ask(page, text) {
  await page.getByLabel('Was möchtest du sehen?').fill(text);
  return through(page, 'project', () => page.getByRole('button', {name:'Ansicht erzeugen'}).click());
}

async function rejected(page, path, body, reason) {
  const response = await page.request.post(new URL('/api/' + path, url).href, {
    headers:{Origin:url.origin}, data:body
  });
  assert.equal(response.status(), 400);
  assert.match((await response.json()).error, reason);
}

async function checkout(page, row) {
  await page.goto(new URL('/?shop=' + row.shop.toLowerCase(), url).href);
  await page.getByRole('heading', {name:row.shop, exact:true}).waitFor();
  for (const item of row.cart_input) {
    await page.locator('input[name="' + item.id + '"]').fill(String(item.quantity));
  }
  const cart = await action(page, 'update');
  assert.deepEqual(cart.data.cart, row.before);
  await page.getByLabel('Gutscheincode').fill(row.shop.slice(0, 4).toUpperCase() + row.case);
  const coupon = await action(page, 'discount');
  assert.deepEqual(coupon.data.cart, row.after);
  assert.equal(await present(page, 'discount'), 0);
  await stage(page, 'checkout');
  const placed = await action(page, 'place');
  assert.deepEqual(placed.data.receipt, row.order);
  assert.equal(placed.receipt.revision, 3);
  await page.getByRole('heading', {name:'Bestellung eingegangen'}).waitFor();
  return cart.session;
}

async function journey(browser, row, foreign) {
  const page = await browser.newPage({viewport:{width:1440,height:1000}, locale:'de-DE'});
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', req => {if (new URL(req.url()).origin !== url.origin) external.push(req.url());});
  try {
    const root = await checkout(page, row);
    await page.reload();
    await page.getByRole('heading', {name:row.shop, exact:true}).waitFor();
    assert.equal(await present(page, 'update'), 0);
    const admin = await stage(page, 'admin');
    assert(admin.data.orders.some(order => order.id === root));
    assert(!foreign || !admin.data.orders.some(order => order.id === foreign));
    if (foreign) await rejected(page, 'project', {session:root, scope:'admin', selected:foreign, intent:finance}, /tenant|root/i);
    await select(page, root);
    const first = await ask(page, finance);
    assert.equal(first.data.selection.payment, 'open');
    assert.equal(await present(page, 'pay'), 1);
    assert.equal(await present(page, 'reopen'), 0);

    const paid = await action(page, 'pay');
    assert.equal(paid.data.selection.payment, 'paid');
    assert.equal(await present(page, 'pay'), 0);
    assert.equal(await present(page, 'reopen'), 1);
    await rejected(page, 'action', {session:root, scope:'admin', row:root, action:'pay',
      projection:first.projection, revision:first.revision, request:'stale-offer'}, /abgelaufen/);
    const reopened = await action(page, 'reopen');
    assert.equal(reopened.data.selection.payment, 'open');
    assert.equal(await present(page, 'pay'), 1);
    assert.equal(await present(page, 'reopen'), 0);
    const ship = await ask(page, shipping);
    assert.deepEqual(ship.data.selection.taxes, row.order.taxes);
    assert.equal(await page.locator('[data-panel=order_taxes] dd').count(), row.order.taxes.length);
    assert.equal(await page.getByLabel('Lieferstatus filtern').count(), 1);
    assert.equal(await present(page, 'ship'), control ? 0 : 1);
    if (!control) {
      await rejected(page, 'action', {session:root, scope:'admin', row:foreign || 'wrong',
        action:'ship', projection:ship.projection, revision:ship.revision, request:'wrong-target'}, /anderen Bestellung/);
    } else {
      await rejected(page, 'action', {session:root, scope:'admin', row:root,
        action:'ship', projection:ship.projection, revision:ship.revision, request:'without-program'}, /nicht verfügbar/);
    }
    const final = control ? ship : await action(page, 'ship');
    assert.equal(final.data.selection.delivery, control ? 'open' : 'shipped');
    assert.equal(final.data.workflow.records.length, control ? 0 : 1);
    assert.equal(await present(page, 'ship'), 0);
    const statuses = [paid, reopened, ...(control ? [] : [final])];
    for (let i = 0; i < statuses.length; i++) {
      const step = statuses[i], expected = row.events[i];
      assert.deepEqual(step.receipt.workflow_steps.map(s => s.event), [expected.event]);
      for (const [key, value] of Object.entries(expected.state)) assert.equal(step.data.selection[key], value);
      assert.equal(Number(step.data.workflow.ready), expected.markers.both);
    }
    const screenshot = control ? '-control' : '-admin';
    await page.screenshot({path:join(screenshots, row.shop.toLowerCase() + screenshot + '.png')});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    return {shop:row.shop, root, control, gross:row.order.gross, cartExact:true, checkoutExact:true,
      foreignOrderRejected:Boolean(foreign), staleOfferRejected:true, invalidBindingRejected:true,
      statusTransitionsChecked:statuses.length, finalRevision:final.revision,
      workflowEffects:final.data.workflow.records.length, delivery:final.data.selection.delivery};
  } finally {
    await page.close();
  }
}

async function main() {
  assert.equal(rows.length, 4);
  fs.mkdirSync(output, {recursive:true});
  fs.mkdirSync(screenshots, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  try {
    const result = [];
    for (const row of rows) result.push(await journey(browser, row, result.at(-1)?.root));
    fs.writeFileSync(join(output, control ? 'control-report.json' : 'browser-report.json'),
      JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
