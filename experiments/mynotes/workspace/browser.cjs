// Drive the original frontend with its original server switched off.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [origin, screenshot] = process.argv.slice(2);
assert.equal(new URL(origin).hostname, '127.0.0.1');

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(origin);
    const searching = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/v1/notes' && url.searchParams.get('q') === 'fulcrum';
    });
    const search = page.getByRole('searchbox', { name: 'Full-text search' });
    await search.fill('fulcrum');
    assert.equal((await searching).status(), 200);
    await page.getByRole('link', { name: 'Field log revised', exact: true }).first().click();
    await search.fill('');
    await page.getByRole('heading', { name: 'Field log revised', exact: true }).waitFor();
    const outbound = page.getByRole('link', { name: 'Project brief', exact: true }).first();
    await outbound.click();
    await page.getByRole('heading', { name: 'Project brief', exact: true }).waitFor();
    await page.getByRole('link', { name: 'Field log revised', exact: true }).first().click();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Field log browser revision');
    const saved = page.waitForResponse((response) => response.request().method() === 'PATCH'
      && new URL(response.url()).pathname === '/api/v1/notes/field-log');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    const patch = await saved;
    assert.equal(patch.status(), 200);
    assert.ok(patch.request().headers()['if-match']);
    await page.getByRole('heading', { name: 'Field log browser revision', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Split by headings', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Split note by headings' });
    const splitting = page.waitForResponse((response) => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/v1/notes/field-log/split');
    await dialog.getByRole('button', { name: 'Split', exact: true }).click();
    const split = await splitting;
    assert.equal(split.status(), 201);
    const notes = (await split.json()).notes;
    assert.deepEqual(notes.map(({ title }) => title), ['Decisions', 'Follow-up']);
    await page.goto(origin + '/notes/' + notes[0].slug);
    await page.getByRole('heading', { name: 'Decisions', exact: true, level: 1 }).waitFor();
    await page.getByText('A fulcrum result.', { exact: false }).first().waitFor();
    await page.reload();
    await page.getByRole('heading', { name: 'Decisions', exact: true, level: 1 }).waitFor();
    await page.getByRole('button', { name: 'New note', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Browser handoff');
    await page.locator('.cm-content').fill('Follow the [[brief]] after the site visit.');
    const tag = page.getByPlaceholder('Add tag…');
    await tag.fill('review');
    await tag.press('Enter');
    const creating = page.waitForResponse((response) => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/v1/notes');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    const created = await creating;
    assert.equal(created.status(), 201);
    const handoff = await created.json();
    assert.equal(handoff.slug, 'browser-handoff');
    assert.deepEqual(handoff.tags.map(({ slug }) => slug), ['review']);
    assert.deepEqual(handoff.outgoing_links.map(({ slug }) => slug), ['brief']);
    await page.getByRole('heading', { name: 'Browser handoff', exact: true, level: 1 }).waitFor();
    assert.deepEqual(errors, []);
    if (screenshot) await page.screenshot({ path: screenshot });
    console.log('Original UI: full-text search, links, conditional edit, split, reload and tagged note creation passed.');
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
