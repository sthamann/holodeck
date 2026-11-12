// Record one real MyNotes title edit, including the original client's request.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [origin, destination] = process.argv.slice(2);
assert.equal(new URL(origin).hostname, '127.0.0.1');

(async () => {
  const browser = await chromium.launch({executablePath:process.env.HOLODECK_CHROME});
  try {
    const page = await browser.newPage();
    const created = await page.request.post(origin + '/api/v1/notes', {
      headers:{Origin:origin}, data:{title:'Browser source probe', content:'Synthetic browser body'},
    });
    assert.equal(created.status(), 201);
    const {slug} = await created.json();
    const route = '/api/v1/notes/' + slug;
    const beforeResponse = await page.request.get(origin + route);
    assert.equal(beforeResponse.status(), 200);
    const before = await beforeResponse.json();
    await page.goto(origin + '/notes/' + slug);
    await page.getByRole('button', {name:'Edit', exact:true}).click();
    await page.getByLabel('Title', {exact:true}).fill('Browser probe revised');
    const pending = page.waitForResponse(response =>
      new URL(response.url()).pathname === route && response.request().method() === 'PATCH');
    const start = Date.now();
    await page.getByRole('button', {name:'Save', exact:true}).click();
    const response = await pending;
    const end = Date.now();
    assert.equal(response.status(), 200);
    const request = response.request().postDataJSON();
    const ifMatch = response.request().headers()['if-match'];
    assert.equal(ifMatch, beforeResponse.headers().etag);
    assert.equal(request.content, before.content);
    assert.deepEqual(request.tags, []);
    await page.getByRole('heading', {name:request.title, exact:true}).waitFor();
    const after = await page.request.get(origin + route);
    assert.equal(after.status(), 200);
    const nativeAfter = await after.json();
    assert.deepEqual(nativeAfter, await response.json());
    fs.writeFileSync(destination, JSON.stringify({
      profile:'browser-write', clock_mode:'native', route, request, if_match:ifMatch,
      clock_interval:[start,end], before, native_patch_status:response.status(), native_after:nativeAfter,
    }, null, 2) + '\n');
    console.log('Native browser edit produced conditional PATCH and persisted title.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
