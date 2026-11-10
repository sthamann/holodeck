// Check the actual native quantity input and the generated bound select.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');

const [, , mode, endpoint, source, output] = process.argv;
const base = new URL(endpoint);
assert.equal(base.hostname, '127.0.0.1');
assert(['native', 'deck'].includes(mode));
const rows = fs.readFileSync(source, 'utf8').trim().split('\n').map(JSON.parse);
const shops = [...new Set(rows.map(row => row.shop))];
assert(rows.every(row => row.split === 'holdout'));
fs.mkdirSync(output, {recursive:true});

const control = row => row.storefront;
function expectedOptions(row) {
  const values = [];
  if (control(row)) for (let q = row.storefront.min; q <= row.storefront.max; q += row.storefront.step) values.push(q);
  return values;
}

async function native(browser) {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  try {
    const result = [];
    for (const row of rows) {
      const reply = await page.goto(new URL(row.storefront_path, base).href, {waitUntil:'domcontentloaded'});
      assert.equal(reply.status(), 200);
      const field = page.locator('input[name="lineItems[' + row.product.id + '][quantity]"]');
      assert.equal(await field.count(), row.storefront ? 1 : 0);
      if (row.storefront) {
        const data = await field.evaluate(input => ({min:Number(input.min), max:Number(input.max),
                                                    step:Number(input.step), value:Number(input.value)}));
        assert.deepEqual(data, row.storefront);
      }
      const options = expectedOptions(row);
      let typed = null;
      if (process.env.HOLODECK_NATIVE_EDIT === '1' && row.storefront) {
        const probe = row.cart.find(event => event.request >= row.storefront.min &&
                                    event.request <= row.storefront.max && !options.includes(event.request));
        if (probe) {
          await page.waitForLoadState('load');
          await field.fill(String(probe.request));
          await field.blur();
          typed = await field.evaluate(input => ({value:Number(input.value), valid:input.checkValidity()}));
          typed = {...typed, request:probe.request, cartQuantity:probe.quantity};
        }
      }
      result.push({shop:row.shop, product:row.product.name, options, ...(typed && {typed})});
    }
    return result;
  } finally {
    await page.close();
  }
}

async function captureResponse(page, route, click) {
  const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/' + route);
  await click();
  const response = await waiting;
  const data = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(data));
  await page.locator('#surface[aria-busy=false]').waitFor();
  return data;
}

async function generated(browser) {
  const result = [];
  for (const shop of shops) {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (new URL(request.url()).origin !== base.origin) external.push(request.url());});
    try {
      const catalog = rows.filter(row => row.shop === shop);
      await page.goto(new URL('/?shop=' + shop.toLowerCase(), base).href);
      await page.getByRole('heading', {name:shop, exact:true}).waitFor();
      for (const row of catalog) {
        const field = page.getByLabel('Menge ' + row.product.name);
        const expected = expectedOptions(row);
        if (expected.length) {
          const values = await field.locator('option').evaluateAll(items => items.map(item => Number(item.value)).slice(1));
          assert.deepEqual(values, expected);
          assert(await field.isEnabled());
        } else {
          assert.equal(await field.count(), 0);
          const card = page.locator('.product').filter({has:page.getByRole('heading', {name:row.product.name, exact:true})});
          assert(await card.getByText('Nicht verfügbar', {exact:true}).isVisible());
        }
      }
      await page.screenshot({path:join(output, shop.toLowerCase() + '-quantities.png'), fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      if (shop === shops[0]) {
        const unavailable = catalog.find(row => !expectedOptions(row).length);
        if (unavailable) {
          const card = page.locator('.product').filter({
            has:page.getByRole('heading', {name:unavailable.product.name, exact:true})
          });
          await card.getByText('Nicht verfügbar', {exact:true}).scrollIntoViewIfNeeded();
        }
        await page.screenshot({path:join(output, 'mobile-quantities.png')});
      }
      await page.setViewportSize({width:1440,height:1000});
      const candidates = catalog.map(row => {
        const quantity = expectedOptions(row)[0];
        const contrast = row.cart.find(event => event.quantity > quantity &&
                                     event.quantity <= row.product.stock && event.quantity !== event.request);
        return {row, quantity, contrast};
      });
      const candidate = candidates.find(item => item.contrast);
      assert(candidate);
      const {row:sample, quantity, contrast} = candidate;
      const id = sample.product.id;
      await page.getByLabel('Menge ' + sample.product.name).selectOption(String(quantity));
      const cart = await captureResponse(page, 'action', () => page.locator('[data-action=update]').click());
      assert.equal(cart.data.cart.items[id].quantity, quantity);

      const response = await page.request.post(new URL('/api/action', base).href, {
        headers:{Origin:base.origin},
        data:{session:cart.session, scope:'storefront', action:'update', projection:cart.projection,
              revision:cart.revision, request:'off-grid-' + id, quantities:{[id]:contrast.request}}
      });
      const applied = await response.json();
      assert.equal(response.status(), 200, JSON.stringify(applied));
      assert.equal(applied.data.cart.items[id].quantity, contrast.quantity);
      assert.equal(applied.data.quantities[id], contrast.quantity);
      const quote = await captureResponse(page, 'project', () => page.locator('nav [data-scope=checkout]').click());
      assert.equal(quote.data.cart.items[id].quantity, contrast.quantity);
      const checkout = await captureResponse(page, 'action', () => page.locator('[data-action=place]').click());
      assert.equal(checkout.data.receipt.lines[id].quantity, contrast.quantity);
      assert.equal(checkout.data.receipt.stock[id], sample.product.stock - contrast.quantity);
      const admin = await captureResponse(page, 'project', () => page.locator('nav [data-scope=admin]').click());
      assert(admin.data.orders.some(row => row.id === cart.session));
      assert.deepEqual(external, []);
      assert.deepEqual(errors, []);
      result.push({shop, productControls:catalog.length, rawRequest:contrast.request,
                   storedQuantity:contrast.quantity, order:cart.session});
    } finally {
      await page.close();
    }
  }
  return result;
}

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  try {
    const result = await (mode === 'native' ? native : generated)(browser);
    fs.writeFileSync(join(output, mode + '-report.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(mode, JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
