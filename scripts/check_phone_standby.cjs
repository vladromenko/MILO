// Verify the offline launch panel. --stop stops MILO through the real browser UI.
// Never clicks Start: that operation moves the physical arm.
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const {chromium} = require('playwright');

(async () => {
  const code = execFileSync('ssh', ['-o','BatchMode=yes','milo-jetson',
    '/home/vlad/MILO/milo web-code'], {encoding:'utf8'}).trim();
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    fs.mkdirSync('artifacts', {recursive:true});
    for (const [width,height] of [[390,844],[1280,900]]) {
      const page = await browser.newPage({viewport:{width,height}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.MILO_PANEL_URL || 'http://10.43.0.1/');
      await page.locator('#code').fill(code);
      await page.locator('#login button').click();
      await page.waitForFunction(() => ['Standby','Running','Starting'].includes(
        document.getElementById('runtime-state').textContent));
      if (process.argv.includes('--stop') && width === 390 &&
          await page.locator('#stop-milo').isEnabled()) {
        await page.locator('#stop-milo').click();
      }
      await page.waitForFunction(() => document.getElementById('runtime-state').textContent === 'Standby',
        null, {timeout:90000});
      await page.reload();
      await page.waitForFunction(() => !document.getElementById('start-milo').disabled);
      if (await page.locator('#stop-milo').isEnabled()) throw new Error('Stop enabled in standby');
      if (await page.locator('#estop').isEnabled()) throw new Error('Stale actuator state');
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow');
      await page.screenshot({path:`artifacts/standby-${width}.png`,fullPage:true});
      if (errors.length) throw new Error(errors.join('\n'));
      console.log(JSON.stringify({width,height,state:'Standby',startEnabled:true,reloadPassed:true,errors}));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode=1; });
