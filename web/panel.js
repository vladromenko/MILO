'use strict';
const $ = id => document.getElementById(id);
let csrf = '', active = 'status', settingsLoaded = false, inFlight = false;
let frameURL = null, jogging = false;
let runtimeState = 'unknown', runtimeRequest = false, brainOnline = false;
const notice = text => { $('notice').textContent = text; if ($('game-notice')) $('game-notice').textContent = text; };
async function api(path, body, timeout) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
    ...(timeout ? {signal:AbortSignal.timeout(timeout)} : {}),
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-Milo-CSRF': csrf},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (response.status === 401) signed(false);
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try { message = JSON.parse(raw).error || raw; } catch (_) {}
    throw new Error(String(message).slice(0, 240));
  }
  return response.json();
}
function signed(yes) {
  $('login').hidden = yes; $('app').hidden = !yes; $('logout').hidden = !yes;
  if (!yes) { csrf = ''; settingsLoaded = false; if (typeof endJoystick === 'function') endJoystick(); }
}
function list(id, entries) {
  $(id).replaceChildren();
  for (const [key, value] of entries) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = key; dd.textContent = value == null ? 'Unavailable' : String(value);
    $(id).append(dt, dd);
  }
}
const flag = value => value ? 'Ready' : 'Unavailable';
function render(s) {
  brainOnline = Boolean(s.arm);
  $('live-status').hidden = !brainOnline;
  const fresh = s.edge_age_ms < 2000, a = s.audio || {}, p = s.perception || {}, b = s.behavior || {};
  $('connection').textContent = s.edge_error ? 'Degraded' : 'Connected';
  $('arm').textContent = s.arm?.state || (runtimeState === 'stopped' ? 'OFF' : 'Unknown');
  const moving = s.home?.state === 'running' || s.manual_move?.state === 'running' || s.search?.state === 'searching';
  $('reset-stop').disabled = !brainOnline;
  list('arm-pose', [['State', s.arm?.state], ['Controller', s.arm ? s.arm.firmware?.error || 'Ready' : 'Offline'],
    ['Startup pose', s.home?.error || s.home?.state || 'idle'],
    ...['J1','J2','J3','J4','J5','J6'].map(joint => [joint, s.arm?.raw_pose?.[joint]]),
    ['Tracking pose', s.arm?.feedback_stable ? 'Ready' : 'Outside tracking range or no feedback']]);
  const manual = s.arm?.manual || {};
  const pending = Boolean(s.arm?.pending) || Object.values(manual).some(gate => gate.pending);
  const firmware = s.arm?.firmware;
  const controllerBlocked = Boolean(firmware && (!firmware.fresh || firmware.fault));
  list('posture-status', Object.entries(manual).map(([joint,gate]) => [joint,
    String(gate.pose ?? 'Unknown') + ' deg; ' + (s.arm?.state === 'ESTOP' ? 'ESTOP' : gate.blocked_reason ? gate.blocked_reason : (gate.feedback_stable ? 'feedback stable' : gate.reason || 'unavailable'))
    + '; range ' + (gate.command_limits || gate.limits).join('..')]));
  for (const button of document.querySelectorAll('[data-joint]')) {
    const joint = button.dataset.joint, manualGate = Boolean(manual[joint]);
    const auxiliary = ['J2','J4','J5'].includes(joint);
    const current = manualGate ? manual[joint]?.pose : s.arm?.pose?.[joint];
    const [low,high,step] = manualGate ? [...(manual[joint]?.command_limits || manual[joint]?.limits || [0,0]),['J3','J4'].includes(joint) ? 4 : joint === 'J1' ? 3 : 2] : joint === 'J1' ? [0,180,3] : [76,135,4];
    let target = current + Number(button.dataset.delta) * step;
    if (joint === 'J4' && current > 0 && target < 0) target = 0;
    if (joint === 'J2' && Number(button.dataset.delta) === -1 && target > high && current <= high + 4) target = high;
    const feedbackStable = manualGate ? manual[joint]?.feedback_stable : s.arm?.feedback_stable;
    button.disabled = jogging || controllerBlocked || moving || s.arm?.state !== 'DISARMED' || !feedbackStable || !$('joystick-enabled').checked
      || pending || current == null || target < low || target > high
      || (auxiliary && (!$('joystick-enabled').checked || !manual[joint]?.feedback_stable || manual[joint]?.blocked_reason))
      || (joint === 'J2' && target > 115 && target >= current);
  }
  $('track').disabled = jogging || moving || pending || controllerBlocked || !fresh || !s.arm?.feedback_stable || s.arm?.state !== 'DISARMED';
  $('scan').disabled = $('track').disabled;
  $('home').disabled = jogging || controllerBlocked || pending || moving || s.arm?.state !== 'DISARMED';
  const move = s.manual_move;
  $('move-state').textContent = move?.state === 'running' ? `${move.joint} to ${move.target} deg` : move?.error || '';
  for (const joint of ['J1','J2','J3','J4','J5']) {
    const gate = manual[joint], input = $('target-' + joint), button = $('move-' + joint);
    const [low, high] = gate?.command_limits || gate?.limits || [0,0];
    input.min = low; input.max = high;
    if (!input.dataset.edited && Number.isInteger(gate?.pose)) input.value = gate.pose;
    const state = gate?.blocked_reason || (s.arm?.state === 'ESTOP' ? 'E-STOP' : gate?.feedback_stable ? 'Ready' : 'Feedback unavailable or out of range');
    $('current-' + joint).textContent = `${gate?.pose ?? '?'} deg / ${low}..${high} / ${state}`;
    const target = Number(input.value);
    button.disabled = jogging || moving || pending || controllerBlocked || !gate?.feedback_stable
      || gate?.blocked_reason || s.arm?.state !== 'DISARMED' || !$('joystick-enabled').checked
      || input.value === '' || !Number.isInteger(target) || target < low || target > high
      || (joint === 'J2' && target > 115 && target >= gate.pose);
  }
  for (const id of ['off', 'estop', 'hold']) $(id).disabled = !brainOnline;
  for (const id of ['behavior', 'audio-settings']) {
    for (const el of $(id).elements) el.disabled = !brainOnline;
  }
  list('health', [['Jetson', 'Online'], ['Raspberry Pi', fresh ? 'Connected' : 'Unavailable'],
    ['LLM', flag(s.llm_ready)], ['Speech recognition', flag(s.stt_ready)],
    ['Camera', fresh ? p.status?.state : 'Unavailable'], ['Hailo', fresh ? p.status?.objects?.error || p.status?.backends?.objects : 'Unavailable'],
    ['Microphone', fresh ? flag(a.input_ready) : 'Unavailable'], ['Speaker', fresh ? flag(a.output_ready) : 'Unavailable'],
    ['Memory', flag(s.memory_ready)], ['Person', fresh ? s.identity?.name || 'Unknown' : 'Unknown'],
    ['Faces', fresh ? p.faces?.length : null], ['Arm', s.arm?.reason || s.arm?.state]]);
  if (!brainOnline) list('health', [['Phone panel', 'Ready'], ['MILO', $('runtime-state').textContent]]);
  list('social', [['State', b.state], ['Expression', b.social?.visible_expression],
    ['Expression model score', b.social?.confidence], ['Reply source', s.metrics?.reply_source],
    ['Trend', b.social?.trend], ['Topic', b.topic],
    ['Camera search', s.search?.state], ['Visual question', s.visual_inquiry?.state],
    ['Visual error', s.visual_inquiry?.error]]);
  $('reply').textContent = s.last_reply || '';
  const resources = [];
  for (const [host, values] of Object.entries(s.resources || {})) {
    if (!values) continue;
    resources.push([host + ' CPU', values.cpu_percent == null ? null : values.cpu_percent + '%'],
      [host + ' RAM', values.ram_used_mb + ' / ' + values.ram_total_mb + ' MB'],
      [host + ' GPU', values.gpu_percent == null ? null : values.gpu_percent + '%']);
    for (const [sensor, temp] of Object.entries(values.temperatures || {})) resources.push([host + ' ' + sensor, temp + ' C']);
  }
  list('resources', resources);
  list('metrics', [['STT', a.stt_ms ? Math.round(a.stt_ms) + ' ms' : null], ['TTS synthesis', a.tts_ms ? Math.round(a.tts_ms) + ' ms' : null],
    ['LLM TTFT', s.metrics?.llm_ttft_ms == null ? null : s.metrics.llm_ttft_ms + ' ms'],
    ['Memory retrieval', s.metrics?.memory_retrieval_ms == null ? null : s.metrics.memory_retrieval_ms + ' ms'],
    ['Camera age', p.capture_age_ms == null ? null : Math.round(p.capture_age_ms) + ' ms'],
    ['Voice endpoint to first PCM', a.response_pcm_ms == null ? null : a.response_pcm_ms + ' ms'],
    ['TTS to first PCM', a.tts_first_pcm_ms == null ? null : a.tts_first_pcm_ms + ' ms']]);
  if (!settingsLoaded && s.settings) {
    for (const form of [$('behavior'), $('audio-settings')]) {
      for (const el of form.elements) if (el.name in s.settings) {
        if (el.type === 'checkbox') el.checked = s.settings[el.name]; else el.value = s.settings[el.name];
      }
    }
    updateOutputs(); settingsLoaded = true;
  }
  if (s.settings_error) notice(s.settings_error);
  if (typeof renderJoystick === 'function') renderJoystick(s);
}
function renderRuntime(s) {
  runtimeState = s.state;
  const labels = {stopped:'Standby', starting:'Starting', running:'Running', stopping:'Stopping',
    start_failed:'Start incomplete', stop_failed:'Stop incomplete'};
  $('runtime-state').textContent = labels[s.state] || 'Unavailable';
  $('start-milo').disabled = runtimeRequest || !['stopped','start_failed','stop_failed'].includes(s.state);
  $('stop-milo').disabled = runtimeRequest || !['starting','running','start_failed','stop_failed'].includes(s.state);
}
for (const action of ['start', 'stop']) $(action + '-milo').onclick = async () => {
  if (action === 'start' && !confirm('Start MILO and move to the startup posture (J4 0, J3 75, J2 115), then track faces? Keep the arm path clear. J6 will not move.')) return;
  runtimeRequest = true; renderRuntime({state:runtimeState});
  try {
    renderRuntime(await api('/api/runtime', {action}));
    settingsLoaded = false; notice('');
  } catch (e) { notice('Not confirmed: ' + e.message); }
  finally { runtimeRequest = false; await refresh(); }
};
function updateOutputs() {
  for (const [key, id, unit] of [['speaker_volume','speaker-volume-value','%'], ['tts_volume','tts-volume-value',''],
    ['mic_gain','mic-gain-value','%'], ['speech_rate','speech-rate-value','']]) {
    $(id).textContent = $('audio-settings').elements[key].value + unit;
  }
}
$('audio-settings').oninput = updateOutputs;
for (const id of ['behavior', 'audio-settings']) $(id).onsubmit = async event => {
  event.preventDefault(); const body = {};
  for (const el of event.currentTarget.elements) if (el.name) body[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'range' ? Number(el.value) : el.value;
  try { await api('/api/settings', body); notice('Saved'); } catch (e) { notice(e.message); }
};
$('login').onsubmit = async event => {
  event.preventDefault();
  try { const result = await api('/login', {code: $('code').value.trim()}); csrf = result.csrf; $('code').value = ''; signed(true); notice(''); await refresh(); }
  catch (e) { notice(e.message); }
};
$('logout').onclick = async () => { try { await api('/api/logout', {}); signed(false); } catch (e) { notice(e.message); } };
for (const [id, action] of [['off','disarm'], ['estop','estop']]) $(id).onclick = async () => {
  try { await api('/api/control', {action}); notice(action === 'estop' ? 'E-STOP latched' : 'Actuators disabled'); await refresh(); }
  catch (e) { notice('Not confirmed: ' + e.message); }
};
for (const [id,action] of [['track','arm'],['hold','disarm']]) $(id).onclick = async () => {
  if (action === 'arm' && !confirm('Enable face tracking with J1 and J3? Keep the arm path clear.')) return;
  try { await api('/api/control', {action}); notice(action === 'arm' ? 'Face tracking enabled' : 'Tracking stopped'); await refresh(); }
  catch (e) { notice('Not confirmed: ' + e.message); }
};
$('home').onclick = async () => {
  if (!confirm('Move to startup posture: J4 0, J3 75, J2 115 degrees? Keep the path clear and watch the arm. J6 will not move.')) return;
  jogging = true;
  try { await api('/api/control', {action:'home'}); notice('Startup posture reached; tracking remains off'); }
  catch (e) { notice('Startup posture stopped: ' + e.message); }
  finally { jogging = false; await refresh(); }
};
$('reset-stop').onclick = async () => {
  if (!confirm('Have you checked the arm and cables? Allow a new manual attempt from the measured position? This clears the stop after fresh stable feedback, cancels the old command and leaves tracking off. It does not move the arm or disable fault detection.')) return;
  jogging = true;
  try { const result = await api('/api/control', {action:'recover'}); notice('Recovered. Use a new joystick gesture to move. Ready joints: ' + result.ready_joints.join(', ')); }
  catch (e) { notice('Reset not confirmed: ' + e.message); }
  finally { jogging = false; await refresh(); }
};
$('scan').onclick = async () => {
  if (!confirm('Look left and right from the current base position using J1 only, then return? Check the path and cable slack. J2-J6 will not be commanded.')) return;
  try { await api('/api/control', {action:'scan'}); notice('Looking around'); await refresh(); }
  catch (e) { notice('Not confirmed: ' + e.message); }
};
for (const joint of ['J1','J2','J3','J4','J5']) {
  const row = document.createElement('form'); row.className = 'target-row';
  const label = document.createElement('label'), input = document.createElement('input');
  label.textContent = joint + ' target (deg)'; input.id = 'target-' + joint;
  input.type = 'number'; input.step = '1'; input.required = true; input.inputMode = 'numeric';
  input.oninput = () => { input.dataset.edited = 'true'; refresh(); }; label.append(input);
  const current = document.createElement('span'); current.id = 'current-' + joint;
  const steps = document.createElement('div'); steps.className = 'target-steps';
  const step = ['J3','J4'].includes(joint) ? 4 : joint === 'J1' ? 3 : 2;
  for (const delta of [-1,1]) {
    const nudge = document.createElement('button'); nudge.type = 'button'; nudge.disabled = true;
    nudge.dataset.joint = joint; nudge.dataset.delta = delta;
    nudge.textContent = delta < 0 ? '\u2212' : '+';
    nudge.title = `${joint} ${delta < 0 ? 'minus' : 'plus'} ${step} degrees`;
    nudge.setAttribute('aria-label', nudge.title); steps.append(nudge);
  }
  const button = document.createElement('button'); button.id = 'move-' + joint;
  button.textContent = 'Move ' + joint; button.disabled = true;
  row.append(label, steps, button, current); $('target-controls').append(row);
  row.onsubmit = async event => {
    event.preventDefault(); if (jogging || button.disabled) return;
    const target = Number(input.value);
    if (!confirm(`Move ${joint} to ${target} degrees in small steps? Keep the path clear. J6 will not move.`)) return;
    jogging = true; button.disabled = true;
    try { await api('/api/control', {action:'move',joint,target,posture:$('joystick-enabled').checked}); notice(`${joint}: target reached within 2 degrees`); }
    catch (e) { notice('Movement stopped: ' + e.message); }
    finally { jogging = false; await refresh(); }
  };
}
for (const button of document.querySelectorAll('[data-joint]')) button.onclick = async () => {
  if (jogging) return;
  jogging = true;
  for (const item of document.querySelectorAll('[data-joint]')) item.disabled = true;
  const body = {action:'nudge', joint:button.dataset.joint, delta:Number(button.dataset.delta)};
  if (['J2','J4','J5'].includes(body.joint)) body.posture = $('joystick-enabled').checked;
  try { await api('/api/control', body); notice('Movement confirmed'); }
  catch (e) { notice('Not confirmed: ' + e.message); }
  finally { setTimeout(() => { jogging = false; refresh(); }, 500); }
};
for (const button of document.querySelectorAll('[data-tab]')) button.onclick = async () => {
  active = button.dataset.tab;
  document.body.classList.toggle('camera-active', active === 'vision');
  for (const other of document.querySelectorAll('[data-tab]')) { $(other.dataset.tab).hidden = other !== button; other.setAttribute('aria-selected', String(other === button)); }
  try { if (active === 'memory') await people(); if (active === 'logs') await logs(); } catch(e) { notice(e.message); }
};
async function people() {
  const data = await api('/api/memory'), selected = $('people').value;
  $('people').replaceChildren(new Option('Select person', ''));
  for (const person of data.people) $('people').add(new Option(person.name, person.id));
  $('people').value = selected; await facts();
}
async function facts() {
  $('facts').replaceChildren(); const person = $('people').value;
  if (!person) return;
  const data = await api('/api/memory?person=' + encodeURIComponent(person));
  if (!data.facts.length) $('facts').textContent = 'No saved facts';
  for (const fact of data.facts) {
    const row = document.createElement('div'), input = document.createElement('textarea'); row.className = 'fact';
    const label = document.createElement('label'); label.textContent = fact.key; input.value = fact.text; label.append(input); row.append(label);
    for (const action of ['edit', 'delete']) {
      const button = document.createElement('button'); button.textContent = action === 'edit' ? 'Save' : 'Delete';
      button.onclick = async () => {
        if (action === 'delete' && !confirm('Delete this memory?')) return;
        button.disabled = true;
        try { await api('/api/memory', {action, person_id: person, key: fact.key, value: input.value, updated_at: fact.updated_at}); await facts(); }
        catch (e) { notice(e.message); button.disabled = false; }
      }; row.append(button);
    }
    $('facts').append(row);
  }
}
$('people').onchange = () => facts().catch(e => notice(e.message));
$('refresh-memory').onclick = () => people().catch(e => notice(e.message));
async function logs() { $('log-output').textContent = (await api('/api/logs?component=' + $('log-service').value + '&level=' + $('log-level').value)).lines; }
$('refresh-logs').onclick = () => logs().catch(e => notice(e.message));
for (const component of ['launch', 'stop']) $('log-service').add(new Option(component, component));
async function refresh() {
  if (!csrf || inFlight) return;
  inFlight = true;
  try {
    const runtime = await api('/api/runtime');
    renderRuntime(runtime);
    if (runtime.state === 'stopped') {
      render({}); $('connection').textContent = 'Standby'; settingsLoaded = false;
    } else {
      try { render(await api('/api/status')); }
      catch (e) {
        render({}); $('connection').textContent = 'Runtime offline';
        if (!['starting','stopping'].includes(runtime.state)) notice(e.message);
      }
    }
    if (active === 'logs') await logs();
  }
  catch (e) { if (typeof endJoystick === 'function') endJoystick(); $('connection').textContent = 'Unavailable'; $('arm').textContent = 'Unknown'; notice(e.message); }
  finally { inFlight = false; }
}
async function tick() { await refresh(); setTimeout(tick, document.hidden ? 5000 : 2000); }
function clearFrame(message) {
  $('camera-frame').hidden = true; $('camera-frame').removeAttribute('src');
  if (frameURL) URL.revokeObjectURL(frameURL);
  frameURL = null; $('video-state').hidden = false; $('video-state').textContent = message;
}
async function videoTick() {
  const enabled = () => csrf && brainOnline && active === 'vision' && $('live-video').checked && !document.hidden;
  if (enabled()) {
    try {
      const response = await fetch('/api/preview', {signal:AbortSignal.timeout(2500)});
      if (!response.ok) throw new Error('Camera unavailable');
      const blob = await response.blob();
      if (enabled()) {
        const next = URL.createObjectURL(blob), previous = frameURL;
        $('camera-frame').src = next; frameURL = next;
        $('camera-frame').hidden = false; $('video-state').hidden = true;
        if (previous) URL.revokeObjectURL(previous);
      }
    } catch (_) { clearFrame('Camera unavailable'); }
  } else if (frameURL) clearFrame('Video paused');
  setTimeout(videoTick, 250);
}
$('camera-frame').onerror = () => clearFrame('Frame unavailable');
videoTick();
api('/api/session').then(result => { csrf = result.csrf; signed(true); }).catch(() => signed(false)).finally(tick);
