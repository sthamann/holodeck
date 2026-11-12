const assert = require('node:assert/strict');
const { readFileSync, writeFileSync } = require('node:fs');
const { join } = require('node:path');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, nativeTrace, output, mode] = process.argv.slice(2);
const native = JSON.parse(readFileSync(nativeTrace, 'utf8'));
const expected = (workspace, label) => native.find((row) => row.workspace === workspace && row.label === label).result;
const cedar = expected('cedar', 'versioned split');
const river = expected('river', 'versioned split');
assert.equal(cedar.status, 201);

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(7000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const main = page.getByRole('main');
    const heading = (title) => main.getByRole('heading', { name: title, level: 1, exact: true });
    const link = (name) => main.getByRole('link', { name, exact: true });
    const note = async (slug) => {
      const response = await page.request.get(base + '/api/v1/notes/' + slug);
      assert.equal(response.status(), 200);
      return response.json();
    };
    async function submit(caption, method, path, status) {
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.request().method() === method && new URL(r.url()).pathname === path),
        main.getByRole('button', { name: caption, exact: true }).click(),
      ]);
      assert.equal(response.status(), status);
    }
    const open = (view, slug) => page.goto(base + '/?' + new URLSearchParams({ view, slug }));
    const children = cedar.body.notes.map(({ slug }) => slug);
    const checkChildren = async () => {
      for (const item of cedar.body.notes) {
        const actual = await note(item.slug);
        const prior = expected('cedar', 'source unchanged').body;
        const source = await note(prior.slug);
        assert.equal(actual.title, item.title);
        assert.deepEqual(actual.tags, item.tags);
        assert.equal(actual.created_at, source.created_at);
        assert.equal(actual.updated_at, source.updated_at);
      }
    };
    if (mode === 'restart') {
      await open('note', children[0]);
      await heading(cedar.body.notes[0].title).waitFor();
      await checkChildren();
      assert.equal((await note('audit-draft')).title, 'Audit draft revised');
      assert.deepEqual(errors, []);
      console.log('Browser write and versioned children restored after restarting the same artifact.');
      return;
    }

    // Capture both projections before this browser has made a single write.
    await open('split', 'cedar-desk');
    await heading('Split by headings').waitFor();
    const initialPreview = await main.locator('ol > li').allTextContents();
    assert.deepEqual(initialPreview, cedar.body.notes.map(({ title }) => title));
    const inherited = cedar.body.notes[0].tags.some(({ slug }) => slug === 'cedar-source');
    assert.equal(await main.getByText('New notes inherit the source tags.', { exact: true }).count(), inherited ? 1 : 0);
    await main.getByLabel('Tag the new notes', { exact: true }).selectOption('cedar-review');
    await page.screenshot({ path: join(output, 'cedar-preview.png') });
    await open('note', 'river-desk');
    await heading('river desk revised').waitFor();
    assert.equal(await link('Split by headings').count(), river.status === 201 ? 1 : 0);
    await page.screenshot({ path: join(output, 'river-actions.png') });

    await open('note', 'cedar-desk');
    await heading('cedar desk revised').waitFor();
    await link('cedar index').click();
    await heading('cedar index').waitFor();
    await link('cedar desk revised').click();
    await heading('cedar desk revised').waitFor();

    await page.getByRole('banner').getByRole('link', { name: 'New note' }).click();
    await main.getByLabel('Title', { exact: true }).fill('Audit draft');
    await main.getByLabel('Markdown', { exact: true }).fill('Check the [[cedar-index]] before Friday.');
    await main.getByRole('checkbox', { name: 'cedar-source', exact: true }).check();
    await submit('Create note', 'POST', '/api/v1/notes', 201);
    await heading('Audit draft').waitFor();
    await link('Edit note').click();
    await main.getByLabel('Title', { exact: true }).fill('Audit draft revised');
    await submit('Save changes', 'PATCH', '/api/v1/notes/audit-draft', 200);
    await heading('Audit draft revised').waitFor();
    const draft = await note('audit-draft');
    assert.deepEqual(draft.outgoing_links, [{ slug: 'cedar-index', title: 'cedar index' }]);
    assert.deepEqual(draft.tags, [{ slug: 'cedar-source' }]);

    await open('note', 'river-desk');
    await heading('river desk revised').waitFor();
    assert.equal(await link('Split by headings').count(), river.status === 201 ? 1 : 0);
    const splitRiver = await page.request.post(base + '/api/v1/notes/river-desk/split', {
      data: { tag: 'river-review' },
    });
    assert.equal(splitRiver.status(), river.status);
    const actualRiver = await splitRiver.json();
    if (river.status === 400) assert.deepEqual(actualRiver, river.body);
    else assert.deepEqual(actualRiver.notes.map(({ slug }) => slug), river.body.notes.map(({ slug }) => slug));

    await open('note', 'cedar-desk');
    await link('Split by headings').click();
    await heading('Split by headings').waitFor();
    const preview = await main.locator('ol > li').allTextContents();
    assert.deepEqual(preview, initialPreview);
    await main.getByLabel('Tag the new notes', { exact: true }).selectOption('cedar-review');
    const before = await note('cedar-desk');
    await submit('Create split notes', 'POST', '/api/v1/notes/cedar-desk/split', 201);
    await heading('cedar desk revised').waitFor();
    await checkChildren();
    assert.deepEqual(await note('cedar-desk'), before);
    assert.deepEqual(errors, []);
    writeFileSync(join(output, 'browser.json'), JSON.stringify({
      draft: draft.slug, river: { exposed: river.status === 201, status: river.status }, preview, inherited,
      children: cedar.body.notes.map(({ slug, tags }) => ({ slug, tags })),
    }, null, 2) + '\n');
    console.log('Chrome verified notes, links, tags, changed action availability and persisted split preview.');
  } finally {
    await browser.close();
  }
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
