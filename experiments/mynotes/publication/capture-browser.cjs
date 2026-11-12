const assert = require('node:assert/strict');
const { writeFileSync } = require('node:fs');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, destination] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const slug = 'browser-publication';
const requests = [];
const views = [];
const bindings = [];
async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/v1/')) requests.push({ method: request.method(), path, body: request.postData() });
    });
    const created = await page.request.post(base + '/api/v1/notes', { data: {
      slug, title: 'Browser publication draft', content: 'Public copy from the browser.',
    } });
    assert.equal(created.status(), 201);
    await page.goto(base + '/notes/' + slug);
    const manage = (name) => page.getByRole('button', { name, exact: true });
    const dialog = page.getByRole('dialog');
    async function record(history, triggerName, dialogName) {
      await manage(triggerName).click();
      const view = page.getByRole('dialog', { name: dialogName });
      await view.waitFor();
      const tree = await view.ariaSnapshot();
      views.push({ history, trigger: triggerName, heading: dialogName,
        controls: [...tree.matchAll(/button "([^"]+)"/gu)].map((match) => match[1]), tree });
    }
    async function clickRequest(name, method, suffix, status) {
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.request().method() === method && new URL(r.url()).pathname.endsWith(suffix)),
        dialog.getByRole('button', { name, exact: true }).click(),
      ]);
      assert.equal(response.status(), status);
      bindings.push({ label: name, method: response.request().method(),
        path: new URL(response.url()).pathname.replace('/' + slug + '/', '/:slug/'),
        mediaType: response.request().headers()['content-type'] ?? null });
    }
    await record([], 'Publish', 'Publish note');
    await clickRequest('Publish', 'PUT', '/publish', 200);
    await dialog.getByRole('button', { name: 'Publish again' }).waitFor();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await record(['publish'], 'Published — manage public page', 'Published note');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await manage('Edit').click();
    await page.getByLabel('Title', { exact: true }).fill('Edited private draft');
    await manage('Save').click();
    await page.getByRole('heading', { name: 'Edited private draft', exact: true }).waitFor();
    await record(['publish', 'edit'], 'Published — manage public page', 'Published note');
    await clickRequest('Publish again', 'PUT', '/publish', 200);
    await clickRequest('Unpublish', 'DELETE', '/publish', 204);
    await record(['publish', 'edit', 'publish', 'unpublish'], 'Publish', 'Publish note');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    assert.equal((await page.request.delete(base + '/api/v1/notes/' + slug)).status(), 204);
    await page.goto(base + '/notes/' + slug);
    await manage('Publish').waitFor({ state: 'detached' });
    views.push({ history: ['publish', 'edit', 'publish', 'unpublish', 'delete'], trigger: null, heading: null, controls: [] });
    writeFileSync(destination, JSON.stringify({ views, requests, bindings }, null, 2) + '\n');
    console.log(`Recorded ${views.length} native publication views and ${requests.length} browser API calls`);
  } finally { await browser.close(); }
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
