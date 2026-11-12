// Compare the same browser-to-record path under one changed parameter.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, mode] = process.argv.slice(2);
const shallow = mode === 'shallow';
const expected = shallow ? ['Agenda', 'Decisions'] : ['Details'];

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage();
    const source = await page.request.post(base + '/api/v1/notes', {
      data: { slug: 'handoff', title: 'Team handoff',
        content: '## Agenda\n\n### Details\n\nDiscuss risks.\n\n## Decisions\n\nKeep the date.' },
    });
    assert.equal(source.status(), 201);
    await page.goto(base + '/?view=split&slug=handoff');
    const items = page.getByRole('main').locator('ol li');
    await items.first().waitFor();
    assert.deepEqual(await items.allTextContents(), expected);
    const committed = page.waitForResponse((response) => response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/notes/handoff/split');
    await page.getByRole('button', { name: 'Create split notes' }).click();
    assert.equal((await committed).status(), 201);
    const notes = await (await page.request.get(base + '/api/v1/notes?sort=title&order=asc')).json();
    assert.deepEqual(notes.notes.map(({ title }) => title).filter((title) => title !== 'Team handoff'), expected);
    console.log(`${mode}: the same checkpoint selector drove the Holo preview and persisted ${expected.join(', ')}`);
  } finally {
    await browser.close();
  }
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
