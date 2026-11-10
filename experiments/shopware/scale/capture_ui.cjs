// Record a real native Admin save and the corresponding Storefront purchase interaction.
const fs = require('node:fs');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');
const base = new URL(process.env.HOLODECK_TEACHER_URL);
if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1') throw Error('Use an isolated loopback teacher.');
const fixture = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));

async function main() {
  const browser = await chromium.launch({headless:true,
    ...(process.env.HOLODECK_CHROME ? {executablePath:process.env.HOLODECK_CHROME} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    await page.goto(base.origin+'/admin');
    await page.getByRole('textbox',{name:'Benutzername'}).fill(process.env.LAB_SW_USER || 'admin');
    await page.getByRole('textbox',{name:'Passwort'}).fill(process.env.LAB_SW_PASSWORD || 'shopware');
    await page.getByRole('button',{name:'Anmelden'}).click();
    await page.getByRole('navigation',{name:'Main navigation'}).waitFor({timeout:40000});
    const cancel = page.getByRole('button',{name:'Abbrechen',exact:true});
    if (await cancel.count()) await cancel.click();
    const count = Number(process.argv[4] || 1);
    if (!Number.isSafeInteger(count) || count<1) throw Error('Use a positive browser run count.');
    fs.writeFileSync(process.argv[3],'');
    for (let i=0;i<count;i++) {
      const rows = await observe(browser,page,i);
      fs.appendFileSync(process.argv[3],rows.map(row=>JSON.stringify(row)).join('\n')+'\n');
      console.log('Recorded native UI flow',i+1,'of',count);
    }
  } finally {
    await browser.close();
  }
}
async function observe(browser,page,caseIndex) {
  await page.goto(base.origin+'/admin#/sw/product/detail/'+fixture.product);
  const title = page.getByRole('textbox',{name:'Name',exact:true});
  await title.waitFor();
  const referenceName = 'Holodeck Browser Referenz '+Date.now().toString().slice(-5)+' '+caseIndex;
  await title.fill(referenceName);
  const gross = page.locator('input[name=sw-price-field-gross]').first();
  const unit = (42.70 + caseIndex*2.13).toFixed(2);
  await gross.fill(unit);
  await gross.press('Tab');
  const stock = page.locator('#sw-field--product-stock');
  await stock.fill(String(19+caseIndex));
  await stock.press('Tab');
  const pending = page.waitForResponse(r =>
    ['PATCH','POST'].includes(r.request().method()) &&
    (new URL(r.url()).pathname.endsWith('/api/product/'+fixture.product) ||
     (r.url().includes('/api/_action/sync') && (r.request().postData() || '').includes(fixture.product))));
  pending.catch(()=>{});
  await page.getByRole('button',{name:'Save',exact:true}).click();
  const saved = await pending;
  if (!saved.ok()) throw Error('Native product save failed.');
  const shop = await browser.newPage({viewport:{width:1440,height:1000}});
  try {
    await shop.goto(base.origin+'/detail/'+fixture.product);
    await shop.getByRole('heading',{name:referenceName,exact:true}).waitFor();
    await shop.getByText(new RegExp('€\\s*'+unit.replace('.','[.,]'))).first().waitFor();
    const cookies = shop.getByRole('button',{name:/accept only essential|only essential|essential cookies/i});
    if (await cookies.count() && await cookies.first().isVisible()) await cookies.first().click();
    const add = shop.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/checkout/line-item/add'));
    add.catch(()=>{});
    await shop.getByRole('button',{name:'Add to shopping cart',exact:true}).click();
    const line = await add;
    await shop.goto(base.origin+'/checkout/cart');
    await shop.getByText(referenceName,{exact:true}).first().waitFor();
    const field = shop.locator('input[name=code]:visible').first();
    const form = field.locator('xpath=ancestor::form');
    await field.fill(fixture.code);
    const applied = shop.waitForResponse(r=>r.request().method()==='POST' &&
      /\/checkout\/(promotion|line-item)\/add/.test(r.url()));
    applied.catch(()=>{});
    await form.locator('button[type=submit]').click();
    const promotion = await applied;
    await shop.getByText(fixture.code,{exact:false}).first().waitFor();
    await shop.locator('.line-item-promotion').first().waitFor();
    return [
      {surface:'Admin',event:'catalog.edit',fields:['name','gross','stock'],status:saved.status(),
       verified:['same product identifier','changed name and gross price visible in Storefront']},
      {surface:'Storefront',event:'cart.add',status:line.status(),verified:['same edited product in cart']},
      {surface:'Storefront',event:'cart.promo',status:promotion.status(),verified:['native promotion line in cart']},
    ].map(row=>({case:caseIndex,...row}));
  } finally {
    await shop.close();
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
