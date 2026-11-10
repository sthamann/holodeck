// Reproducible recorder for the isolated native Shopware Admin browser.
const fs = require('node:fs');
const { chromium } = require(process.env.HOLODECK_PLAYWRIGHT || 'playwright');

const base = new URL(process.env.HOLODECK_TEACHER_URL);
if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1') throw Error('A loopback teacher is required');
const fixture = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const output = process.argv[3];
const proof = process.env.HOLODECK_UI_PROOF;
const observations = [];

function waitSearch(page, entity, condition) {
  return page.waitForResponse(response => {
    if (!response.url().endsWith('/api/search/' + entity) || response.request().method() !== 'POST') return false;
    try { return condition(response.request().postDataJSON()); } catch { return false; }
  });
}
async function search(page, entity, box, term) {
  const pending = waitSearch(page,entity,body => body.term === term || JSON.stringify(body.query || []).includes(term));
  await box.fill(term);
  await box.press('Enter');
  const response = await pending;
  if (!response.ok()) throw Error('Native search failed');
  return response.status();
}
async function sort(page, entity, caption) {
  const header = page.getByRole('columnheader',{name:new RegExp('^'+caption+'(?: Column actions)?$')});
  const initial = new URL(page.url()).hash;
  const expected = entity==='product' ? 'stock' : 'amountTotal';
  const pending = waitSearch(page,entity,body => body.sort?.[0]?.field===expected && body.sort[0].order==='ASC');
  pending.catch(()=>{});
  await header.waitFor();
  const route = initial.split('?')[0];
  const params = new URLSearchParams(initial.split('?')[1] || '');
  params.set('sortBy',expected);
  params.set('sortDirection','ASC');
  await page.goto(base.origin+'/admin'+route+'?'+params);
  const response = await pending;
  const body = response.request().postDataJSON();
  if (!response.ok() || new URL(page.url()).hash === initial) throw Error('Native sort did not change');
  return {label:caption,property:body.sort[0].field,direction:body.sort[0].order,status:response.status(),input:'native URL sort state'};
}
async function grid(page, source, entity, route, searchName, term, sortName) {
  await page.goto(base.origin+'/admin'+route);
  const table = page.getByRole('main').getByRole('table').first();
  await table.getByRole('row').nth(1).waitFor();
  await table.getByRole('columnheader',{name:new RegExp('^'+sortName+'(?: Column actions)?$')}).waitFor();
  const headers = await table.locator('.sw-data-grid__cell--header:visible').evaluateAll(nodes => nodes
    .map(node => ({label:node.innerText.trim(),sortable:node.classList.contains('sw-data-grid__cell--sortable')}))
    .filter(node => node.label));
  if (!headers.some(header=>header.label===sortName)) throw Error('Native grid columns were not visible');
  observations.push({source,kind:'grid',role:'table',headers,route});
  observations.push({source,kind:'sort',...await sort(page,entity,sortName)});
  const box = page.getByRole('searchbox',{name:searchName});
  observations.push({source,kind:'search',label:searchName,status:await search(page,entity,box,term)});
  if (source==='orders') await table.getByRole('link',{name:term,exact:true}).waitFor();
  else await table.getByText(term,{exact:true}).waitFor();
  if (source==='orders') {
    const panel = page.locator('.sw-sidebar');
    for (const button of await page.locator('.sw-notifications button[aria-label=Close]').all()) {
      if (await button.isVisible()) await button.click();
    }
    await page.getByRole('button',{name:'Filters',exact:true}).click();
    await panel.getByRole('heading',{name:'Filters',exact:true}).waitFor();
    const labels = ['Order status','Payment status','Delivery status'];
    await panel.getByText(/Payment status|Delivery status|Order status/i).first().waitFor({timeout:4000}).catch(()=>{});
    const panelText = await panel.innerText();
    observations.push({source,kind:'filter',labels:labels.filter(label=>panelText.includes(label))});
    await panel.getByRole('button',{name:/ButtonClose/}).click();
  }
  return table;
}

async function main() {
  const browser = await chromium.launch({headless:true,
    ...(process.env.HOLODECK_CHROME ? {executablePath:process.env.HOLODECK_CHROME} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    if (process.env.HOLODECK_CAPTURE_DEBUG) page.on('request', r => {
      if (/\/api\/search\/(product|order)$/.test(r.url()) && r.method()==='POST') {
        const body = r.postDataJSON();
        console.log('native-query',r.url().split('/').at(-1),JSON.stringify(body.sort), Boolean(body.term));
      }
    });
    await page.goto(base.origin+'/admin');
    await page.getByRole('textbox',{name:'Benutzername'}).fill(process.env.LAB_SW_USER || 'admin');
    await page.getByRole('textbox',{name:'Passwort'}).fill(process.env.LAB_SW_PASSWORD || 'shopware');
    await page.getByRole('button',{name:'Anmelden'}).click();
    await page.getByRole('navigation',{name:'Main navigation'}).waitFor({timeout:40000});
    const cancel = page.getByRole('button',{name:'Abbrechen',exact:true});
    if (await cancel.count()) await cancel.click();
    await grid(page,'catalog','product','#/sw/product/index','Search products...',fixture.sku,'In stock');
    const orders = await grid(page,'orders','order','#/sw/order/index','Search all orders...',fixture.number,'Total');
    if (proof) await page.screenshot({path:proof+'-orders.png'});
    await orders.getByRole('link',{name:fixture.number,exact:true}).click();
    await page.getByRole('heading',{name:'Items',exact:true}).waitFor();
    const selectedRecord = page.url().includes(fixture.order.order);
    if (!selectedRecord) throw Error('Unexpected selected order');
    await page.goto(base.origin+'/admin#/sw/order/detail/'+fixture.order.order);
    await page.getByRole('tab',{name:'Details',exact:true}).click();
    const shipping = page.getByRole('article',{name:'Shipping',exact:true});
    await shipping.getByText('Track and trace codes',{exact:true}).waitFor();
    await shipping.getByText(/Shipping address/).first().waitFor();
    observations.push({source:'orders',kind:'expand',fields:['Delivery address','Items','Tracking codes'],
      selectedRecord});
    const open = shipping.getByText('Open',{exact:true});
    const shipped = page.getByText('Shipped',{exact:true}).last();
    await open.click();
    if (!(await shipped.isVisible())) {
      await shipped.waitFor({timeout:3500}).catch(()=>{});
      if (!(await shipped.isVisible()) && await open.isVisible()) await open.click();
    }
    await shipped.click({timeout:10000});
    const dialog = page.getByRole('dialog',{name:'Change status',exact:true});
    await dialog.getByRole('checkbox',{name:'Send email to customer'}).uncheck();
    const pending = page.waitForResponse(r => r.url().endsWith('/state/ship'));
    await dialog.getByRole('button',{name:'Update status',exact:true}).click();
    const response = await pending;
    await shipping.getByText('Shipped',{exact:true}).waitFor();
    const matches = new URL(response.url()).pathname.includes(fixture.order.order_delivery);
    if (!response.ok() || !matches) throw Error('The native shipping command did not target the expected record');
    observations.push({source:'orders',kind:'command',event:'order_delivery.ship',status:response.status(),
      binding:'selected order → delivery relation',after:'shipped',emailSent:false});
    if (proof) await page.screenshot({path:proof+'-shipping.png'});
    fs.writeFileSync(output,observations.map(row=>JSON.stringify(row)).join('\n')+'\n');
    console.log(JSON.stringify({observations:observations.length}));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode=1; });
