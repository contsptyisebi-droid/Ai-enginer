/**
 * Scuderia AI – Frontend
 * Push-to-Talk logic, Web Audio API radio distortion, waveform visualiser,
 * synthesised radio beep sounds, and live telemetry polling.
 */

'use strict';

// ─── Config ───────────────────────────────────────────────────────────────────
const API_BASE           = '';          // empty = same origin
const TELEMETRY_POLL_MS  = 800;         // how often to refresh the telemetry panel
const AUDIO_MIME         = getSupportedMimeType();

// ─── State ────────────────────────────────────────────────────────────────────
let isRecording        = false;
let mediaRecorder      = null;
let audioChunks        = [];
let audioCtx           = null;
let waveformAnimId     = null;
let micAnalyser        = null;
let micStream          = null;

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const pttBtn          = document.getElementById('ptt-btn');
const radioStateLabel = document.getElementById('radio-state-label');
const transcriptEl    = document.getElementById('transcript');
const engineerReplyEl = document.getElementById('engineer-reply');
const engineerStateEl = document.getElementById('engineer-state');
const waveformCanvas  = document.getElementById('waveform');
const waveCtx         = waveformCanvas.getContext('2d');
const telemetryDot    = document.getElementById('telemetry-status');

// ─── Audio context (lazy init on first user gesture) ─────────────────────────
function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  return audioCtx;
}

// ─── Mime type ────────────────────────────────────────────────────────────────
function getSupportedMimeType() {
  const types = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ];
  for (const t of types) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}

// ─── Synthesise radio beep using Web Audio API ────────────────────────────────
/**
 * @param {'open'|'close'} type
 */
function playRadioBeep(type) {
  const ctx = ensureAudioCtx();

  const freq  = type === 'open' ? 1200 : 900;
  const dur   = type === 'open' ? 0.08 : 0.12;

  const osc   = ctx.createOscillator();
  const gain  = ctx.createGain();
  const dist  = ctx.createWaveShaper();

  // Slight distortion for the radio click feel
  dist.curve = makeDistortionCurve(80);

  osc.connect(dist);
  dist.connect(gain);
  gain.connect(ctx.destination);

  osc.type      = 'square';
  osc.frequency.setValueAtTime(freq, ctx.currentTime);

  gain.gain.setValueAtTime(0.4, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);

  osc.start(ctx.currentTime);
  osc.stop(ctx.currentTime + dur);
}

function makeDistortionCurve(amount) {
  const n     = 256;
  const curve = new Float32Array(n);
  const k     = amount;
  for (let i = 0; i < n; i++) {
    const x = (i * 2) / n - 1;
    curve[i] = ((Math.PI + k) * x) / (Math.PI + k * Math.abs(x));
  }
  return curve;
}

// ─── Apply two-way radio distortion to a decoded AudioBuffer ─────────────────
async function applyRadioEffect(mp3ArrayBuffer) {
  const ctx     = ensureAudioCtx();
  const decoded = await ctx.decodeAudioData(mp3ArrayBuffer);

  // Offline context to process the whole buffer
  const offline = new OfflineAudioContext(
    decoded.numberOfChannels,
    decoded.length,
    decoded.sampleRate,
  );

  const source    = offline.createBufferSource();
  source.buffer   = decoded;

  // Bandpass filter to simulate narrow radio frequency response (300–3400 Hz)
  const bandpass  = offline.createBiquadFilter();
  bandpass.type   = 'bandpass';
  bandpass.frequency.value = 1800;
  bandpass.Q.value         = 0.7;

  // Compressor to get that pumping radio dynamics
  const compressor = offline.createDynamicsCompressor();
  compressor.threshold.value = -24;
  compressor.knee.value      = 2;
  compressor.ratio.value     = 12;
  compressor.attack.value    = 0.001;
  compressor.release.value   = 0.1;

  // Mild distortion
  const distortion = offline.createWaveShaper();
  distortion.curve = makeDistortionCurve(40);

  // Gain boost
  const gainNode = offline.createGain();
  gainNode.gain.value = 1.6;

  source.connect(bandpass);
  bandpass.connect(distortion);
  distortion.connect(compressor);
  compressor.connect(gainNode);
  gainNode.connect(offline.destination);

  source.start(0);
  const processedBuffer = await offline.startRendering();
  return processedBuffer;
}

// ─── Play an AudioBuffer through the main audio context ──────────────────────
function playAudioBuffer(buffer) {
  return new Promise((resolve) => {
    const ctx    = ensureAudioCtx();
    const source = ctx.createBufferSource();
    source.buffer    = buffer;
    source.connect(ctx.destination);
    source.onended   = resolve;
    source.start(0);
  });
}

// ─── Waveform visualiser ──────────────────────────────────────────────────────
function startWaveform(analyser) {
  const bufferLen  = analyser.frequencyBinCount;
  const dataArray  = new Uint8Array(bufferLen);
  const W          = waveformCanvas.width;
  const H          = waveformCanvas.height;

  function draw() {
    waveformAnimId = requestAnimationFrame(draw);
    analyser.getByteTimeDomainData(dataArray);

    waveCtx.clearRect(0, 0, W, H);
    waveCtx.lineWidth   = 2;
    waveCtx.strokeStyle = '#e8002d';
    waveCtx.beginPath();

    const sliceWidth = W / bufferLen;
    let x = 0;
    for (let i = 0; i < bufferLen; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * H) / 2;
      if (i === 0) waveCtx.moveTo(x, y);
      else         waveCtx.lineTo(x, y);
      x += sliceWidth;
    }
    waveCtx.lineTo(W, H / 2);
    waveCtx.stroke();
  }
  draw();
}

function stopWaveform() {
  if (waveformAnimId) {
    cancelAnimationFrame(waveformAnimId);
    waveformAnimId = null;
  }
  waveCtx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
  // Draw flat line
  waveCtx.lineWidth   = 1;
  waveCtx.strokeStyle = '#2a2a3a';
  waveCtx.beginPath();
  waveCtx.moveTo(0, waveformCanvas.height / 2);
  waveCtx.lineTo(waveformCanvas.width, waveformCanvas.height / 2);
  waveCtx.stroke();
}

// ─── Start recording ──────────────────────────────────────────────────────────
async function startRecording() {
  if (isRecording) return;
  isRecording = true;

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (err) {
    console.error('Microphone access denied:', err);
    setRadioState('STANDBY');
    isRecording = false;
    return;
  }

  // Set up analyser for waveform
  const ctx     = ensureAudioCtx();
  micAnalyser   = ctx.createAnalyser();
  micAnalyser.fftSize = 512;
  const srcNode = ctx.createMediaStreamSource(micStream);
  srcNode.connect(micAnalyser);

  // Start visualiser
  startWaveform(micAnalyser);

  // Play "open" beep
  playRadioBeep('open');

  // Start MediaRecorder
  audioChunks  = [];
  const opts   = AUDIO_MIME ? { mimeType: AUDIO_MIME } : {};
  mediaRecorder = new MediaRecorder(micStream, opts);
  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  };
  mediaRecorder.start(100);  // collect in 100ms chunks

  setRadioState('RECORDING');
  pttBtn.classList.add('active');
  transcriptEl.textContent = '…';
}

// ─── Stop recording & send to backend ─────────────────────────────────────────
async function stopRecording() {
  if (!isRecording) return;
  isRecording = false;

  stopWaveform();

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }

  pttBtn.classList.remove('active');

  await new Promise((resolve) => {
    if (mediaRecorder) {
      mediaRecorder.onstop = resolve;
    } else {
      resolve();
    }
  });

  if (audioChunks.length === 0) {
    setRadioState('STANDBY');
    return;
  }

  setRadioState('PROCESSING');
  engineerStateEl.textContent = 'Transcribing…';

  const mimeType = (AUDIO_MIME || 'audio/webm').split(';')[0];
  const blob     = new Blob(audioChunks, { type: mimeType });

  try {
    const formData = new FormData();
    formData.append('audio', blob, 'voice.' + mimeType.split('/')[1]);

    const response = await fetch(`${API_BASE}/api/voice`, {
      method: 'POST',
      body:   formData,
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`Server error ${response.status}: ${err}`);
    }

    const driverText   = response.headers.get('X-Driver-Text')   || '';
    const engineerText = response.headers.get('X-Engineer-Text') || '';

    transcriptEl.textContent    = driverText ? `"${driverText}"` : '';
    engineerReplyEl.textContent = engineerText;
    engineerStateEl.textContent = '';

    setRadioState('PLAYING');

    // Decode & apply radio effect
    const mp3Buffer     = await response.arrayBuffer();
    const processedBuf  = await applyRadioEffect(mp3Buffer);

    // Play closing beep first then TTS
    playRadioBeep('close');
    await new Promise(r => setTimeout(r, 120));
    await playAudioBuffer(processedBuf);

    // Play another beep to signal end of transmission
    playRadioBeep('close');

    setRadioState('STANDBY');

  } catch (err) {
    console.error('Voice API error:', err);
    const msg = err.message || '';
    let displayMsg = 'Communication error – check API key.';
    if (msg.includes('Server error')) {
      try {
        const jsonPart = msg.substring(msg.indexOf('{'));
        const parsed = JSON.parse(jsonPart);
        if (parsed.detail) displayMsg = parsed.detail;
      } catch { /* use default */ }
    }
    engineerReplyEl.textContent = '⚠ ' + displayMsg;
    setRadioState('STANDBY');
  }
}

// ─── Radio state helper ───────────────────────────────────────────────────────
function setRadioState(state) {
  radioStateLabel.textContent  = state;
  radioStateLabel.className    = 'radio-state-label ' + state.toLowerCase();
}

// ─── PTT button listeners ─────────────────────────────────────────────────────
pttBtn.addEventListener('pointerdown',  (e) => { e.preventDefault(); startRecording(); });
pttBtn.addEventListener('pointerup',    (e) => { e.preventDefault(); stopRecording();  });
pttBtn.addEventListener('pointerleave', (e) => { if (isRecording) stopRecording();     });

// Keyboard shortcut: Space bar (and backtick ` as alternative for when game uses Space)
window.addEventListener('keydown', (e) => {
  if ((e.code === 'Space' || e.code === 'Backquote') && !e.repeat && !isRecording) {
    e.preventDefault();
    startRecording();
  }
});
window.addEventListener('keyup', (e) => {
  if ((e.code === 'Space' || e.code === 'Backquote') && isRecording) {
    e.preventDefault();
    stopRecording();
  }
});

// ─── Focus detection ──────────────────────────────────────────────────────────
const focusWarning = document.getElementById('focus-warning');
function updateFocusWarning() {
  if (focusWarning) {
    focusWarning.style.display = (document.hidden || !document.hasFocus()) ? 'block' : 'none';
  }
}
document.addEventListener('visibilitychange', updateFocusWarning);
window.addEventListener('blur', updateFocusWarning);
window.addEventListener('focus', updateFocusWarning);

// ─── Live telemetry polling ───────────────────────────────────────────────────
async function pollTelemetry() {
  try {
    const resp = await fetch(`${API_BASE}/api/telemetry`);
    if (!resp.ok) return;
    const d = await resp.json();

    telemetryDot.className = 'status-dot connected';

    // Main gauges
    setText('t-position',  d.position > 0 ? `P${d.position}` : '–');
    setText('t-lap',       d.current_lap   || '–');
    setText('t-speed',     d.speed_kmh > 0 ? `${d.speed_kmh}<small> km/h</small>` : '–<small> km/h</small>', true);
    setText('t-gear',      gearLabel(d.gear));
    setText('t-tyre',      d.tyre_name     || '–');
    setText('t-tyre-age',  d.tyre_age_laps != null ? `${d.tyre_age_laps}<small> laps</small>` : '–<small> laps</small>', true);
    setText('t-fuel',      d.fuel_in_tank  != null ? `${d.fuel_in_tank.toFixed(1)}<small> kg</small>` : '–<small> kg</small>', true);
    setText('t-fuel-delta', d.fuel_remaining_laps != null ? `${d.fuel_remaining_laps.toFixed(1)}<small> laps</small>` : '–<small> laps</small>', true);
    setText('t-ers',       d.ers_store_pct != null ? `${d.ers_store_pct}<small>%</small>` : '–<small>%</small>', true);
    setText('t-ers-mode',  d.ers_deploy_mode || '–');
    setText('t-drs',       d.drs_active ? 'ON' : 'OFF');
    setText('t-flag',      d.fia_flag || '–');

    // Colour coding
    document.getElementById('t-drs').style.color = d.drs_active ? 'var(--green)' : 'var(--muted)';

    // Tyre wear bars [RL, RR, FL, FR]
    const labels = ['rl', 'rr', 'fl', 'fr'];
    const wear   = d.tyre_wear || [0, 0, 0, 0];
    labels.forEach((lbl, i) => {
      const pct  = Math.min(100, Math.max(0, wear[i]));
      const bar  = document.getElementById(`wear-${lbl}`);
      const pctEl = document.getElementById(`wear-${lbl}-pct`);
      if (bar && pctEl) {
        bar.style.width = `${pct}%`;
        bar.style.background = wearColor(pct);
        pctEl.textContent   = `${pct.toFixed(0)}%`;
        pctEl.style.color   = wearColor(pct);
      }
    });

    // Damage
    setDmg('d-fl-wing',   d.front_left_wing_dmg);
    setDmg('d-fr-wing',   d.front_right_wing_dmg);
    setDmg('d-rear-wing', d.rear_wing_dmg);
    setDmg('d-floor',     (d.floor_dmg   || 0));
    setDmg('d-diffuser',  (d.diffuser_dmg || 0));
    setDmg('d-sidepod',   (d.sidepod_dmg  || 0));
    setDmg('d-gearbox',   d.gearbox_dmg);
    setDmg('d-engine',    d.engine_dmg);

    // Fault badges
    setFault('fault-drs',    d.drs_fault);
    setFault('fault-ers',    d.ers_fault);
    setFault('fault-blown',  d.engine_blown);
    setFault('fault-seized', d.engine_seized);

    // Penalties
    setText('p-penalties', d.penalties_sec > 0 ? `${d.penalties_sec}s` : '0s');
    setText('p-warnings',  d.total_warnings || 0);
    setText('p-pitstops',  d.pit_stops      || 0);

    // Delta times (ms → seconds)
    setText('delta-leader', formatDelta(d.delta_to_leader_ms));
    setText('delta-ahead',  formatDelta(d.delta_to_front_ms));

  } catch {
    telemetryDot.className = 'status-dot disconnected';
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function setText(id, value, html = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (html) el.innerHTML = value;
  else      el.textContent = value;
}

function gearLabel(gear) {
  if (gear === -1) return 'R';
  if (gear === 0)  return 'N';
  return gear != null ? String(gear) : '–';
}

function wearColor(pct) {
  if (pct >= 75) return 'var(--accent)';
  if (pct >= 50) return 'var(--yellow)';
  return 'var(--green)';
}

function setDmg(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = `${value || 0}%`;
  el.className   = 'damage-value';
  if (value >= 50)      el.classList.add('critical');
  else if (value >= 20) el.classList.add('warn');
}

function setFault(id, active) {
  const el = document.getElementById(id);
  if (!el) return;
  if (active) el.classList.add('active');
  else        el.classList.remove('active');
}

function formatDelta(ms) {
  if (!ms || ms <= 0) return '–';
  return `+${(ms / 1000).toFixed(3)}s`;
}

// ─── Draw idle flat line on load ──────────────────────────────────────────────
stopWaveform();

// ─── Start telemetry polling ──────────────────────────────────────────────────
pollTelemetry();
setInterval(pollTelemetry, TELEMETRY_POLL_MS);
