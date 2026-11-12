const assert = require('node:assert/strict');
const { writeFileSync } = require('node:fs');
const { join } = require('node:path');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [base, output, phase] = process.argv.slice(2);
const slug = 'cedar-handoff';
const notePath = '/api/v1/notes/' + slug;
const publicPath = '/public/notes/' + slug;
const publicBody = 'Collect the keys before Thursday.';
const privateBody = 'Collect the keys before Friday.';
const draftTitle = 'Cedar handoff';
const updatedTitle = 'Cedar handoff revised';

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.HOLODECK_CHROME });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 930 } });
    const reader = await browser.newPage();
    page.setDefaultTimeout(7000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const main = page.getByRole('main');
    const heading = (label) => main.getByRole('heading', { name: label, level: 1, exact: true });
    const link = (label) => main.getByRole('link', { name: label, exact: true });
    const open = (view, key = slug) => page.goto(base + '/?' + new URLSearchParams({ view, slug: key }));
    const control = (label) => main.getByRole('button', { name: label, exact: true });
    const manage = 'Published — manage public page';

    async function submit(label, method, path, status) {
      const [result] = await Promise.all([
        page.waitForResponse((response) => response.request().method() === method && new URL(response.url()).pathname === path),
        control(label).click(),
      ]);
      assert.equal(result.status(), status);
      if (method === 'PUT') assert.equal(result.request().headers()['content-type'], 'text/html');
    }
    async function publicPage(status, title, fragment) {
      const response = await reader.goto(base + publicPath);
      assert.equal(response.status(), status);
      if (title) {
        await reader.getByRole('heading', { name: title, level: 1, exact: true }).waitFor();
        await reader.getByText(fragment, { exact: true }).waitFor();
      }
    }
    async function privateNote() {
      const response = await page.request.get(base + notePath);
      assert.equal(response.status(), 200);
      return response.json();
    }
    async function staleView() {
      await heading('Published note').waitFor();
      await main.getByText('The private draft changed. The public page still shows the last published version.', { exact: true }).waitFor();
      await control('Publish again').waitFor();
      await control('Unpublish').waitFor();
    }

    if (phase === 'draft') {
      await page.goto(base + '/?view=new');
      await heading('New note').waitFor();
      await main.getByLabel('Title', { exact: true }).fill(draftTitle);
      await main.getByLabel('Markdown', { exact: true }).fill(publicBody);
      await submit('Create note', 'POST', '/api/v1/notes', 201);
      await heading(draftTitle).waitFor();
      await publicPage(404);
      await link('Publish').click();
      await heading('Publish note').waitFor();
      await submit('Publish', 'PUT', notePath + '/publish', 200);
      await heading('Published note').waitFor();
      await publicPage(200, draftTitle, publicBody);
      const beforeNoop = await privateNote();
      const noop = await page.request.patch(base + notePath, {
        headers: { 'if-match': '"' + beforeNoop.updated_at + '"' }, data: { title: draftTitle },
      });
      assert.equal(noop.status(), 200);
      assert.deepEqual(await noop.json(), beforeNoop);
      await open('publication');
      await heading('Published note').waitFor();
      assert.equal(await main.getByText(/private draft changed/u).count(), 0);
      await link('Close').click();
      await link('Edit note').click();
      await main.getByLabel('Title', { exact: true }).fill(updatedTitle);
      await main.getByLabel('Markdown', { exact: true }).fill(privateBody);
      await submit('Save changes', 'PATCH', notePath, 200);
      await heading(updatedTitle).waitFor();
      await link(manage).click();
      await staleView();
      await publicPage(200, draftTitle, publicBody);
      assert.equal((await privateNote()).content, privateBody);
      await page.screenshot({ path: join(output, 'draft-out-of-date.png') });
      await reader.screenshot({ path: join(output, 'public-before-republish.png') });
    } else if (phase === 'republish') {
      await open('publication');
      await staleView();
      await publicPage(200, draftTitle, publicBody);
      await submit('Publish again', 'PUT', notePath + '/publish', 200);
      await main.getByText(/private draft changed/u).waitFor({ state: 'detached' });
      await heading('Published note').waitFor();
      assert.equal(await main.getByText(/private draft changed/u).count(), 0);
      await publicPage(200, updatedTitle, privateBody);
      await reader.screenshot({ path: join(output, 'public-after-republish.png') });
      await submit('Unpublish', 'DELETE', notePath + '/publish', 204);
      await heading('Publish note').waitFor();
      await publicPage(404);
      assert.equal(Object.hasOwn(await privateNote(), 'published_at'), false);
      assert.equal(await control('Unpublish').count(), 0);
    } else if (phase === 'withdrawn') {
      await open('note');
      await heading(updatedTitle).waitFor();
      await publicPage(404);
      await link('Publish').click();
      await heading('Publish note').waitFor();
      await submit('Publish', 'PUT', notePath + '/publish', 200);
      await heading('Published note').waitFor();
      await publicPage(200, updatedTitle, privateBody);
      await link('Close').click();
      await link('Delete note').click();
      await submit('Delete note', 'DELETE', notePath, 204);
      await publicPage(404);
      assert.equal((await page.request.get(base + notePath)).status(), 404);
      const recreated = await page.request.post(base + '/api/v1/notes', { data: {
        slug, title: 'Fresh note under the same address', content: '## First check\n\nPrivate again.\n\n## Second check\n\nKeep the keys.',
      } });
      assert.equal(recreated.status(), 201);
      await open('note');
      await heading('Fresh note under the same address').waitFor();
      await link('Publish').waitFor();
      await publicPage(404);
      await link('Split by headings').click();
      await heading('Split by headings').waitFor();
      assert.deepEqual(await main.locator('ol > li').allTextContents(), ['First check', 'Second check']);
      await submit('Create split notes', 'POST', notePath + '/split', 201);
      assert.equal((await page.request.get(base + '/api/v1/notes/first-check')).status(), 200);
      assert.equal((await page.request.get(base + '/api/v1/notes/second-check')).status(), 200);
      await publicPage(404);
    } else throw new Error('Unknown browser phase');

    assert.deepEqual(errors, []);
    writeFileSync(join(output, phase + '.json'), JSON.stringify({ phase, passed: true }) + '\n');
    console.log('Chrome publication phase passed: ' + phase);
  } finally {
    await browser.close();
  }
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
