'use strict';
let stickHeld = false, stickSession = null, stickSequence = 0, stickGeneration = 0;
let stickVector = {joint:'J1',direction:0}, stickAllowed = false, gameView = false;
let stickOwner = null;
function endJoystick() {
  stickHeld = false; stickGeneration++;
  stickOwner = null;
  const session = stickSession; stickSession = null;
  for (const knob of document.querySelectorAll('.stick-knob')) knob.style.transform = 'translate(-50%,-50%)';
  if (session) api('/api/control', {action:'joystick_stop',session}).catch(e => notice(e.message));
}
function renderJoystick(s) {
  const busy = s.home?.state === 'running' || s.manual_move?.state === 'running' || s.search?.state === 'searching';
  const js = s.joystick || {}, state = s.arm?.state;
  const fault = s.arm?.motion_fault;
  const faultMessage = state === 'ESTOP' && fault?.reason === 'ack_timeout'
    ? `${fault.joint}: target ${fault.target} deg, measured ${fault.measured ?? '?'} deg. Motion not confirmed. Inspect, then Recover.` : '';
  stickAllowed = Boolean(brainOnline && state === 'DISARMED' && !busy && $('joystick-enabled').checked);
  for (const pad of document.querySelectorAll('.drive-pad')) pad.setAttribute('aria-disabled', String(!stickAllowed));
  if (!stickAllowed && stickHeld) endJoystick();
  $('joystick-state').textContent = faultMessage || js.error || (state === 'ESTOP' ? 'E-STOP: inspect and recover' : !brainOnline ? 'MILO offline' : !$('joystick-enabled').checked ? 'Manual control off' : busy ? 'Posture motion active' : js.state === 'limit' ? `${js.joint}: limit reached` : js.state === 'waiting_feedback' ? 'Waiting for stable feedback' : stickHeld ? `${stickVector.joint} / ${$('joystick-speed').value} deg/s` : 'Ready');
  const a = s.arm || {};
  const angle = joint => Number.isInteger(a.raw_pose?.[joint]) ? a.raw_pose[joint] + ' deg' : '?';
  $('base-angles').textContent = 'J1 ' + angle('J1') + ' / J3 ' + angle('J3');
  $('wrist-angles').textContent = 'J4 ' + angle('J4') + ' / J5 ' + angle('J5');
  $('shoulder-angle').textContent = angle('J2');
  const j2 = a.manual?.J2, position = a.raw_pose?.J2;
  $('shoulder-minus').disabled = !stickAllowed || !j2?.feedback_stable || position <= (j2?.limits?.[0] ?? 90);
  $('shoulder-plus').disabled = !stickAllowed || !j2?.feedback_stable || position >= 115;
  $('arm-problem').textContent = faultMessage ? '' : a.error || (a.tracking_error ? 'Face tracking needs the startup posture. Manual joints use their own ranges.' : '');
  for (const id of ['game-hold','game-estop','game-recover']) $(id).disabled = !brainOnline;
}
async function beginJoystick() {
  if (!stickAllowed || stickHeld) return;
  stickHeld = true; const generation = ++stickGeneration;
  try {
    const result = await api('/api/control', {action:'joystick_start',posture:true});
    if (!stickHeld || generation !== stickGeneration) {
      await api('/api/control', {action:'joystick_stop',session:result.session}); return;
    }
    stickSession = result.session; stickSequence = 0;
    const pulse = async () => {
      if (!stickHeld || generation !== stickGeneration || !stickSession) return;
      try {
        await api('/api/control', {action:'joystick_update', session:stickSession, sequence:++stickSequence,
          ...stickVector, speed:Number($('joystick-speed').value)}, 450);
        if (stickHeld && generation === stickGeneration) setTimeout(pulse, 100);
      } catch (e) { endJoystick(); $('joystick-state').textContent = e.message; notice(e.message); }
    };
    await pulse();
  } catch (e) { endJoystick(); notice(e.message); }
}
function pointStick(event, pad) {
  const box = pad.getBoundingClientRect(), radius = box.width * .3;
  let x = (event.clientX - box.left - box.width/2) / radius;
  let y = (event.clientY - box.top - box.height/2) / radius;
  const length = Math.hypot(x,y); if (length > 1) { x /= length; y /= length; }
  const horizontal = pad.dataset.horizontal, vertical = pad.dataset.vertical;
  stickVector = Math.max(Math.abs(x),Math.abs(y)) < .2 ? {joint:horizontal,direction:0}
    : Math.abs(x) > Math.abs(y) ? {joint:horizontal,direction:x>0?1:-1}
    : {joint:vertical,direction:y<0?1:-1};
  pad.querySelector('.stick-knob').style.transform = `translate(calc(-50% + ${x*radius}px),calc(-50% + ${y*radius}px))`;
}
for (const pad of document.querySelectorAll('.drive-pad')) {
  pad.onpointerdown = event => {
    if (!stickAllowed || stickHeld || !event.isPrimary) return;
    event.preventDefault(); pad.setPointerCapture(event.pointerId); stickOwner = pad;
    pointStick(event,pad); beginJoystick();
  };
  pad.onpointermove = event => { if (stickHeld && stickOwner===pad && event.isPrimary) { event.preventDefault(); pointStick(event,pad); } };
  for (const name of ['pointerup','pointercancel','lostpointercapture']) pad.addEventListener(name, () => { if (stickOwner===pad) endJoystick(); });
  pad.onkeydown = event => {
    if (!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key) || !stickAllowed || (stickHeld && stickOwner!==pad)) return;
    event.preventDefault(); stickOwner=pad;
    stickVector = {joint:event.key==='ArrowLeft'||event.key==='ArrowRight'?pad.dataset.horizontal:pad.dataset.vertical,
      direction:event.key==='ArrowRight'||event.key==='ArrowUp'?1:-1};
    if (!event.repeat) beginJoystick();
  };
  pad.onkeyup = event => { if (event.key.startsWith('Arrow') && stickOwner===pad) endJoystick(); };
  pad.onblur = () => { if (stickOwner===pad) endJoystick(); };
}
for (const button of document.querySelectorAll('[data-shoulder]')) {
  const start = () => {
    if (button.disabled || !stickAllowed || stickHeld) return;
    stickOwner=button; stickVector={joint:'J2',direction:Number(button.dataset.shoulder)}; beginJoystick();
  };
  button.onpointerdown = event => {
    if (!event.isPrimary || button.disabled || stickHeld) return;
    event.preventDefault(); button.setPointerCapture(event.pointerId); start();
  };
  for (const name of ['pointerup','pointercancel','lostpointercapture','blur']) button.addEventListener(name,()=>{if(stickOwner===button)endJoystick();});
  button.onkeydown = event => { if ([' ','Enter'].includes(event.key)) {event.preventDefault();if(!event.repeat)start();} };
  button.onkeyup = event => { if ([' ','Enter'].includes(event.key) && stickOwner===button) endJoystick(); };
}
$('joystick-enabled').onchange = async () => {
  endJoystick();
  if ($('joystick-enabled').checked && !confirm('Enable manual J1-J5 joystick? Keep the entire path and cables clear. Releasing stops new commands; the current short move may finish. J6 stays locked.')) $('joystick-enabled').checked = false;
  await refresh();
};
$('joystick-speed').oninput = () => { endJoystick(); $('joystick-speed-value').textContent=$('joystick-speed').value+' deg/s'; };
for (const id of ['game-hold','game-estop','game-recover']) $(id).onclick = () => {
  endJoystick(); $(id === 'game-hold' ? 'hold' : id === 'game-estop' ? 'estop' : 'reset-stop').click();
};
$('game-view').onclick = async () => {
  endJoystick(); gameView = !gameView;
  $('vision').classList.toggle('game-mode',gameView);
  $('game-view').setAttribute('aria-label',gameView?'Exit fullscreen controls':'Fullscreen camera controls');
  try {
    if (gameView) {
      if ($('vision').requestFullscreen) await $('vision').requestFullscreen();
      if (screen.orientation?.lock) await screen.orientation.lock('landscape');
    } else {
      screen.orientation?.unlock?.();
      if (document.fullscreenElement) await document.exitFullscreen();
    }
  } catch (_) { /* The responsive expanded view also works without fullscreen APIs. */ }
};
document.addEventListener('fullscreenchange', () => {
  endJoystick(); if (!document.fullscreenElement) { gameView=false; $('vision').classList.remove('game-mode'); }
});
for (const name of ['blur','pagehide','orientationchange']) window.addEventListener(name,endJoystick);
document.addEventListener('visibilitychange',endJoystick);
document.addEventListener('click',event => {
  if (event.target.closest('[data-tab],#logout,#off,#estop,#hold,#reset-stop,#home,#track,#scan,#stop-milo')) endJoystick();
},true);
