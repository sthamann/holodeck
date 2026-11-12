const assert = require('node:assert/strict');
const { join } = require('node:path');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, destination, mode] = process.argv.slice(2);
const outline = 'Team context.\n\n## Access review\n\nConsult [[cedar-index]].\n\n### Supporting material\n\nCheck the folder.\n\n## Calendar handoff\n\nNotify the team.';

async function verify() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(7000);
    const main = page.locator('main');
    const api = async (path) => {
      const response = await page.request.get(base + '/api/v1' + path);
      assert.equal(response.status(), 200);
      return response.json();
    };
    async function intent(text, slug) {
      const query = new URLSearchParams({ intent: text, ...(slug ? { slug } : {}) });
      const [projection] = await Promise.all([
        page.waitForResponse((response) => new URL(response.url()).pathname === '/holo', { timeout: 35000 }),
        page.goto(base + '/?' + query),
      ]);
      assert.equal(projection.status(), 200);
      await main.waitFor();
    }
    async function submit(method, path, status) {
      const pending = page.waitForResponse((response) =>
        response.request().method() === method && new URL(response.url()).pathname === path);
      await main.locator('form button[type="submit"], form button:not([type])').first().click();
      assert.equal((await pending).status(), status);
    }
    const observe = [];
    page.on('pageerror', (error) => observe.push(error.message));
    if (mode === 'restart') {
      await page.goto(base + '/?view=note&slug=access-review');
      await main.getByText('Access review', { exact: true }).first().waitFor();
      assert.deepEqual((await api('/notes/access-review')).tags, [{ slug: 'audit' }]);
      assert.equal((await api('/notes/cedar-notes')).title, 'Cedar notes revised');
      assert.deepEqual(observe, []);
      console.log('Cedar workspace restored from its SQLite records after restart.');
      return;
    }
    await intent('Show the tags I can use');
    await main.locator('[name="slug"]').fill('audit');
    await submit('POST', '/api/v1/tags', 201);
    assert.deepEqual((await api('/tags')).tags, [{ slug: 'audit', note_count: 0 }]);
    const target = await page.request.post(base + '/api/v1/notes', { data: { title: 'Cedar index' } });
    assert.equal(target.status(), 201);
    await intent('I want to create a new note');
    await main.locator('[name="title"]').fill('Cedar notes');
    await main.locator('[name="content"]').fill(outline);
    await main.locator('[name="tags"][value="audit"]').check();
    await submit('POST', '/api/v1/notes', 201);
    await page.waitForURL('**/?view=note&slug=cedar-notes');
    const initial = await api('/notes/cedar-notes');
    assert.deepEqual(initial.outgoing_links, [{ slug: 'cedar-index', title: 'Cedar index' }]);
    await main.getByRole('link', { name: 'Cedar index', exact: true }).click();
    await page.waitForURL('**/?view=note&slug=cedar-index');
    await main.getByRole('link', { name: 'Cedar notes', exact: true }).click();
    await page.waitForURL('**/?view=note&slug=cedar-notes');
    await intent('Edit the note I have open', 'cedar-notes');
    await main.locator('[name="title"]').fill('Cedar notes revised');
    await submit('PATCH', '/api/v1/notes/cedar-notes', 200);
    await page.waitForURL('**/?view=note&slug=cedar-notes');
    const updated = await api('/notes/cedar-notes');
    assert.equal(updated.title, 'Cedar notes revised');
    assert.deepEqual(updated.tags, [{ slug: 'audit' }]);
    await intent('Break this note into sections at its headings', 'cedar-notes');
    await main.getByText('Access review', { exact: true }).first().waitFor();
    await main.getByText('Calendar handoff', { exact: true }).first().waitFor();
    await main.locator('select[name="tag"]').selectOption('audit');
    await page.screenshot({ path: join(destination, 'cedar-holo.png') });
    await submit('POST', '/api/v1/notes/cedar-notes/split', 201);
    const child = await api('/notes/access-review');
    assert.deepEqual(child.tags, [{ slug: 'audit' }]);
    assert.deepEqual(child.outgoing_links, [{ slug: 'cedar-index', title: 'Cedar index' }]);
    assert.match(child.content, /### Supporting material/u);
    assert.equal((await api('/notes/calendar-handoff')).title, 'Calendar handoff');
    assert.deepEqual(await api('/notes/cedar-notes'), updated);
    assert.deepEqual((await api('/notes/cedar-index')).incoming_links.map(({ slug }) => slug), ['access-review', 'cedar-notes']);
    assert.deepEqual(observe, []);
    console.log('Cedar: four free requests reached real Holo forms, which created, edited, tagged, linked and split persisted notes.');
  } finally { await browser.close(); }
}
verify().catch((error) => { console.error(error); process.exitCode = 1; });
