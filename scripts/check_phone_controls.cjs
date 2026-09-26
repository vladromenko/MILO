// Live operator acceptance. Settings are restored; --motion explicitly tests J1 only.
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');

(async () => {
  const code = execFileSync('ssh', ['milo-jetson', '/home/vlad/MILO/milo web-code'], {encoding:'utf8'}).trim();
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  const page = await browser.newPage({viewport:{width:390,height:844}});
  try {
    await page.goto('http://10.43.0.1/');
    await page.locator('#code').fill(code);
    await page.locator('#login button').click();
    await page.locator('#health dd').first().waitFor();
    await page.locator('[data-tab="audio"]').click();
    const select = page.locator('select[name="language"]');
    const original = await select.inputValue();
    async function save(language) {
      await select.selectOption(language);
      const response = page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method()==='POST');
      await page.locator('#audio-settings button').click();
      const result = await response;
      if (!result.ok()) throw new Error(await result.text());
      const state = await page.evaluate(async () => (await fetch('/api/status')).json());
      if (state.settings.language !== language) throw new Error('Language was not persisted');
      console.log(JSON.stringify({language, voice:state.settings.voice, saved:true}));
    }
    try {
      for (const language of ['fr','de','ja','ar','ur','ru','en']) await save(language);
    } finally { await save(original); }
    if (await page.locator('[name="auto_language"]').count()) throw new Error('Automatic switching control remains');
    if (process.argv.includes('--motion')) {
      await page.locator('[data-tab="vision"]').click();
      const before = await page.evaluate(async () => (await fetch('/api/status')).json());
      if (before.arm.state !== 'DISARMED') throw new Error('Disarm before motion test');
      const initial = before.arm.raw_pose;
      const first = initial.J1 > 135 ? '-1' : '1';
      for (const direction of [first, first==='1'?'-1':'1']) {
        const response = page.waitForResponse(r => r.url().endsWith('/api/control') && r.request().method()==='POST');
        await page.locator(`[data-joint="J1"][data-delta="${direction}"]`).click();
        const result = await response;
        if (!result.ok()) throw new Error(await result.text());
        const state = await page.evaluate(async () => (await fetch('/api/status')).json());
        if (state.arm.raw_pose.J6 !== initial.J6) throw new Error('J6 feedback changed');
        console.log(JSON.stringify({motion:'J1', direction, pose:state.arm.raw_pose, state:state.arm.state}));
        await page.waitForTimeout(1000);
      }
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
