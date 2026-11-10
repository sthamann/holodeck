// Activate the specialist on a real order while the same local shop is running.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [, , base, reference, output, screenshots = output] = process.argv;
const url = new URL(base);
assert.equal(url.hostname, '127.0.0.1');
const rows = fs.readFileSync(reference, 'utf8').trim().split('\n').map(JSON.parse).filter(row => row.case === 0);
const service = 'Zeige den Zahlungsstatus. Zeige den Versandstatus. '
  + 'Rücksendungen im Händlerbereich abwickeln. Rückzahlungen im Händlerbereich verwalten.';
const pay = 'Zahlung als eingegangen verbuchen. Ich will den Bezahlstatus wieder öffnen.';
const shipping = 'Zeige den Versandstatus. Ich brauche die Artikel des Auftrags. Lass mich die Sendung als verschickt markieren.';
const ids = ['return_full', 'return_partial', 'refund_full', 'refund_partial'];

async function through(page, route, click) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/' + route);
  await click();
  const reply = await response;
  const data = await reply.json();
  assert.equal(reply.status(), 200, JSON.stringify(data));
  await page.locator('#surface[aria-busy=false]').waitFor();
  return data;
}
const action = (page, id) => through(page, 'action', () => page.locator('[data-action=' + id + ']').click());
const present = (page, id) => page.locator('[data-action=' + id + ']').count();
const nav = (page, name) => through(page, 'project', () => page.locator('nav [data-scope=' + name + ']').click());
const select = (page, root) => through(page, 'project', () => page.locator('[data-order="' + root + '"]').click());

async function ask(page, text) {
  await page.getByLabel('Was möchtest du sehen?').fill(text);
  return through(page, 'project', () => page.getByRole('button', {name:'Ansicht erzeugen'}).click());
}

async function post(page, route, body, status) {
  const reply = await page.request.post(new URL('/api/' + route, url).href, {
    headers:{Origin:url.origin}, data:body
  });
  const data = await reply.json();
  assert.equal(reply.status(), status, JSON.stringify(data));
  return data;
}

function exact(reply, native) {
  assert.deepEqual(reply.receipt.workflow_steps.map(step => step.event), [native.event]);
  for (const [key, value] of Object.entries(native.state)) assert.equal(reply.data.selection[key], value);
  assert.equal(reply.data.workflow.records.length, native.markers.both);
}

async function run(browser, row) {
  const page = await browser.newPage({viewport:{width:1440,height:1000}, locale:'de-DE'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(new URL('/?shop=' + row.shop.toLowerCase(), url).href);
    await page.getByRole('heading', {name:row.shop, exact:true}).waitFor();
    for (const item of row.cart_input) {
      await page.locator('input[name="' + item.id + '"]').fill(String(item.quantity));
    }
    const cart = await action(page, 'update');
    assert.deepEqual(cart.data.cart, row.before);
    await page.getByLabel('Gutscheincode').fill(row.shop.slice(0, 4).toUpperCase() + row.case);
    assert.deepEqual((await action(page, 'discount')).data.cart, row.after);
    await nav(page, 'checkout');
    const placed = await action(page, 'place');
    assert.deepEqual(placed.data.receipt, row.order);
    const root = cart.session;

    await nav(page, 'admin');
    await select(page, root);
    await ask(page, pay);
    const paid = await action(page, 'pay');
    exact(paid, row.events[0]);
    const old = await ask(page, service);
    for (const id of ids) assert.equal(await present(page, id), 0);
    await page.screenshot({path:join(screenshots, row.shop.toLowerCase() + '-before-activation.png')});
    const request = {session:root, revision:old.revision, request:'activate-' + root};
    const activated = await post(page, 'activate', request, 200);
    assert.equal(activated.revision, old.revision + 1);
    assert.notEqual(activated.checkpoint_change.from, activated.checkpoint_change.to);
    assert.deepEqual(await post(page, 'activate', request, 200), activated);
    assert.match((await post(page, 'action', {session:root, row:root, scope:'admin', action:'refund_partial',
      projection:old.projection, revision:old.revision, request:'stale-' + root}, 400)).error, /abgelaufen/);

    await page.reload();
    await page.getByRole('heading', {name:row.shop, exact:true}).waitFor();
    await nav(page, 'admin');
    await select(page, root);
    const current = await ask(page, service);
    assert.equal(current.revision, activated.revision);
    assert.equal(current.data.selection.id, root);
    assert.equal(current.data.selection.payment, 'paid');
    assert.equal(current.data.selection.delivery, 'open');
    assert.equal(await present(page, 'refund_partial'), 1);
    assert.equal(await present(page, 'return_full'), 0);
    assert.match((await post(page, 'action', {session:root, row:root, scope:'admin', action:'return_full',
      projection:current.projection, revision:current.revision, request:'premature-return'}, 400)).error, /nicht verfügbar/);
    assert.notEqual(row.events[1].http_status, 200);
    await page.screenshot({path:join(screenshots, row.shop.toLowerCase() + '-after-activation.png')});

    const partialPayment = await action(page, 'refund_partial');
    exact(partialPayment, row.events[2]);
    await ask(page, shipping);
    const shipped = await action(page, 'ship');
    exact(shipped, row.events[3]);
    await ask(page, service);
    await page.screenshot({path:join(screenshots, row.shop.toLowerCase() + '-service.png')});
    const partialReturn = await action(page, 'return_partial');
    exact(partialReturn, row.events[4]);
    const returned = await action(page, 'return_full');
    exact(returned, row.events[5]);
    const refunded = await action(page, 'refund_full');
    exact(refunded, row.events[6]);
    assert.equal(refunded.data.selection.id, root);
    assert.deepEqual(refunded.data.selection.stock, row.order.stock);
    assert.equal(refunded.data.selection.gross, row.order.gross);
    assert.equal(refunded.revision, 10);
    assert.deepEqual(errors, []);
    return {shop:row.shop, root, activation:activated.checkpoint_change, activationRevision:activated.revision,
      finalRevision:refunded.revision, workflowMarks:refunded.data.workflow.records.length,
      checkoutExact:true, staleOfferRejected:true, prematureReturnRejected:true};
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
    for (const row of rows) result.push(await run(browser, row));
    fs.writeFileSync(join(output, 'browser-report.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
