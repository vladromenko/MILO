// Run against an SSH-forwarded instance. Never prints the pairing code.
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const {chromium} = require('playwright');
(async () => {
  const url = process.env.MILO_PANEL_URL || 'http://127.0.0.1:18784';
  const code = execFileSync('ssh', ['-o', 'BatchMode=yes', process.env.MILO_JETSON_HOST || 'milo-jetson',
    '/home/vlad/MILO/milo web-code'], {encoding:'utf8'}).trim();
  const browser = await chromium.launch({headless:true, channel:process.env.MILO_BROWSER_CHANNEL || 'chrome'});
  try {
    fs.mkdirSync('artifacts', {recursive:true});
    for (const [name,width,height] of [['desktop',1280,900],['mobile',390,844]]) {
      const page = await browser.newPage({viewport:{width,height}});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(url);
      await page.locator('#code').fill(code);
      await page.getByRole('button', {name:'Sign in',exact:true}).click();
      await page.locator('#app').waitFor({state:'visible'});
      await page.locator('#health dd').first().waitFor();
      for (const tab of ['status','audio','memory','logs']) {
        await page.locator(`[data-tab="${tab}"]`).click();
        await page.waitForTimeout(250);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        if (overflow) throw Error(name + ': horizontal overflow on ' + tab);
        await page.screenshot({path:`artifacts/panel-${name}-${tab}.png`,fullPage:true});
      }
      await page.locator('[data-tab="audio"]').click();
      await page.locator('#audio-settings button').click();
      await page.waitForFunction(() => document.getElementById('notice').textContent === 'Saved');
      await page.locator('#off').click();
      await page.waitForFunction(() => document.getElementById('notice').textContent === 'Actuators disabled');
      if (errors.length) throw Error(errors.join('\n'));
      console.log(name + ': authenticated views, audio apply, actuator OFF, layout OK');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode=1; });
