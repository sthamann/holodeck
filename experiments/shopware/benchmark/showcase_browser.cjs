// Capture the actual local model, action bindings and order revisions.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const {performance} = require('node:perf_hooks');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');

const [, , endpoint, reference, destination, mode] = process.argv;
const base = new URL(endpoint);
assert.equal(base.hostname, '127.0.0.1');
const rows = fs.readFileSync(reference, 'utf8').trim().split('\n').map(JSON.parse);
const record = mode === 'record';
const size = {width:1440,height:900};
const pay = 'Zahlung als eingegangen verbuchen. Ich will den Bezahlstatus wieder öffnen.';
const service = 'Zeige den Zahlungsstatus. Zeige den Versandstatus. Rücksendungen im Händlerbereich abwickeln. Rückzahlungen im Händlerbereich verwalten.';
const shipping = 'Zeige den Versandstatus. Ich möchte nach Lieferzustand filtern. Ich brauche die Artikel des Auftrags. Lass mich die Sendung als verschickt markieren.';
const serviceActions = ['return_full', 'return_partial', 'refund_full', 'refund_partial'];

async function cursor(page) {
  if (!record) return;
  await page.evaluate(() => {
    const marker = document.createElement('div');
    Object.assign(marker.style, {position:'fixed',left:'0',top:'0',zIndex:'9999',
      pointerEvents:'none',willChange:'transform',filter:'drop-shadow(1px 2px 2px #0006)'});
    marker.innerHTML = '<svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">' +
      '<path d="M3 2 21 15 13 16 9 23Z" fill="#fffdf1" stroke="#152a24" stroke-width="1.5"/></svg>';
    document.body.append(marker);
    document.addEventListener('pointermove', event => {
      marker.style.transform = 'translate(' + event.clientX + 'px,' + event.clientY + 'px)';
    });
    document.addEventListener('pointerdown', event => {
      const ring = document.createElement('span');
      Object.assign(ring.style, {position:'fixed',width:'30px',height:'30px',left:event.clientX-15+'px',
        top:event.clientY-15+'px',border:'2px solid #c4e38d',borderRadius:'50%',zIndex:'9998',pointerEvents:'none'});
      document.body.append(ring);
      ring.animate([{opacity:1,transform:'scale(.55)'},{opacity:0,transform:'scale(1.65)'}],
        {duration:400}).finished.then(() => ring.remove());
    });
  });
}

async function run(browser, row, name, options = {}) {
  const clock = performance.now();
  const chapter = {};
  const mark = label => chapter[label] = Number(((performance.now() - clock) / 1000).toFixed(3));
  const context = await browser.newContext({
    viewport:options.mobile ? {width:390,height:844} : size, locale:'de-DE',
    ...(record && {recordVideo:{dir:destination,size}}),
  });
  const page = await context.newPage();
  const issues = [];
  page.on('pageerror', error => issues.push(error.message));
  page.on('request', request => {
    if (new URL(request.url()).origin !== base.origin) issues.push(request.url());
  });
  page.on('response', response => {
    if (response.status() >= 400 && !response.url().includes('/api/action')) issues.push(response.status()+' '+response.url());
  });
  const pause = (ms=1000) => record ? page.waitForTimeout(ms) : Promise.resolve();
  const action = id => page.locator('[data-action="' + id + '"]');
  const nav = scope => page.locator('nav [data-scope="' + scope + '"]');

  async function point(target) {
    await target.waitFor({state:'visible'});
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    assert(box, 'Missing visible target');
    await page.mouse.move(box.x + box.width/2, box.y + box.height/2, {steps:record ? 9 : 1});
    await pause(130);
  }
  async function click(target) {
    await point(target);
    await target.click();
  }
  async function waitFor(route, use) {
    const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/' + route);
    await use();
    const reply = await response;
    const data = await reply.json();
    assert.equal(reply.status(), 200, JSON.stringify(data));
    await page.locator('#surface[aria-busy=false]').waitFor();
    await pause(260);
    return data;
  }
  const doAction = id => waitFor('action', () => click(action(id)));
  const navigate = scope => waitFor('project', () => click(nav(scope)));
  async function ask(request, slow = false) {
    const field = page.getByLabel('Was möchtest du sehen?');
    await point(field);
    await field.fill('');
    if (slow && record) await field.pressSequentially(request, {delay:14});
    else await field.fill(request);
    await pause(320);
    return waitFor('project', () => click(page.getByRole('button', {name:'Ansicht erzeugen'})));
  }
  async function home() {
    await page.evaluate(() => window.scrollTo(0,0));
    await pause(400);
  }
  function saved(reply, index) {
    const expected = row.events[index];
    assert.deepEqual(reply.receipt.workflow_steps.map(step => step.event), [expected.event]);
    for (const [key, value] of Object.entries(expected.state)) assert.equal(reply.data.selection[key], value);
    assert.equal(reply.data.workflow.records.length, expected.markers.both);
  }

  let result;
  const video = page.video();
  try {
    await page.goto(new URL('/?shop=' + row.shop.toLowerCase() + '&demo=1', base).href);
    await page.getByRole('heading', {name:row.shop, exact:true}).waitFor();
    await page.locator('#surface[aria-busy=false]').waitFor();
    await page.locator('.product-photo').first().waitFor();
    await page.locator('.product-photo').evaluateAll(images => Promise.all(images.map(image => image.decode())));
    assert(await page.getByText('Shopware aus', {exact:true}).isVisible());
    await cursor(page);
    if (options.mobile) {
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path:join(destination,'mobile.png'),fullPage:true});
      return {mobile:true};
    }
    mark('store');
    await page.screenshot({path:join(destination, name + '-storefront.png')});
    await pause(2300);
    for (const item of row.cart_input) {
      const field = page.locator('input[name="' + item.id + '"]');
      await point(field);
      await field.fill(String(item.quantity));
      await pause(160);
    }
    const cart = await doAction('update');
    assert.deepEqual(cart.data.cart, row.before);
    const root = cart.session;
    const coupon = page.getByLabel('Gutscheincode');
    await point(coupon);
    await coupon.fill(row.shop.slice(0,4).toUpperCase()+row.case);
    await pause(450);
    assert.deepEqual((await doAction('discount')).data.cart, row.after);
    await home();
    mark('checkout');
    const checkout = await navigate('checkout');
    assert.equal(checkout.data.cart.gross, row.order.gross);
    await page.screenshot({path:join(destination,name+'-checkout.png')});
    await pause(2100);
    const placed = await doAction('place');
    assert.deepEqual(placed.data.receipt, row.order);
    assert.equal(placed.provenance.root, root);
    await home();
    mark('receipt');
    await page.screenshot({path:join(destination,name+'-receipt.png')});
    await pause(1700);
    await navigate('admin');
    await waitFor('project', () => click(page.locator('[data-order="' + root + '"]')));
    mark('admin');
    await ask(pay, true);
    saved(await doAction('pay'), 0);
    mark('prompt');
    const old = await ask(service, true);
    assert.equal(old.provenance.root, root);
    assert.equal(old.provenance.serviceActive, false);
    for (const id of serviceActions) assert.equal(await action(id).count(), 0);
    await home();
    mark('before');
    await page.screenshot({path:join(destination,name+'-before.png')});
    await pause(2100);
    mark('activate');
    const activated = await waitFor('project', () => click(page.getByRole('button',{name:'Service aktivieren'})));
    assert.equal(activated.revision, old.revision+1);
    assert.equal(activated.provenance.root, root);
    assert.equal(activated.provenance.serviceActive, true);
    assert.notEqual(activated.provenance.checkpoint, old.provenance.checkpoint);
    assert.equal(await action('refund_partial').count(), 1);
    assert.equal(await action('return_full').count(), 0);
    const denied = await page.request.post(new URL('/api/action',base).href, {headers:{Origin:base.origin},
      data:{session:root,row:root,scope:'admin',action:'return_full',projection:activated.projection,
        revision:activated.revision,request:'premature-'+root}});
    assert.equal(denied.status(), 400);
    assert.match((await denied.json()).error,/nicht verfügbar/);
    await page.screenshot({path:join(destination,name+'-after.png')});
    await pause(3100);
    mark('dispatch');
    saved(await doAction('refund_partial'), 2);
    await ask(shipping);
    const filter = page.getByLabel('Lieferstatus filtern');
    await filter.waitFor();
    await page.screenshot({path:join(destination,name+'-shipping.png')});
    saved(await doAction('ship'), 3);
    await ask(service);
    assert.equal(await action('return_full').count(), 1);
    mark('returns');
    await page.screenshot({path:join(destination,name+'-returns.png')});
    await pause(2100);
    saved(await doAction('return_partial'), 4);
    saved(await doAction('return_full'), 5);
    const final = await doAction('refund_full');
    saved(final, 6);
    assert.equal(final.data.selection.id, root);
    assert.equal(final.data.selection.gross, row.order.gross);
    assert.equal(final.revision, 10);
    await home();
    mark('final');
    await page.screenshot({path:join(destination,name+'-final.png')});
    await pause(2600);
    if (record) {
      mark('inspect');
      await click(page.getByText('Projektion ansehen', {exact:true}));
      await page.getByText('Checkpoint ' + final.provenance.checkpoint.slice(0,12)).waitFor();
      await pause(2300);
      mark('switch');
      await click(page.getByRole('link', {name:'Sandfracht',exact:true}));
      await page.getByRole('heading', {name:'Sandfracht',exact:true}).waitFor();
      await page.locator('#surface[aria-busy=false]').waitFor();
      await cursor(page);
      assert(await page.getByText('Basisdeck',{exact:true}).isVisible());
      await pause(2400);
    }
    mark('end');
    assert.deepEqual(issues, []);
    result = {shop:row.shop,root,orderNumber:root.slice(0,8).toUpperCase(),total:row.order.gross,
      teacherOffline:final.provenance.teacherOffline,beforeRevision:old.revision,
      activatedRevision:activated.revision,finalRevision:final.revision,base:old.provenance.checkpoint,
      service:final.provenance.checkpoint,finalDelivery:final.data.selection.delivery,
      finalPayment:final.data.selection.payment,nativeCheckoutExact:true,earlyReturnRejected:true,chapter};
  } finally {
    await context.close();
  }
  if (video) await video.saveAs(join(destination,name+'.webm'));
  return result;
}

async function main() {
  fs.mkdirSync(destination,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:process.env.HOLODECK_CHROME});
  try {
    const row = name => rows.find(r => r.shop === name && r.case === 0);
    const main = await run(browser,row('Birkenkai'),'birkenkai');
    const second = record ? null : await run(browser,row('Sandfracht'),'sandfracht');
    if (!record) await run(browser,row('Birkenkai'),'mobile',{mobile:true});
    fs.writeFileSync(join(destination,'evidence.json'),JSON.stringify({main,second},null,2)+'\n');
    console.log(JSON.stringify({main,second}));
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error);process.exitCode=1;});
