const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, destination] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
    const traces = [];
    let network = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/v1/')) {
        network.push({ method: request.method(), route: path, body: request.postData() });
      }
    });
    const tag = await page.request.post(base + '/api/v1/tags', { data: { slug: 'browser-seed' }, headers: { origin: base } });
    assert.equal(tag.status(), 201);
    const seed = await page.request.post(base + '/api/v1/notes', { data: {
      title: 'Browser walk', content: '## Section A\n\nFirst note.\n\n## Section B\n\nSecond note.', tags: ['browser-seed'],
    }, headers: { origin: base } });
    assert.equal(seed.status(), 201);
    const { slug } = await seed.json();
    async function observe(task, action) {
      const before = await page.locator('body').ariaSnapshot();
      network = [];
      await action();
      await page.waitForTimeout(160);
      traces.push({ task, before, after: await page.locator('body').ariaSnapshot(), requests: network });
    }
    await page.goto(base + '/notes/' + slug);
    await page.getByRole('heading', { name: 'Browser walk', exact: true }).waitFor();
    await observe('open edit form', () => page.getByRole('button', { name: 'Edit', exact: true }).click());
    await observe('save edited note', async () => {
      await page.getByLabel('Title', { exact: true }).fill('Browser walk reviewed');
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await page.getByRole('heading', { name: 'Browser walk reviewed', exact: true }).waitFor();
    });
    await observe('open split dialog', () => page.getByRole('button', { name: 'Split by headings', exact: true }).click());
    await observe('apply split', async () => {
      const split = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/split'));
      await page.getByRole('dialog').getByRole('button', { name: 'Split', exact: true }).click();
      assert.equal((await split).status(), 201);
    });
    await observe('open create form', () => page.getByRole('button', { name: 'New note', exact: true }).click());
    await observe('save new note', async () => {
      await page.getByLabel('Title', { exact: true }).fill('Browser-created draft');
      await page.locator('.cm-content').fill('Reference [[browser-walk]].');
      await page.getByPlaceholder('Add tag…').fill('browser-new-tag');
      await page.getByPlaceholder('Add tag…').press('Enter');
      await page.getByRole('button', { name: 'Remove tag browser-new-tag', exact: true }).waitFor();
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await page.getByRole('heading', { name: 'Browser-created draft', exact: true }).waitFor();
    });
    await observe('open tags', () => page.getByRole('tab', { name: 'Tags', exact: true }).click());
    fs.writeFileSync(destination, JSON.stringify(traces, null, 2) + '\n');
    console.log(`Captured ${traces.length} real browser interactions with accessibility trees and requests`);
  } finally { await browser.close(); }
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
