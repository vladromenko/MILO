// Authenticated browser checks; never sends actuator or settings commands.
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const {chromium} = require('playwright');

(async () => {
  const code = execFileSync('ssh', ['-o', 'BatchMode=yes', 'milo-jetson',
    '/home/vlad/MILO/milo web-code'], {encoding: 'utf8'}).trim();
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    fs.mkdirSync('artifacts', {recursive: true});
    for (const [name, width, height] of [['small-phone',320,740], ['phone',390,844], ['desktop',1280,900]]) {
      const page = await browser.newPage({viewport: {width,height}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.MILO_PANEL_URL || 'http://10.43.0.1/');
      await page.locator('#code').fill(code);
      await page.locator('#login button').click();
      await page.locator('#app').waitFor({state: 'visible'});
      await page.locator('#health dd').first().waitFor();
      for (const tab of ['status','vision','audio','memory','logs']) {
        await page.locator(`[data-tab="${tab}"]`).click();
        await page.waitForTimeout(350);
        if (tab === 'vision') {
          if (await page.locator('#target-controls form').count() !== 5 ||
              await page.locator('[data-joint="J6"]').count()) throw new Error('Unexpected manual joints');
          await page.waitForFunction(() => {
            const image = document.getElementById('camera-frame');
            return !image.hidden && image.complete && image.naturalWidth > 0;
          }, null, {timeout:15000});
        }
        if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
          throw new Error(`${name}: horizontal overflow on ${tab}`);
        }
        await page.screenshot({path:`artifacts/phone-check-${name}-${tab}.png`,fullPage:true});
      }
      await page.locator('[data-tab="vision"]').click();
      console.log(JSON.stringify({viewport:name, arm:await page.locator('#arm').textContent(),
        joints:await page.locator('#arm-pose').textContent(),
        video:await page.locator('#video-state').textContent(),
        errors}));
      if (errors.length) throw new Error(errors.join('\n'));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error.message); process.exitCode=1;});
