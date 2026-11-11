// Native and generated surfaces are exercised by the same product edit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [mode, url, source, output, proof] = process.argv.slice(2);
const base = new URL(url);
assert.equal(base.hostname, '127.0.0.1');
assert(['native', 'replay'].includes(mode));
const rows = JSON.parse(fs.readFileSync(source, 'utf8'));

async function login(page) {
  await page.goto(base.origin + '/admin');
  await page.getByRole('textbox', {name: /^(Username|Benutzername)$/i}).fill(process.env.LAB_SW_USER || 'admin');
  await page.getByRole('textbox', {name: /^(Password|Passwort)$/i}).fill(process.env.LAB_SW_PASSWORD || 'shopware');
  await page.getByRole('button', {name: /^(Log in|Login|Anmelden)$/i}).click();
  await page.waitForURL('**/admin#/sw/dashboard/**');
  await page.getByText('Administration', {exact: true}).first().waitFor({timeout: 40000});
  const dismiss = page.getByRole('button', {name: /^(Abbrechen|Cancel)$/}).first();
  if (await dismiss.isVisible()) await dismiss.click();
}

async function take(page, name) {
  if (!proof) return;
  fs.mkdirSync(proof, {recursive: true});
  await page.screenshot({path: path.join(proof, name + '.png')});
}

async function observe(page, row, index) {
  const record = row.record;
  let stock = 64 + index + (row.split === 'holdout' ? 8 : 0);
  const storefront = '/detail/' + record.id;
  const admin = '/admin#/sw/product/detail/' + record.id + '/base';
  assert.equal((await page.goto(base.origin + storefront)).status(), 200);
  const heading = page.getByRole('heading', {name: record.name, exact: true}).first();
  await heading.waitFor({timeout: 25000});
  const result = [{surface: 'storefront', route: storefront, record, heading: await heading.innerText()}];
  if (!index) await take(page, 'storefront');
  await page.goto(base.origin + admin);
  const title = page.getByText(record.name, {exact: true}).first();
  await title.waitFor({timeout: 25000});
  const field = page.getByLabel('Stock', {exact: true});
  await field.waitFor({timeout: 25000});
  const before = Number(await field.inputValue());
  if (before === stock) stock += 10;
  const pending = page.waitForResponse(response => {
    if (response.request().method() !== 'POST' || new URL(response.url()).pathname !== '/api/_action/sync') return false;
    const operations = response.request().postDataJSON();
    return Object.values(operations).some(operation => operation.payload.some(item => item.id === record.id));
  });
  await field.fill(String(stock));
  await field.press('Tab');
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  if (process.env.HOLODECK_CAPTURE_DEBUG) console.log('after click', before, stock,
    (await page.locator('body').innerText()).split('\n').filter(line => /error|required|saved|success|invalid/i.test(line)));
  const response = await pending;
  assert(response.ok(), `Native-compatible action returned ${response.status()}`);
  const body = response.request().postDataJSON();
  assert.equal(Object.values(body)[0].payload[0].stock, stock);
  await page.reload();
  await field.waitFor({timeout: 25000});
  const persisted = Number(await field.inputValue());
  assert.equal(persisted, stock);
  result.push({surface: 'admin', route: admin, record, heading: await title.innerText(), action: {
    label: 'Stock', button: 'Save', method: response.request().method(), path: new URL(response.url()).pathname,
    body, status: response.status(), before, value: stock, persisted,
  }});
  if (!index) {
    await field.scrollIntoViewIfNeeded();
    await take(page, 'admin');
  }
  return result;
}

async function main() {
  const browser = await chromium.launch({headless: true, executablePath: process.env.HOLODECK_CHROME});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, locale: 'en-US'});
    if (process.env.HOLODECK_CAPTURE_DEBUG) page.on('response', response => {
      const request = response.request();
      if (['POST', 'PATCH', 'PUT'].includes(request.method())) {
        const pathname = new URL(request.url()).pathname;
        console.log(request.method(), pathname, response.status());
        if (pathname === '/api/_action/sync') {
          const payload = request.postDataJSON();
          console.log(JSON.stringify(payload));
        }
      }
    });
    if (mode === 'native') await login(page);
    const observations = [];
    for (const [index, row] of rows.slice(0, 2).entries()) observations.push(...await observe(page, row, index));
    fs.writeFileSync(output, JSON.stringify(observations, null, 2) + '\n');
    console.log(`${mode}: ${observations.length} views and ${observations.length / 2} persisted browser actions`);
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
