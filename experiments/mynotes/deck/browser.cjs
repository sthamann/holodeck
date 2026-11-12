const assert = require('node:assert/strict');
const { join } = require('node:path');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, out, mode] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(7_000);
    const errors = [];
    const writes = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (request.method() !== 'GET') writes.push([request.method(), path, request.headers()['if-match']]);
    });
    const main = page.getByRole('main');
    const heading = (name) => main.getByRole('heading', { name, level: 1, exact: true });
    const open = (view) => page.goto(base + '/?' + view);
    const field = (name) => main.getByLabel(name, { exact: true });
    const button = (name) => main.getByRole('button', { name, exact: true });
    const link = (name) => main.getByRole('link', { name, exact: true });
    const get = async (path) => {
      const response = await page.request.get(base + '/api/v1' + path);
      assert.equal(response.status(), 200);
      return [await response.json(), response.headers().etag];
    };
    const save = async (caption, status, method, path) => {
      const pending = page.waitForResponse((response) =>
        new URL(response.url()).pathname === path && response.request().method() === method);
      await button(caption).click();
      const response = await pending;
      assert.equal(response.status(), status);
    };
    if (mode === 'restart') {
      await open('view=note&slug=inspection-2');
      await heading('Inspection').waitFor();
      assert.deepEqual((await get('/notes/inspection-2'))[0].tags, [{ slug: 'archive' }]);
      assert.equal((await get('/notes/site-log'))[0].title, 'Field log for handover');
      assert.deepEqual((await get('/notes/project-brief'))[0].incoming_links.map(({ slug }) => slug),
        ['site-log', 'inspection-2']);
      assert.deepEqual(errors, []);
      console.log('Restart preserved Holo-created notes, split records, tags and backlink graph.');
      return;
    }

    await open('view=tags');
    await heading('Tags').waitFor();
    for (const tag of ['field', 'archive']) {
      await field('Tag name').fill(tag);
      await save('Create tag', 201, 'POST', '/api/v1/tags');
      await link(tag + ' · 0').waitFor();
    }
    async function create(title, content, tag) {
      await page.getByRole('banner').getByRole('link', { name: 'New note' }).click();
      await heading('New note').waitFor();
      await field('Title').fill(title);
      await field('Markdown').fill(content);
      if (tag) await main.getByRole('checkbox', { name: tag }).check();
      await save('Create note', 201, 'POST', '/api/v1/notes');
      await heading(title).waitFor();
      return (await get('/notes/' + new URL(page.url()).searchParams.get('slug')))[0];
    }
    await create('Project brief', 'Return measurements before Friday.');
    await create('Inspection', 'Earlier inspection record.');
    const source = [
      'Site visit with Lina.', '',
      '## Inspection', '', 'Measurements are in the [[project-brief|project brief]].', '',
      '### Measurements', '', 'The beam is 240 cm.', '',
      '```md', '# This is a sample, never a section', '[[inspection]]', '```', '',
      '## Handover', '', 'Send the measurements to Lina.',
    ].join('\n');
    const created = await create('Site log', source, 'field');
    assert.deepEqual(created.outgoing_links, [{ slug: 'project-brief', title: 'Project brief' }]);
    await link('Project brief').click();
    await heading('Project brief').waitFor();
    await link('Site log').click();
    await heading('Site log').waitFor();

    await link('Edit note').click();
    await heading('Edit note').waitFor();
    const [, initialRevision] = await get('/notes/site-log');
    const outside = await page.request.patch(base + '/api/v1/notes/site-log', {
      headers: { 'if-match': initialRevision }, data: { title: 'Site log reviewed' },
    });
    assert.equal(outside.status(), 200);
    await field('Title').fill('Stale browser edit');
    await save('Save changes', 412, 'PATCH', '/api/v1/notes/site-log');
    assert.equal((await get('/notes/site-log'))[0].title, 'Site log reviewed');
    await link('Reload current version').click();
    await field('Markdown').fill(source.replace('[[project-brief|project brief]]', 'the project brief'));
    await main.getByRole('checkbox', { name: 'field' }).uncheck();
    await save('Save changes', 200, 'PATCH', '/api/v1/notes/site-log');
    await heading('Site log reviewed').waitFor();
    assert.deepEqual((await get('/notes/site-log'))[0].tags, []);
    assert.deepEqual((await get('/notes/project-brief'))[0].incoming_links, []);
    await link('Edit note').click();
    await field('Title').fill('Field log for handover');
    await field('Markdown').fill(source);
    await main.getByRole('checkbox', { name: 'archive' }).check();
    await main.getByRole('checkbox', { name: 'field' }).check();
    await save('Save changes', 200, 'PATCH', '/api/v1/notes/site-log');
    await heading('Field log for handover').waitFor();
    const [updated] = await get('/notes/site-log');
    assert.deepEqual(updated.tags, [{ slug: 'archive' }, { slug: 'field' }]);
    assert.deepEqual((await get('/notes/site-log'))[0], updated);

    await link('Split by headings').click();
    await heading('Split by headings').waitFor();
    assert.deepEqual(await main.locator('ol > li').allTextContents(), ['Inspection', 'Handover']);
    await field('Tag the new notes').selectOption('archive');
    await page.screenshot({ path: join(out, 'generated-split.png') });
    await save('Create split notes', 201, 'POST', '/api/v1/notes/site-log/split');
    assert.equal((await get('/notes/handover'))[0].title, 'Handover');
    await heading(updated.title).waitFor();
    const [inspection] = await get('/notes/inspection-2');
    assert.match(inspection.content, /### Measurements/u);
    assert.deepEqual(inspection.outgoing_links, [{ slug: 'project-brief', title: 'Project brief' }]);
    assert.deepEqual(inspection.tags, [{ slug: 'archive' }]);
    assert.equal(inspection.updated_at, updated.updated_at);
    assert.deepEqual((await get('/notes/site-log'))[0], updated);
    await link('Project brief').click();
    await heading('Project brief').waitFor();
    await link('Inspection').click();
    await heading('Inspection').waitFor();
    assert.equal(await main.locator('article').getByRole('link', { name: 'project brief', exact: true }).count(), 1);
    assert.equal(await main.locator('pre').getByRole('link').count(), 0);
    await page.screenshot({ path: join(out, 'generated-note.png') });

    const aside = page.getByRole('complementary');
    await aside.getByLabel('Find notes').fill('measurements');
    await aside.getByLabel('Filter by tag').selectOption('archive');
    await aside.getByRole('button', { name: 'Search' }).click();
    await aside.getByRole('link', { name: /^Handover/u }).waitFor();
    assert.deepEqual((await get('/notes?q=measurements&tag=archive'))[0].notes.map(({ slug }) => slug).sort(),
      ['handover', 'inspection-2', 'site-log']);
    await page.setViewportSize({ width: 390, height: 844 });
    await open('view=note&slug=inspection-2');
    await heading('Inspection').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: join(out, 'generated-note-mobile.png') });
    assert.deepEqual(errors, []);
    assert.deepEqual(writes.map(([method, route]) => [method, route]), [
      ['POST', '/api/v1/tags'], ['POST', '/api/v1/tags'],
      ['POST', '/api/v1/notes'], ['POST', '/api/v1/notes'], ['POST', '/api/v1/notes'],
      ['PATCH', '/api/v1/notes/site-log'], ['PATCH', '/api/v1/notes/site-log'],
      ['PATCH', '/api/v1/notes/site-log'],
      ['POST', '/api/v1/notes/site-log/split'],
    ]);
    assert.equal(writes[5][2], initialRevision);
    assert.notEqual(writes[6][2], initialRevision);
    await open('view=tags');
    await link('Delete field').click();
    await heading('Delete field').waitFor();
    await save('Delete tag', 204, 'DELETE', '/api/v1/tags/field');
    await heading('Tags').waitFor();
    assert.deepEqual((await get('/notes/site-log'))[0].tags, [{ slug: 'archive' }]);
    await open('view=note&slug=inspection');
    await link('Delete note').click();
    await save('Delete note', 204, 'DELETE', '/api/v1/notes/inspection');
    assert.equal((await page.request.get(base + '/api/v1/notes/inspection')).status(), 404);
    assert.deepEqual(errors, []);
    console.log('Generated Holo: create, edit, add and remove tags and links, split, search, deletion and mobile passed.');
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
