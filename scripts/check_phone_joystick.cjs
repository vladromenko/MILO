// UI regression only: every control POST is intercepted, never sent to the robot.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');

(async () => {
  const code = execFileSync('ssh', ['milo-jetson','/home/vlad/MILO/milo web-code'], {encoding:'utf8'}).trim();
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    for (const [name,width,height] of [['portrait',390,844],['landscape',844,390],['desktop-game',1280,800]]) {
      const page = await browser.newPage({viewport:{width,height}}), calls = [], errors = [];
      let delayStart = false, mockFault = {reason:'ack_timeout',joint:'J4',target:5,measured:2};
      page.on('pageerror',e=>errors.push(e.message)); page.on('dialog',d=>d.accept());
      await page.route('**/api/control',async route => {
        const body=route.request().postDataJSON(); calls.push(body);
        if (body.action==='joystick_start' && delayStart) await new Promise(resolve=>setTimeout(resolve,250));
        await route.fulfill({json:body.action==='joystick_start'?{session:'a'.repeat(48)}:{ok:true,ready_joints:['J1','J2','J3','J4','J5']}});
      });
      await page.route('**/api/status',async route => {
        const result=await route.fetch(), s=await result.json();
        if (!s.arm) { await route.fulfill({response:result,json:s}); return; }
        s.arm.state=mockFault?'ESTOP':'DISARMED'; s.arm.pending=null; s.arm.error=mockFault?'ack_timeout':null;
        s.arm.motion_fault=mockFault;
        s.home={state:'idle'};s.manual_move={state:'idle'};s.search={state:'idle'};s.joystick={state:'holding'};
        await route.fulfill({json:s});
      });
      await page.goto('http://10.43.0.1/'); await page.locator('#code').fill(code); await page.locator('#login button').click();
      await page.locator('#app').waitFor({state:'visible'});
      assert.equal(await page.locator('[data-tab="profiles"]').count(),0);
      await page.locator('[data-tab="vision"]').click();
      assert.equal(await page.getByRole('button',{name:'E-STOP',exact:true}).count(),1);
      assert.equal(await page.getByRole('button',{name:'Recover',exact:true}).count(),1);
      assert.equal(await page.locator('#joystick-axes').count(),0);
      await page.waitForFunction(()=>document.getElementById('joystick-state').textContent.includes('J4: target 5 deg, measured 2 deg'));
      assert.equal(await page.locator('#joystick').getAttribute('aria-disabled'),'true');
      await page.screenshot({path:`artifacts/joystick-fault-${name}.png`,fullPage:true});
      await page.locator('#game-recover').click();
      await page.waitForFunction(()=>document.body.textContent.includes('Recovered. Use a new joystick gesture'));
      assert.deepEqual(calls.map(x=>x.action),['recover']);
      mockFault=null;
      await page.evaluate(()=>{document.getElementById('vision').requestFullscreen=undefined;});
      await page.locator('#game-view').click();
      await page.locator('#joystick-enabled').check();
      await page.waitForFunction(()=>document.getElementById('joystick').getAttribute('aria-disabled')==='false');
      await page.waitForFunction(()=>document.getElementById('camera-frame').naturalWidth>0);
      await page.locator('#joystick-speed').fill('8');
      const pad=await page.locator('#joystick').boundingBox();
      await page.mouse.move(pad.x+pad.width*.8,pad.y+pad.height*.5); await page.mouse.down();
      await page.waitForTimeout(350); await page.mouse.up(); await page.waitForTimeout(150);
      assert(calls.some(x=>x.action==='joystick_update'&&x.joint==='J1'&&x.speed===8&&x.direction===1));
      assert(calls.some(x=>x.action==='joystick_stop'));
      const stopped=calls.length;await page.waitForTimeout(300);assert.equal(calls.length,stopped);
      const wrist=await page.locator('#wrist-joystick').boundingBox();
      await page.mouse.move(wrist.x+wrist.width*.8,wrist.y+wrist.height*.5);
      await page.mouse.down();await page.waitForTimeout(200);await page.mouse.up();await page.waitForTimeout(100);
      assert(calls.some(x=>x.action==='joystick_update'&&x.joint==='J4'&&x.direction===1));
      await page.locator('#wrist-joystick').focus();await page.keyboard.down('ArrowUp');
      await page.waitForTimeout(200);await page.keyboard.up('ArrowUp');await page.waitForTimeout(100);
      assert(calls.some(x=>x.action==='joystick_update'&&x.joint==='J5'&&x.direction===1));
      const shoulder=await page.locator('#shoulder-minus').boundingBox();
      await page.mouse.move(shoulder.x+20,shoulder.y+20);await page.mouse.down();
      await page.waitForTimeout(200);await page.mouse.up();await page.waitForTimeout(100);
      assert(calls.some(x=>x.action==='joystick_update'&&x.joint==='J2'&&x.direction===-1));
      await page.mouse.move(pad.x+pad.width*.8,pad.y+pad.height*.5);
      delayStart=true;const oldUpdates=calls.filter(x=>x.action==='joystick_update').length;
      await page.mouse.down();await page.waitForTimeout(40);await page.mouse.up();await page.waitForTimeout(450);
      assert.equal(calls.filter(x=>x.action==='joystick_update').length,oldUpdates);
      assert.equal(calls.at(-1).action,'joystick_stop');
      assert(!calls.some(x=>x.joint==='J6'));
      assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));
      if(width>height) {
        const consoleBox=await page.locator('.joystick-console').boundingBox();
        const shoulderBox=await page.locator('#shoulder-minus').boundingBox();
        assert(shoulderBox.y+shoulderBox.height<=consoleBox.y+consoleBox.height+1,'shoulder clipped by control area');
        for(const id of ['joystick','wrist-joystick','shoulder-minus','game-estop','game-recover','joystick-speed']) {
          const box=await page.locator('#'+id).boundingBox();
          assert(box.x>=0 && box.x+box.width<=width && box.y>=0 && box.y+box.height<=height, id+' clipped');
        }
      } else {
        const picture=await page.locator('#camera-frame').boundingBox();
        const settings=await page.locator('.drive-settings').boundingBox();
        assert(picture.y+picture.height<=settings.y+1,'video overlaps control settings');
      }
      await page.screenshot({path:`artifacts/joystick-${name}.png`,fullPage:true});
      assert.deepEqual(errors,[]);console.log(JSON.stringify({name,mockedControlCalls:calls.length,errors}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
