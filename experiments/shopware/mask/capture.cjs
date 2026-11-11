// Record safe browser trees and computed layout from the isolated native teacher.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const [mode, origin, episodes, output, screenshots] = process.argv.slice(2);
assert.equal(new URL(origin).hostname, '127.0.0.1');
const catalog = mode === 'batch' ? [] : JSON.parse(fs.readFileSync(episodes));
const seedRoutes = ['product', 'category', 'manufacturer', 'property', 'order', 'customer', 'rule', 'flow', 'promotion', 'media'];
const routePrefixes = {rule:'settings/rule', promotion:'promotion/v2'};

async function tree(page) {
  return page.evaluate(() => {
    const tokens = [], tokenIds = new Map();
    const css = `display position top right bottom left z-index box-sizing width max-width min-width height min-height max-height
      margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom padding-left
      flex-direction flex-wrap flex-grow flex-shrink flex-basis align-items align-self justify-content gap row-gap column-gap
      grid-template-columns grid-template-rows grid-column grid-row overflow overflow-x overflow-y
      background-color color font-family font-size font-weight font-style line-height letter-spacing white-space
      text-align text-overflow text-decoration-line border-top border-right border-bottom border-left border-radius
      box-shadow opacity object-fit vertical-align`.trim().split(/\s+/);
    const forbidden = new Set(['SCRIPT','STYLE','LINK','NOSCRIPT','IFRAME','OBJECT','EMBED','IMG','VIDEO','AUDIO']);
    const allowed = new Set('DIV SPAN SECTION ARTICLE MAIN HEADER FOOTER NAV ASIDE H1 H2 H3 H4 P UL OL LI A BUTTON LABEL INPUT TEXTAREA SELECT OPTION TABLE TBODY THEAD TR TD TH FORM FIELDSET LEGEND STRONG SMALL B I HR'.split(' '));
    function visit(node) {
      if (node.nodeType === 3) return node.textContent.trim() ? node.textContent : null;
      if (node.nodeType !== 1 || forbidden.has(node.tagName) || node.id?.startsWith('sfwdt') ||
          node.classList.contains('sf-toolbar') || node.classList.contains('sw-notifications')) return null;
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return null;
      const props = Object.fromEntries(css.map(key => [key, style.getPropertyValue(key)]).filter(([,v]) => v && !v.includes('url(')));
      // Fixed positioning in the original viewport should belong to the generated frame.
      if (props.position === 'fixed') props.position = 'absolute';
      const encoded = JSON.stringify(props);
      if (!tokenIds.has(encoded)) { tokenIds.set(encoded, tokens.length); tokens.push(props); }
      const attrs = {};
      for (const name of ['role','aria-label','aria-selected','title','placeholder','type','id','for','colspan','rowspan']) {
        const v = node.getAttribute(name);
        if (v) attrs[name] = v;
      }
      const href = node.getAttribute('href');
      if (href?.startsWith('#/sw/product/detail/')) attrs.route = href;
      if (['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) {
        if (['password','hidden','file'].includes(node.type)) return null;
        attrs.value = node.value;
        if (node.checked) attrs.checked = true;
        const label = Array.from(node.labels || []).map(x => x.innerText.trim()).filter(Boolean)[0];
        if (label) attrs.label = label;
      }
      if (node.disabled || node.getAttribute('aria-disabled') === 'true') attrs.disabled = true;
      const children = Array.from(node.childNodes).map(visit).filter(value => value !== null);
      if (node.tagName.toLowerCase() === 'svg') return null;
      return [allowed.has(node.tagName) ? node.tagName.toLowerCase() : 'div', tokenIds.get(encoded), attrs, children];
    }
    const root = document.querySelector('#app') || document.body;
    return {tokens, root: visit(root)};
  });
}

async function login(page) {
  await page.goto(origin + '/admin');
  await page.getByRole('textbox', {name:/^(Username|Benutzername)$/i}).fill(process.env.LAB_SW_USER || 'admin');
  await page.getByRole('textbox', {name:/^(Password|Passwort)$/i}).fill(process.env.LAB_SW_PASSWORD || 'shopware');
  await page.getByRole('button', {name:/^(Log in|Login|Anmelden)$/i}).click();
  await page.getByText('Administration', {exact:true}).first().waitFor({timeout:45000});
  const cancel = page.getByRole('button', {name:/^(Abbrechen|Cancel)$/}).first();
  if (await cancel.isVisible()) await cancel.click();
}

async function product(page, episode, index) {
  const record = episode.requests.at(-1).after;
  const root = '#/sw/product/detail/' + record.id;
  await page.goto(origin + '/admin' + root + '/base');
  await page.getByText(record.name, {exact:true}).first().waitFor({timeout:40000});
  const tabs = await page.locator('a[href*="/sw/product/detail/"]:visible').evaluateAll((links, root) => links
    .map(x => ({path:x.hash, label:x.innerText.trim()})).filter(x => x.path.startsWith(root + '/') && x.label), root);
  const discovered = [...new Map(tabs.map(x => [x.path, x])).values()];
  if (!discovered.some(t => t.path.endsWith('/base'))) discovered.unshift({path:root + '/base', label:'General'});
  const states = [];
  for (const item of discovered) {
    await page.goto(origin + '/admin' + item.path);
    await page.getByText(record.name, {exact:true}).first().waitFor({timeout:20000});
    await page.waitForLoadState('networkidle', {timeout:12000}).catch(() => {});
    if (item.path.endsWith('/base')) await page.getByLabel('Stock', {exact:true}).waitFor({timeout:25000});
    await page.locator('.sw-loader:visible').first().waitFor({state:'hidden', timeout:12000}).catch(() => {});
    const state = {key:item.path.slice(root.length + 1), label:item.label, ...await tree(page)};
    states.push(state);
    if (screenshots && index === 0) await page.screenshot({path:path.join(screenshots, state.key.replaceAll('/','-')+'.png'),
      style: '.sf-toolbar,.sw-notifications{display:none!important}'});
  }
  return {record, tabs:states};
}

async function batch(page) {
  const rows = [];
  for (const name of (process.env.HOLODECK_AREAS?.split(',') || seedRoutes)) {
    const prefix = routePrefixes[name] || name;
    const route = '#/sw/' + prefix + '/index';
    try {
      await page.goto(origin + '/admin' + route);
      await page.locator('.sw-page__head-area, .sw-page__content').first().waitFor({timeout:5500});
      await page.waitForLoadState('networkidle', {timeout:6500}).catch(() => {});
      await page.locator('.sw-loader:visible').first().waitFor({state:'hidden', timeout:5000}).catch(() => {});
      await page.locator('.sw-data-grid:visible, .sw-empty-state:visible, .sw-media-index__content:visible, .sw-tree:visible, main article:visible, main table:visible')
        .first().waitFor({timeout:9000});
      const actual = new URL(page.url()).hash;
      if (!actual.startsWith('#/sw/' + prefix + '/')) throw new Error('native route did not resolve');
      const text = (await page.locator('main, .sw-page').last().innerText({timeout:3500})).slice(0,2500);
      if (!text.trim()) throw new Error('native content empty');
      rows.push({name, route:actual, tree:await tree(page)});
      console.log('captured', name);
    } catch (error) {
      rows.push({name, route, error:error.message.split('\n')[0]});
      console.log('unmeasured', name);
    }
    fs.writeFileSync(output, JSON.stringify(rows));
  }
}

async function main() {
  if (screenshots) fs.mkdirSync(screenshots, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.HOLODECK_CHROME});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}, locale:'en-US'});
    await login(page);
    if (mode === 'batch') return await batch(page);
    const rows = [];
    for (const [index, item] of catalog.entries()) {
      rows.push(await product(page, item, index));
      fs.writeFileSync(output, JSON.stringify(rows));
      console.log(item.record.name, rows.at(-1).tabs.map(tab => tab.key).join(', '));
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
