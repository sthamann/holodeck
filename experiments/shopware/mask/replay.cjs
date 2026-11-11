// Test the held-out product using the generated Admin and native-compatible API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, heldout, output] = process.argv.slice(2);
const rows = JSON.parse(fs.readFileSync(heldout));

function controls(tree) {
  if (typeof tree === 'string') return 0;
  return Number(['input','textarea','select'].includes(tree[0]))
    + tree[3].reduce((total, child) => total + controls(child), 0);
}

async function inspect(page, record, tab) {
  await page.locator('#view[aria-busy=false][data-tab="' + tab + '"]').waitFor();
  assert(await page.getByText(record.name, {exact:true}).first().isVisible());
  return page.locator('#view input, #view textarea, #view select').count();
}

async function journey(page, row, index) {
  const {record, tabs} = row;
  const route = '#/sw/product/detail/' + record.id + '/';
  const local = base + '/admin' + route;
  await page.goto(local + 'base');
  await inspect(page, record, 'base');
  const results = [];
  for (const tab of tabs) {
    if (tab.key !== 'base') await page.locator('a[href="' + route + tab.key + '"]').first().click();
    const fields = await inspect(page, record, tab.key);
    results.push({key:tab.key, label:tab.label, nativeControls:controls(tab.root), generatedControls:fields});
    if (!index) await page.screenshot({path:path.join(output, tab.key.replaceAll('/','-')+'.png')});
  }
  await page.locator('a[href="' + route + 'base"]').first().click();
  await inspect(page, record, 'base');
  const input = page.locator('[data-stock=editable]');
  assert.equal(await input.count(), 1);
  assert.equal(await page.getByLabel('Name', {exact:true}).inputValue(), record.name);
  assert.equal(await page.getByLabel('Product number', {exact:true}).inputValue(), record.productNumber);
  assert.equal(Number(await input.inputValue()), record.stock);
  const save = page.getByRole('button', {name:'Save', exact:true}).first();
  await input.fill('');
  await save.click();
  await page.getByRole('status').filter({hasText:'Enter a whole stock quantity.'}).waitFor();
  const changed = record.stock + 13;
  await input.fill(String(changed));
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/_action/sync');
  await save.click();
  const synced = await pending;
  assert.equal(synced.status(), 200);
  assert.equal(synced.request().method(), 'POST');
  const patch = synced.request().postDataJSON()[0].payload[0];
  assert.equal(patch.id, record.id);
  assert.equal(patch.stock, changed);
  await page.getByText('Stock saved.', {exact:true}).waitFor();
  await page.reload();
  await inspect(page, record, 'base');
  assert.equal(Number(await input.inputValue()), changed);
  const read = await page.request.post(base + '/store-api/product/' + record.id, {
    headers:{'sw-access-key':'absorption-store'},
    data:{includes:{product_detail:['product','apiAlias'],product:['id','name','stock','productNumber']}},
  });
  assert.equal(read.status(), 200);
  assert.equal((await read.json()).product.stock, changed);
  return {id:record.id, name:record.name, tabs:results, validation:'empty stock refused', before:record.stock, after:changed,
          syncStatus:synced.status(), reloaded:true, storeApiRead:true};
}

async function main() {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  const external = [];
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000},locale:'en-US'});
    page.on('request', request => {if (!request.url().startsWith(base + '/')) external.push(request.url());});
    const report = [];
    for (const [i, row] of rows.entries()) report.push(await journey(page, row, i));
    assert.deepEqual(external, []);
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode=1;});
