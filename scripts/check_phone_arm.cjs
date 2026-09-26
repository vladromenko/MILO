// Physical test. Requires the operator beside a clear arm path.
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
if (!process.argv.includes('--confirm-clear-path')) throw new Error('Explicit physical test confirmation required');
(async () => {
  const code = execFileSync('ssh',['milo-jetson','/home/vlad/MILO/milo web-code'],{encoding:'utf8'}).trim();
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    page.on('dialog', dialog => dialog.accept());
    await page.goto('http://10.43.0.1/');
    await page.locator('#code').fill(code);
    await page.locator('#login button').click();
    await page.waitForFunction(() => ['ESTOP','DISARMED','ARMED'].includes(document.getElementById('arm').textContent));
    if (await page.locator('#reset-stop').isVisible()) {
      await page.locator('#reset-stop').click();
      await page.waitForFunction(() => document.getElementById('arm').textContent === 'DISARMED');
    }
    await page.locator('[data-tab="vision"]').click();
    await page.locator('#game-hold').click();
    await page.locator('#joystick-enabled').check();
    await page.locator('.precision-controls > summary').click();
    await page.waitForFunction(() => !document.querySelector('[data-joint="J1"][data-delta="-1"]').disabled);
    const snapshot = () => page.evaluate(async () => (await fetch('/api/status')).json());
    const before = (await snapshot()).arm.raw_pose;
    const wrist = process.argv.includes('--j5-only');
    const remaining = process.argv.includes('--remaining-manual') || wrist;
    let after = before;
    if (!remaining) {
    await page.locator('[data-joint="J1"][data-delta="-1"]').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent === 'Movement confirmed');
    after = (await snapshot()).arm.raw_pose;
    if (!(after.J1 < before.J1 && before.J1 - after.J1 <= 4 && after.J6 === before.J6)) {
      throw new Error('Unexpected feedback: ' + JSON.stringify({before,after}));
    }
    }
    const manual = [];
    if (process.argv.includes('--all-manual') || remaining) {
      await page.locator('#joystick-enabled').check();
      const joints = wrist ? [['J5',1]] : remaining ? [['J4',1],['J5',1]] : [['J2',-1],['J3',1],['J4',1],['J5',1]];
      for (const [joint,delta] of joints) {
        const selector = `[data-joint="${joint}"][data-delta="${delta}"]`;
        await page.waitForFunction(sel => !document.querySelector(sel).disabled, selector);
        const previous = (await snapshot()).arm.raw_pose;
        const response = page.waitForResponse(r => r.url().endsWith('/api/control') && r.request().method() === 'POST');
        await page.locator(selector).click();
        const result = await response;
        if (!result.ok()) throw new Error(`${joint}: command rejected: ${await result.text()}`);
        await page.waitForTimeout(1200);
        const current = (await snapshot()).arm.raw_pose;
        const change = current[joint] - previous[joint];
        if (!(change * delta > 0 && Math.abs(change) <= 5 && current.J6 === before.J6)) {
          throw new Error('Unexpected manual feedback: ' + JSON.stringify({joint,previous,current}));
        }
        manual.push({joint,before:previous[joint],after:current[joint]});
      }
    }
    await page.waitForFunction(() => !document.getElementById('track').disabled);
    await page.locator('#track').click();
    await page.waitForFunction(() => document.getElementById('arm').textContent === 'ARMED');
    await page.screenshot({path:'artifacts/manual-arm-phone.png',fullPage:true});
    console.log(JSON.stringify({manualMovement:true,before,after,manual,trackingEnabled:true}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error.message);process.exitCode=1;});
