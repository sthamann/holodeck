// Drive the generated form, then compare its independent readback to the native oracle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [origin, holdout, directory, mode] = process.argv.slice(2);
const episodes = JSON.parse(fs.readFileSync(holdout));
const read = (page, id) => page.request.get(origin + '/mask?' + new URLSearchParams({product:id, tab:'base'}));
const status = (page, text) => page.getByRole('status').filter({hasText:text}).waitFor();
const sha = value => createHash('sha256').update(value).digest('hex');

async function journey(page, episode, index) {
  const {record, steps} = episode;
  const step = name => steps.find(row => row.action === name);
  const target = tab => origin + '/admin#/sw/product/detail/' + record.id + '/' + tab;
  const expectedPrice = step('price').observed.values.price[0];
  const expectedVariant = step('variant').observed.values;
  const expectedMedia = step('media_upload').observed.values;
  const requests = [];
  const observe = response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/')) requests.push([url.pathname, response.status()]);
  };
  page.on('response', observe);
  if (mode !== 'verify') {
    await page.goto(target('base'));
    await page.locator('#view[data-tab=base][aria-busy=false]').waitFor();
    await page.locator('[data-price=gross]').fill(String(expectedPrice.gross));
    await page.locator('[data-price=net]').fill(String(expectedPrice.net));
    await page.locator('[data-linked]').setChecked(expectedPrice.linked);
    await page.getByRole('button', {name:'Save', exact:true}).first().click();
    await status(page, 'Product saved.');

    await page.locator('a[href$="/variants"]').first().click();
    await page.locator('#view[data-tab=variants][aria-busy=false]').waitFor();
    await page.getByRole('button', {name:'Generate variants', exact:true}).click();
    await page.getByLabel('Variant option').selectOption(episode.options[0].id);
    await page.getByLabel('Variant product number').fill(expectedVariant.productNumber);
    await page.getByLabel('Variant stock').fill(String(expectedVariant.stock));
    await page.getByRole('button', {name:'Create variant', exact:true}).click();
    await status(page, 'Variant created.');
    await page.getByText(expectedVariant.productNumber, {exact:false}).last().waitFor();
    if (!index) await page.screenshot({path:path.join(directory, 'variants.png')});

    await page.locator('a[href$="/base"]').first().click();
    await page.locator('#view[data-tab=base][aria-busy=false]').waitFor();
    await page.locator('input[type=file]').setInputFiles({
      name:expectedMedia.fileName + '.jpg', mimeType:'image/jpeg',
      buffer:fs.readFileSync(path.join(__dirname, '../native/public/assets', episode.image_fixture)),
    });
    await status(page, 'Image uploaded and assigned.');
    assert.deepEqual(requests.map(([route, code]) => [route.replace(/[0-9a-f]{32}/g, '{id}'), code]),
      steps.map(({request, status}) => [request.path.replace(/[0-9a-f]{32}/g, '{id}'), status]));
  }

  await page.goto(target('base'));
  await page.locator('#view[data-tab=base][aria-busy=false]').waitFor();
  assert.equal(Number(await page.locator('[data-price=gross]').inputValue()), expectedPrice.gross);
  assert.equal(Number(await page.locator('[data-price=net]').inputValue()), expectedPrice.net);
  assert.equal(await page.locator('[data-linked]').isChecked(), expectedPrice.linked);
  assert.equal(Number(await page.locator('[data-stock=editable]').inputValue()), record.stock);
  const response = await read(page, record.id);
  assert.equal(response.status(), 200);
  const {product} = await response.json();
  assert.deepEqual(product.price, [expectedPrice]);
  assert.equal(product.variants.length, 1);
  const {id, ...variant} = product.variants[0];
  assert.deepEqual(variant, expectedVariant);
  assert.equal(product.media.length, 1);
  const [media] = product.media;
  assert.equal(media.productId, record.id);
  assert.equal(product.coverId, media.id);
  assert.deepEqual(media.file, expectedMedia);
  const image = await page.request.get(origin + '/mask-media/' + media.mediaId);
  assert.equal(image.status(), 200);
  assert.equal(sha(await image.body()), episode.image_sha256);
  assert(await page.getByRole('img', {name:expectedMedia.fileName}).evaluate(async image => {
    await image.decode();
    return image.naturalWidth > 0;
  }));
  if (!index && mode !== 'verify') {
    await page.screenshot({path:path.join(directory, 'base.png')});
    const preview = page.getByRole('img', {name:expectedMedia.fileName});
    await preview.scrollIntoViewIfNeeded();
    assert(await preview.evaluate(image => {
      const bounds = image.getBoundingClientRect();
      return document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) === image;
    }), 'The decoded image must be visibly rendered in the media card');
    await page.screenshot({path:path.join(directory, 'media.png')});
  }
  page.off('response', observe);
  return {product:record.name, price:[expectedPrice.gross, expectedPrice.net], variant:expectedVariant.productNumber,
    stock:expectedVariant.stock, imageSha256:episode.image_sha256, cover:true, requests:requests.length,
    actionStatusParity:mode !== 'verify', verifiedAfterRestart:mode === 'verify'};
}

(async () => {
  fs.mkdirSync(directory, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}, locale:'en-US'});
    const external = [];
    page.on('request', request => { if (!request.url().startsWith(origin + '/')) external.push(request.url()); });
    const results = [];
    for (const [index, episode] of episodes.entries()) results.push(await journey(page, episode, index));
    assert.deepEqual(external, []);
    const report = {results, externalRequests:external};
    fs.writeFileSync(path.join(directory, mode === 'verify' ? 'restart.json' : 'report.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
