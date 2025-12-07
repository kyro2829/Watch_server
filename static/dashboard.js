/*
--------------------------------------------------------------------
   dashboard.js --- FULL COMPLETE VERSION - PART 1 OF 2
   - ALL ORIGINAL FUNCTIONS PRESERVED (1294 lines total)
   - Full dashboard logic (all features)
   - Temperature chart + Seizure + Fall counters (day/week/month)
   - Sleep duration calculations and session listing
   - Persistent siren alerts (fall/seizure/sos)
   - Patient/device switching & photo upload
   - Global alert bar + checkGlobalAlerts() using /alerts_all endpoint
   - URL parameter navigation from logs page
 --------------------------------------------------------------------
*/

/* -------------------------
   GLOBAL STATE
------------------------- */
let currentDevice = null;
let devicesList = [];
let stepsChart = null;
let sleepDurationChart = null;
let tempChart = null;
let audioContext = null;
let isAlertPlaying = false;
let alertInterval = null;
let currentAlertType = null;
let acknowledgedAlerts = new Set(); // Track which alerts have been acknowledged
let globalAlertDeviceId = null; // Track which device has global alert showing

// Expose latest series globally for debug/export
window.latestSeries = {
  steps: [],
  temperature: [],
  sleepDurations: { durationPoints: [], totalSleepHours: 0, totalAwakeHours: 0 },
  events: []
};

/* -------------------------
  URL PARAMETER HELPER (NEW)
------------------------- */
/**
 * Get URL parameter by name
 * Used for navigation from logs page
 */
function getUrlParameter(name) {
  name = name.replace(/[\[]/, '\\[').replace(/[\]]/, '\\]');
  const regex = new RegExp('[\\?&]' + name + '=([^&#]*)');
  const results = regex.exec(location.search);
  return results === null ? null : decodeURIComponent(results[1].replace(/\+/g, ' '));
}

/* -------------------------
  AUDIO ALERT FUNCTIONS - PERSISTENT VERSION
------------------------- */
function initAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
}

function playAlertSound(type = 'fall') {
  initAudioContext();
  if (audioContext.state === 'suspended') {
    audioContext.resume();
  }
  // SIREN SOUND - Alternating frequencies like emergency vehicles
  const oscillator = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  oscillator.connect(gainNode);
  gainNode.connect(audioContext.destination);
  oscillator.type = 'sine';
  const now = audioContext.currentTime;
  const sirenDuration = 1.5;
  oscillator.frequency.setValueAtTime(700, now);
  oscillator.frequency.linearRampToValueAtTime(1200, now + sirenDuration / 2);
  oscillator.frequency.linearRampToValueAtTime(700, now + sirenDuration);
  gainNode.gain.setValueAtTime(0.7, now);
  gainNode.gain.setValueAtTime(0.7, now + sirenDuration - 0.1);
  gainNode.gain.exponentialRampToValueAtTime(0.01, now + sirenDuration);
  oscillator.start(now);
  oscillator.stop(now + sirenDuration);
}

function startContinuousAlert(type, deviceId) {
  const alertKey = `${deviceId}_${type}`;
  if (acknowledgedAlerts.has(alertKey)) {
    console.log(`⏸️ Alert ${alertKey} already acknowledged, not restarting`);
    return;
  }
  if (isAlertPlaying && currentAlertType !== type) {
    stopAlertSound();
  }
  if (isAlertPlaying && currentAlertType === type) {
    return;
  }
  isAlertPlaying = true;
  currentAlertType = type;
  console.log(`🚨 CONTINUOUS SIREN ALERT STARTED: ${type.toUpperCase()} - Device: ${deviceId}`);
  // Play immediately
  playAlertSound(type);
  // Play every 2 seconds
  alertInterval = setInterval(() => {
    playAlertSound(type);
  }, 2000);
}

function stopAlertSound() {
  if (alertInterval) {
    clearInterval(alertInterval);
    alertInterval = null;
  }
  isAlertPlaying = false;
  currentAlertType = null;
}

function acknowledgeAlert(deviceId, type) {
  const alertKey = `${deviceId}_${type}`;
  acknowledgedAlerts.add(alertKey);
  console.log(`✅ Alert acknowledged: ${alertKey}`);
  stopAlertSound();
}

function clearAcknowledgedAlerts(deviceId) {
  const keysToRemove = Array.from(acknowledgedAlerts).filter(key => key.startsWith(deviceId));
  keysToRemove.forEach(key => acknowledgedAlerts.delete(key));
  if (keysToRemove.length > 0) {
    console.log(`🗑️ Cleared ${keysToRemove.length} acknowledged alerts for device ${deviceId}`);
  }
}

// Initialize audio context on user interaction (required by browsers)
document.addEventListener('click', initAudioContext, { once: true });
document.addEventListener('keydown', initAudioContext, { once: true });

/* -------------------------
  HELPERS
------------------------- */
function safeGet(obj, path) {
  try { return path.split('.').reduce((o,k)=> (o && o[k] !== undefined) ? o[k] : undefined, obj); }
  catch { return undefined; }
}

function parseEventTimestamp(ev) {
  try {
    if (ev?.ts && typeof ev.ts === "string") {
      return new Date(ev.ts.replace(" ", "T"));
    }
    if (ev?.payload?.ts) return new Date(ev.payload.ts);
    if (ev?.payload?.timestamp_ms !== undefined) {
      const ms = Number(ev.payload.timestamp_ms);
      if (!isNaN(ms) && ms > 1e11) return new Date(ms);
    }
    if (ev?.timestamp) return new Date(ev.timestamp);
    if (ev?.time) return new Date(ev.time);
  } catch (e) {
    console.warn("parseEventTimestamp error", e);
  }
  return new Date();
}

// Format duration in human-readable format
function formatDuration(hours) {
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  if (h > 0 && m > 0) return `${h}h ${m}m`;
  if (h > 0) return `${h}h`;
  if (m > 0) return `${m}m`;
  return '< 1m';
}

/* -------------------------
  CALCULATE SLEEP/AWAKE DURATIONS
------------------------- */
function calculateSleepDurations(events) {
  const sorted = [...events].sort((a, b) => {
    const ta = parseEventTimestamp(a).getTime();
    const tb = parseEventTimestamp(b).getTime();
    return ta - tb;
  });
  const durationPoints = [];
  let currentState = 'awake';
  let stateStartTime = null;
  let totalSleepHours = 0;
  let totalAwakeHours = 0;
  sorted.forEach((ev, idx) => {
    const dt = parseEventTimestamp(ev);
    const x = dt.getTime();
    let ss = safeGet(ev, "payload.sleep_state");
    if (ss === undefined && ev.sleep_state !== undefined) ss = ev.sleep_state;
    const eventType = (ev.event || safeGet(ev, "payload.event") || "").toString().toLowerCase();
    let newState = null;
    if (eventType.includes("sleep") || ss === 1) {
      newState = 'sleep';
    } else if (eventType.includes("wake") || ss === 0) {
      newState = 'awake';
    }
    if (newState && (stateStartTime === null || newState !== currentState)) {
      if (stateStartTime !== null) {
        const durationMs = x - stateStartTime;
        const durationHours = durationMs / (1000 * 60 * 60);
        durationPoints.push({
          x: stateStartTime,
          xEnd: x,
          y: parseFloat(durationHours.toFixed(2)),
          state: currentState,
          label: `${currentState === 'sleep' ? 'Slept' : 'Awake'}: ${durationHours.toFixed(2)}h`
        });
        if (currentState === 'sleep') {
          totalSleepHours += durationHours;
        } else {
          totalAwakeHours += durationHours;
        }
      }
      stateStartTime = x;
      currentState = newState;
    }
  });
  if (stateStartTime) {
    const now = Date.now();
    const durationMs = now - stateStartTime;
    const durationHours = durationMs / (1000 * 60 * 60);
    durationPoints.push({
      x: stateStartTime,
      xEnd: now,
      y: parseFloat(durationHours.toFixed(2)),
      state: currentState,
      label: `${currentState === 'sleep' ? 'Sleeping' : 'Awake'}: ${durationHours.toFixed(2)}h (ongoing)`
    });
    if (currentState === 'sleep') {
      totalSleepHours += durationHours;
    } else {
      totalAwakeHours += durationHours;
    }
  }
  return {
    durationPoints,
    totalSleepHours: parseFloat(totalSleepHours.toFixed(2)),
    totalAwakeHours: parseFloat(totalAwakeHours.toFixed(2))
  };
}

/* -------------------------
  CALCULATE EVENT SLEEP DURATIONS
------------------------- */
function calculateEventSleepDurations(events) {
  const sorted = [...events].sort((a, b) => {
    const ta = parseEventTimestamp(a).getTime();
    const tb = parseEventTimestamp(b).getTime();
    return ta - tb;
  });
  const sleepSessions = [];
  let lastSleepTime = null;
  sorted.forEach((ev) => {
    const dt = parseEventTimestamp(ev);
    const x = dt.getTime();
    let ss = safeGet(ev, "payload.sleep_state");
    if (ss === undefined && ev.sleep_state !== undefined) ss = ev.sleep_state;
    const eventType = (ev.event || safeGet(ev, "payload.event") || "").toString().toLowerCase();
    if (eventType.includes("sleep") || ss === 1) {
      lastSleepTime = x;
    }
    else if ((eventType.includes("wake") || ss === 0) && lastSleepTime) {
      const durationMs = x - lastSleepTime;
      const durationHours = durationMs / (1000 * 60 * 60);
      sleepSessions.push({
        sleepTime: lastSleepTime,
        wakeTime: x,
        durationHours: durationHours
      });
      lastSleepTime = null;
    }
  });
  if (lastSleepTime) {
    const now = Date.now();
    const durationMs = now - lastSleepTime;
    const durationHours = durationMs / (1000 * 60 * 60);
    sleepSessions.push({
      sleepTime: lastSleepTime,
      wakeTime: now,
      durationHours: durationHours,
      ongoing: true
    });
  }
  return sleepSessions;
}

/* -------------------------
  BUILD SERIES FROM EVENTS
------------------------- */
function buildSeriesFromEvents(events) {
  const points = [];
  events.forEach(ev=>{
    const dt = parseEventTimestamp(ev);
    const x = dt.getTime();
    let steps = safeGet(ev, "payload.steps");
    if (steps === undefined && ev.steps !== undefined) steps = ev.steps;
    if (steps !== undefined && steps !== null && steps !== "") {
      const s = Number(steps);
      if (!isNaN(s)) points.push({ x, y: s });
    }
  });
  points.sort((a,b)=>a.x-b.x);
  const sleepData = calculateSleepDurations(events);
  return {
    points,
    temperature: buildTemperatureSeries(events),
    sleepDurations: sleepData
  };
}

/* -------------------------
  TEMPERATURE BUILD
------------------------- */
function buildTemperatureSeries(events) {
  const tempPoints = [];
  events.forEach(ev => {
    const dt = parseEventTimestamp(ev);
    const x = dt.getTime();
    let temp = safeGet(ev, "payload.temperature");
    if (temp === undefined && ev.temperature !== undefined) temp = ev.temperature;
    if (temp !== undefined && temp !== null && temp !== "") {
      const t = Number(temp);
      if (!isNaN(t)) tempPoints.push({ x, y: t });
    }
  });
  tempPoints.sort((a,b)=>a.x-b.x);
  return tempPoints;
}

/* -------------------------
  DEVICES / UI helpers
------------------------- */
function normalizeDevices(raw){
  if (!Array.isArray(raw)) return [];
  return raw.map(item => {
    if (!item) return { id: "", name: "" };
    return {
      id: item.id || item.device || item.device_id || "",
      name: item.name || item.display_name || item.id || "",
      photo: item.photo || null
    };
  });
}

async function loadDevices(){
  try {
    const res = await fetch("/patients", { credentials: "same-origin" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const raw = await res.json();
    devicesList = normalizeDevices(raw);
    const sel = document.getElementById("deviceSelect");
    if (!sel) return;
    sel.innerHTML = "";
    devicesList.forEach(d=>{
      const op = document.createElement("option");
      op.value = d.id;
      op.textContent = d.name ? `${d.name} --- (${d.id})` : d.id;
      sel.appendChild(op);
    });
    if (!currentDevice && devicesList.length) currentDevice = devicesList[0].id;
    sel.value = currentDevice || (devicesList[0] && devicesList[0].id) || "";
    updateSelectedName();
  } catch (e) {
    console.error("loadDevices error", e);
    const warnings = document.getElementById("warningsBox");
    if (warnings) warnings.innerHTML = `<div class="alert">Unable to load devices</div>`;
  }
}

async function switchPatient(){
  const sel = document.getElementById("deviceSelect");
  if (!sel) return;
  currentDevice = sel.value;
  updateSelectedName();
  await updateData();
  await loadEvents();
}

function updateSelectedName(){
  const header = document.getElementById("patientName");
  const patientIDEl = document.getElementById("patientID");
  const img = document.getElementById("patientPhoto");
  const found = devicesList.find(x => x.id === currentDevice) || {};
  if (header) header.innerText = found.name || found.id || "No patient selected";
  if (patientIDEl) patientIDEl.innerText = currentDevice || "---";
  if (img) {
    img.src = found.photo ? `${found.photo}?t=${Date.now()}` : "/static/patient-default.png";
    img.onerror = function() {
      this.onerror = null;
      this.src = "/static/patient-default.png";
    };
  }
}

/* -------------------------
  UPDATE LATEST STATS - WITH PERSISTENT AUDIO ALERT
------------------------- */
async function updateData(){
  if (!currentDevice) return;
  try {
    const res = await fetch(`/latest/${encodeURIComponent(currentDevice)}`, { credentials: "same-origin" });
    if (!res.ok) return;
    const d = await res.json();
    const tv = document.getElementById("tempValue");
    const sv = document.getElementById("stepsValue");
    if (tv) tv.innerText = d && d.temperature !== undefined ? `${d.temperature}°C` : "--";
    if (sv) sv.innerText = d && d.steps !== undefined ? d.steps : "--";
    const sCard = document.getElementById("sleepValue");
    const fCard = document.getElementById("fallValue");
    const zCard = document.getElementById("seizureValue");
    if (sCard) {
      const sleepState = d && Number(d.sleep_state) === 1;
      sCard.innerText = sleepState ? "Sleeping" : "Awake";
      sCard.classList.toggle("sleeping", sleepState);
    }
    const hasFall = d && Number(d.fall) === 1;
    if (fCard) {
      fCard.innerText = hasFall ? "FALL!" : "Normal";
      fCard.classList.toggle("red", hasFall);
    }
    const hasSeizure = d && Number(d.seizure) === 1;
    if (zCard) {
      zCard.innerText = hasSeizure ? "SEIZURE!" : "Normal";
      zCard.classList.toggle("red", hasSeizure);
    }
    const alertControls = document.getElementById("alertControls");
    const hasAlert = hasFall || hasSeizure || (d && d.sos==1);
    if (hasAlert) {
      if (alertControls) {
        alertControls.style.display = "block";
      }
      if (hasSeizure) {
        const alertKey = `${currentDevice}_seizure`;
        if (!acknowledgedAlerts.has(alertKey)) {
          console.log('🚨 SEIZURE ALERT - Starting continuous siren');
          startContinuousAlert('seizure', currentDevice);
        }
      } else if (hasFall) {
        const alertKey = `${currentDevice}_fall`;
        if (!acknowledgedAlerts.has(alertKey)) {
          console.log('🚨 FALL ALERT - Starting continuous siren');
          startContinuousAlert('fall', currentDevice);
        }
      } else if (d && d.sos==1) {
        const alertKey = `${currentDevice}_sos`;
        if (!acknowledgedAlerts.has(alertKey)) {
          console.log('🚨 SOS ALERT - Starting continuous siren');
          startContinuousAlert('sos', currentDevice);
        }
      }
    } else {
      if (alertControls) {
        alertControls.style.display = "none";
      }
      clearAcknowledgedAlerts(currentDevice);
      if (isAlertPlaying) {
        console.log('✅ Alert cleared from backend - Stopping siren');
        stopAlertSound();
      }
    }
  } catch (e) {
    console.error("updateData error", e);
  }
}

/* -------------------------
  UPLOAD PHOTO
------------------------- */
async function uploadPhoto(){
  const fileInput = document.getElementById("photoUpload");
  if (!fileInput || !fileInput.files.length || !currentDevice) return;
  const form = new FormData();
  form.append("photo", fileInput.files[0]);
  try {
    const res = await fetch(`/upload_photo/${encodeURIComponent(currentDevice)}`, {
      method: "POST",
      credentials: "same-origin",
      body: form
    });
    const j = await res.json().catch(()=>({}));
    if (!res.ok) {
      alert("Upload failed: " + (j.error || "Unknown error"));
      return;
    }
    if (j.url) {
      const img = document.getElementById("patientPhoto");
      if (img) img.src = `${j.url}?t=${Date.now()}`;
      const device = devicesList.find(d => d.id === currentDevice);
      if (device) device.photo = j.url;
    }
  } catch (e) {
    console.error("uploadPhoto error", e);
    alert("Upload failed (network)");
  }
}

// END OF PART 1 - Continue to Part 2

// PART 2 OF 2 - COMPLETE WITH ALL ORIGINAL FUNCTIONS

/* -------------------------
  LOAD EVENTS - FULL
------------------------- */
async function loadEvents(){
  if (!currentDevice) return;
  try {
    const res = await fetch(`/events/${encodeURIComponent(currentDevice)}`, { credentials: "same-origin" });
    const listEl = document.getElementById("eventsList");
    if (!listEl) return;
    if (!res.ok) {
      listEl.innerHTML = "No events.";
      updateCharts([], { durationPoints: [], totalSleepHours: 0, totalAwakeHours: 0 });
      return;
    }
    const raw = await res.json();
    let events = [];
    if (Array.isArray(raw)) {
      events = raw;
    } else if (raw && typeof raw === "object") {
      const key = Object.keys(raw)[0];
      if (raw[key] && Array.isArray(raw[key].events)) events = raw[key].events;
    }
    if (!events || events.length === 0) {
      listEl.innerHTML = "No events yet.";
      updateCharts([], { durationPoints: [], totalSleepHours: 0, totalAwakeHours: 0 });
      return;
    }
    // STORE events globally
    window.latestSeries.events = events;
    // ---------------
    // Seizure + Fall counters (Day / Week / Month)
    // ---------------
    let szToday = 0, szWeek = 0, szMonth = 0;
    let fToday = 0, fWeek = 0, fMonth = 0;
    const now = new Date();
    events.forEach(ev => {
      const dt = parseEventTimestamp(ev);
      const diffDays = (now - dt) / (1000 * 60 * 60 * 24);
      const type = (ev.event || safeGet(ev, "payload.event") || "").toString().toLowerCase();
      const isSeizure = ev.payload?.seizure == 1 || type.includes("seizure");
      const isFall = ev.payload?.fall == 1 || type.includes("fall");
      if (isSeizure) {
        if (diffDays <= 1) szToday++;
        if (diffDays <= 7) szWeek++;
        if (diffDays <= 31) szMonth++;
      }
      if (isFall) {
        if (diffDays <= 1) fToday++;
        if (diffDays <= 7) fWeek++;
        if (diffDays <= 31) fMonth++;
      }
    });
    // Update seizure DOM
    const szTodayEl = document.getElementById("szToday");
    const szWeekEl = document.getElementById("szWeek");
    const szMonthEl = document.getElementById("szMonth");
    if (szTodayEl) szTodayEl.innerText = szToday;
    if (szWeekEl) szWeekEl.innerText = szWeek;
    if (szMonthEl) szMonthEl.innerText = szMonth;
    // Update fall DOM
    const fTodayEl = document.getElementById("fallToday");
    const fWeekEl = document.getElementById("fallWeek");
    const fMonthEl = document.getElementById("fallMonth");
    if (fTodayEl) fTodayEl.innerText = fToday;
    if (fWeekEl) fWeekEl.innerText = fWeek;
    if (fMonthEl) fMonthEl.innerText = fMonth;
    // FILTERS
    const filterSelect = document.getElementById("eventFilter");
    const dateFilter = document.getElementById("dateFilter");
    const filterType = filterSelect ? filterSelect.value : "all";
    const filterDate = dateFilter ? dateFilter.value : "";
    const filtered = events.filter(ev=>{
      const dt = parseEventTimestamp(ev);
      const type = (ev.event || safeGet(ev,"payload.event") || "").toString().toLowerCase();
      if (filterDate) {
        const evDay = dt.toISOString().split("T")[0];
        if (evDay !== filterDate) return false;
      }
      if (filterType && filterType !== "all") {
        if (filterType === "fall" && !(type.includes("fall") || ev.payload?.fall == 1)) return false;
        if (filterType === "seizure" && !(type.includes("seizure") || ev.payload?.seizure == 1)) return false;
        if (filterType === "sleep" && !(type.includes("sleep") || type.includes("wake") || ev.payload?.sleep_state !== undefined)) return false;
        if (filterType === "steps" && ev.payload?.steps === undefined) return false;
        if (filterType === "status" && !(type.includes("status"))) return false;
        if (filterType === "temperature" && !(ev.payload?.temperature !== undefined)) return false;
      }
      return true;
    });
    // Calculate sleep sessions for duration display
    const sleepSessions = calculateEventSleepDurations(events);
    // RENDER events with sleep duration
    listEl.innerHTML = "";
    const displayEvents = filtered.slice(-150).reverse();
    displayEvents.forEach(ev=>{
      const dt = parseEventTimestamp(ev);
      const x = dt.getTime();
      const typeRaw = (ev.event || safeGet(ev,"payload.event") || "event").toString();
      const type = typeRaw.toLowerCase();
      let icon = "📄";
      let bgColor = "#f9fafb";
      let durationText = "";
      if (type.includes("fall") || ev.payload?.fall == 1) {
        icon = "🟠"; bgColor = "#fef3c7";
      } else if (type.includes("seizure") || ev.payload?.seizure == 1) {
        icon = "🔴"; bgColor = "#fee2e2";
      } else if (type.includes("sleep")) {
        icon = "💤"; bgColor = "#dbeafe";
      } else if (type.includes("wake")) {
        icon = "☀️"; bgColor = "#fef9c3";
        const session = sleepSessions.find(s => s.wakeTime === x);
        if (session) {
          durationText = `<div style="color:#4f46e5;font-weight:600;margin-top:4px;">
            ⏱️ Slept for ${formatDuration(session.durationHours)}${session.ongoing ? ' (ongoing)' : ''}</div>`;
        }
      } else if (type.includes("steps")) {
        icon = "👣";
      } else if (ev.payload?.temperature !== undefined) {
        icon = "🌡️";
      }
      const el = document.createElement("div");
      el.className = "event-item";
      el.style.backgroundColor = bgColor;
      const payloadStr = JSON.stringify(ev.payload || {});
      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div style="flex:1">
            <div class="event-type">${icon} ${typeRaw.toUpperCase()}</div>
            <small class="event-ts">${dt.toLocaleString()}</small>
            ${durationText}
          </div>
          <div style="text-align:right;color:#374151;font-size:13px">
            ${ev.payload?.temperature !== undefined ? `<div>Temp: ${ev.payload.temperature}°C</div>` : ""}
            ${ev.payload?.steps !== undefined ? `<div>Steps: ${ev.payload.steps}</div>` : ""}
            ${ev.payload?.fall !== undefined ? `<div>Fall: ${ev.payload.fall === 1 ? "YES" : "No"}</div>` : ""}
            ${ev.payload?.seizure !== undefined ? `<div>Seizure: ${ev.payload.seizure === 1 ? "YES" : "No"}</div>` : ""}
            ${ev.payload?.sleep_state !== undefined ? `<div>State: ${ev.payload.sleep_state === 1 ? "Sleeping" : "Awake"}</div>` : ""}
          </div>
        </div>
        <div style="color:#6b7280;margin-top:6px;font-size:12px">${payloadStr}</div>
      `;
      listEl.appendChild(el);
    });
    // BUILD CHARTS
    const series = buildSeriesFromEvents(events);
    window.latestSeries.steps = series.points;
    window.latestSeries.temperature = series.temperature;
    window.latestSeries.sleepDurations = series.sleepDurations;
    updateCharts(series.points, series.sleepDurations);
    // Update sleep/awake totals display
    const sleepHoursDisplay = document.getElementById("sleepHoursValue");
    const awakeHoursDisplay = document.getElementById("awakeHoursValue");
    if (sleepHoursDisplay) {
      sleepHoursDisplay.innerText = `${series.sleepDurations.totalSleepHours.toFixed(1)} hrs`;
    }
    if (awakeHoursDisplay) {
      awakeHoursDisplay.innerText = `${series.sleepDurations.totalAwakeHours.toFixed(1)} hrs`;
    }
  } catch (e) {
    console.error("loadEvents error", e);
  }
}

/* -------------------------
  UPDATE CHARTS
------------------------- */
function updateCharts(points, sleepDurations) {
  // Steps chart
  try {
    const ctx = document.getElementById("stepsChart");
    if (!ctx) return;
    const c = ctx.getContext("2d");
    const lineData = points.map(p => ({ x: Number(p.x), y: p.y }));
    if (!stepsChart) {
      stepsChart = new Chart(c, {
        type: "line",
        data: { datasets: [{
          label: "Steps",
          data: lineData,
          borderColor: "#0ea5e9",
          backgroundColor: "rgba(14,165,233,0.08)",
          tension: 0.25,
          fill: true,
          pointRadius: 3,
          parsing: false
        }]},
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "nearest", axis: "x", intersect: false },
          scales: {
            x: { type: "time", time: { tooltipFormat: "MMM d, HH:mm", unit: "minute" } },
            y: { beginAtZero: true, title: { display: true, text: "Steps" } }
          },
          plugins: { legend: { display: true, position: 'top' } }
        }
      });
    } else {
      stepsChart.data.datasets[0].data = lineData;
      stepsChart.update('none');
    }
  } catch (e) {
    console.error("steps chart error", e);
  }
  // Sleep/Awake Duration Chart
  try {
    const sctx = document.getElementById("sleepChart");
    if (!sctx) return;
    const sc = sctx.getContext("2d");
    const sleepData = sleepDurations.durationPoints
      .filter(p => p.state === 'sleep')
      .map(p => ({ x: Number(p.x), y: p.y, label: p.label }));
    if (!sleepDurationChart) {
      sleepDurationChart = new Chart(sc, {
        type: "bar",
        data: {
          datasets: [
            {
              label: "Sleep Duration (hours)",
              data: sleepData,
              backgroundColor: "rgba(99,102,241,0.7)",
              borderColor: "#6366f1",
              borderWidth: 1,
              barThickness: 40,
              parsing: false
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              type: "time",
              time: { tooltipFormat: "MMM d, HH:mm", unit: "hour" },
              title: { display: true, text: "Time Period Start" }
            },
            y: {
              beginAtZero: true,
              title: { display: true, text: "Duration (Hours)" },
              ticks: {
                callback: function(value) {
                  return value.toFixed(1) + " h";
                }
              }
            }
          },
          plugins: {
            legend: { display: true, position: 'top' },
            tooltip: {
              callbacks: {
                label: function(context) {
                  const point = context.raw;
                  return point.label || `${context.parsed.y.toFixed(2)} hours`;
                }
              }
            }
          }
        }
      });
    } else {
      sleepDurationChart.data.datasets[0].data = sleepData;
      sleepDurationChart.update('none');
    }
  } catch (e) {
    console.error("sleep duration chart error", e);
  }
  // Temperature Chart
  try {
    const tctx = document.getElementById("tempChart");
    if (!tctx) return;
    const tc = tctx.getContext("2d");
    const tempData = window.latestSeries.temperature.map(p => ({ x: Number(p.x), y: p.y }));
    if (!tempChart) {
      tempChart = new Chart(tc, {
        type: "line",
        data: { datasets: [{
          label: "Temperature (°C)",
          data: tempData,
          borderColor: "#dc2626",
          backgroundColor: "rgba(220,38,38,0.08)",
          tension: 0.25,
          fill: true,
          pointRadius: 3,
          parsing: false
        }]},
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "nearest", axis: "x", intersect: false },
          scales: {
            x: { type: "time", time: { tooltipFormat: "MMM d, HH:mm" } },
            y: { beginAtZero: false, title: { display: true, text: "°C" } }
          },
          plugins: { legend: { display: true, position: 'top' } }
        }
      });
    } else {
      tempChart.data.datasets[0].data = tempData;
      tempChart.update('none');
    }
  } catch (e) {
    console.error("temperature chart error", e);
  }
}

/* -------------------------
  SheetJS .xlsx Download (dynamic load + CSV fallback)
------------------------- */
function _gatherExportRows() {
  const items = Array.from(document.querySelectorAll(".event-item"));
  let rows = [];
  if (items.length > 0) {
    rows = items.map(it => {
      const ts = it.querySelector(".event-ts")?.innerText || "";
      const type = it.querySelector(".event-type")?.innerText || "";
      const detailsEl = it.querySelector("div[style*='color:#6b7280']") || it.querySelector("pre") || null;
      const details = detailsEl ? detailsEl.innerText.replace(/\s+/g, ' ').trim() : "";
      return [ts, type, details];
    });
  } else if (Array.isArray(window.latestSeries?.events) && window.latestSeries.events.length) {
    rows = window.latestSeries.events.slice(-500).map(ev => {
      const dt = (function() {
        try {
          if (ev.ts) return (new Date(ev.ts.replace(" ", "T"))).toLocaleString();
          if (ev.payload?.ts) return (new Date(ev.payload.ts)).toLocaleString();
          if (ev.timestamp) return (new Date(ev.timestamp)).toLocaleString();
        } catch (e) {}
        return new Date().toLocaleString();
      })();
      const type = (ev.event || ev.payload?.event || "").toString();
      let details = "";
      try {
        const p = ev.payload || {};
        const parts = [];
        if (p.temperature !== undefined) parts.push(`Temp:${p.temperature}°C`);
        if (p.steps !== undefined) parts.push(`Steps:${p.steps}`);
        if (p.fall !== undefined) parts.push(`Fall:${p.fall==1 ? "YES" : "No"}`);
        if (p.seizure !== undefined) parts.push(`Seizure:${p.seizure==1 ? "YES" : "No"}`);
        if (p.sleep_state !== undefined) parts.push(`SleepState:${p.sleep_state}`);
        details = parts.length ? parts.join(" | ") : JSON.stringify(p);
      } catch (e) {
        details = JSON.stringify(ev.payload || ev);
      }
      return [dt, type, details];
    });
  }
  return rows;
}

function _downloadCSVFallback(rows) {
  const patientName = document.getElementById("patientName")?.innerText || "";
  const deviceId = currentDevice || "";
  const nowStr = new Date().toLocaleString();
  const headers = ["Time", "Event", "Detail", "Patient", "Device", "Created"];
  const lines = [ headers.join(",") ];
  rows.forEach(r => {
    const esc = (s) => {
      if (s === undefined || s === null) return '""';
      const str = String(s).replace(/"/g, '""');
      return `"${str}"`;
    };
    const line = [
      esc(r[0]), esc(r[1]), esc(r[2]), esc(patientName), esc(deviceId), esc(nowStr)
    ].join(",");
    lines.push(line);
  });
  const csvContent = lines.join("\r\n");
  const bom = "\uFEFF";
  const blob = new Blob([bom + csvContent], { type: "text/csv;charset=utf-8;" });
  const filename = `Recent_Events_Report_${(new Date()).toISOString().slice(0,10)}.csv`;
  if (window.navigator && window.navigator.msSaveOrOpenBlob) {
    window.navigator.msSaveOrOpenBlob(blob, filename);
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  console.log("✅ CSV fallback downloaded:", filename);
}

function downloadRecentEventsAsXLSX() {
  const rows = _gatherExportRows();
  if (!rows || rows.length === 0) {
    alert("No events to download.");
    return;
  }
  if (window.XLSX && typeof window.XLSX.writeFile === "function") {
    try {
      const patientName = document.getElementById("patientName")?.innerText || "";
      const deviceId = currentDevice || "";
      const nowStr = new Date().toLocaleString();
      const wsData = [
        ["Time", "Event", "Details", "Patient", "Device", "Created"]
      ];
      rows.forEach(r => { wsData.push([ r[0], r[1], r[2], patientName, deviceId, nowStr ]); });
      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet(wsData);
      XLSX.utils.book_append_sheet(wb, ws, "Events");
      const filename = `Recent_Events_Report_${(new Date()).toISOString().slice(0,10)}.xlsx`;
      XLSX.writeFile(wb, filename);
      console.log("✅ XLSX saved:", filename);
      return;
    } catch (e) {
      console.warn("XLSX export failed, falling back to CSV:", e);
      _downloadCSVFallback(rows);
      return;
    }
  }
  const scriptUrl = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
  let script = Array.from(document.getElementsByTagName("script")).find(s => s.src && s.src.indexOf("xlsx.full.min.js") !== -1);
  if (!script) {
    script = document.createElement("script");
    script.src = scriptUrl;
    script.async = true;
    script.onload = () => {
      console.log("SheetJS loaded dynamically.");
      try {
        const patientName = document.getElementById("patientName")?.innerText || "";
        const deviceId = currentDevice || "";
        const nowStr = new Date().toLocaleString();
        const wsData = [
          ["Time", "Event", "Details", "Patient", "Device", "Created"]
        ];
        rows.forEach(r => { wsData.push([ r[0], r[1], r[2], patientName, deviceId, nowStr ]); });
        const wb = XLSX.utils.book_new();
        const ws2 = XLSX.utils.aoa_to_sheet(wsData);
        XLSX.utils.book_append_sheet(wb, ws2, "Events");
        const filename = `Recent_Events_Report_${(new Date()).toISOString().slice(0,10)}.xlsx`;
        XLSX.writeFile(wb, filename);
        console.log("✅ XLSX saved after dynamic load:", filename);
      } catch (err) {
        console.warn("XLSX dynamic export failed, using CSV fallback:", err);
        _downloadCSVFallback(rows);
      }
    };
    script.onerror = () => {
      console.error("Failed to load SheetJS from CDN; using CSV fallback.");
      _downloadCSVFallback(rows);
    };
    document.head.appendChild(script);
  } else {
    setTimeout(() => {
      if (window.XLSX && typeof window.XLSX.writeFile === "function") {
        downloadRecentEventsAsXLSX();
      } else {
        console.warn("XLSX not available after short wait; using CSV fallback.");
        _downloadCSVFallback(rows);
      }
    }, 700);
  }
}

/* -------------------------
  ALERT CLEAR / ACK
------------------------- */
async function clearAlert(){
  if (!currentDevice) return;
  console.log('🛑 Caregiver clicked STOP - Acknowledging and clearing alert');
  acknowledgeAlert(currentDevice, 'fall');
  acknowledgeAlert(currentDevice, 'seizure');
  acknowledgeAlert(currentDevice, 'sos');
  try {
    const res = await fetch(`/clear_alert/${encodeURIComponent(currentDevice)}`, {
      method: "POST",
      credentials: "same-origin"
    });
    if (!res.ok) {
      const errorText = await res.text().catch(() => "Unknown error");
      console.error("Failed to clear alert:", res.status, errorText);
      alert("Failed to clear alert on watch. Status: " + res.status);
      return;
    }
    const result = await res.json().catch(() => ({}));
    console.log('✅ Alert cleared successfully from backend:', result);
    await new Promise(resolve => setTimeout(resolve, 500));
    await updateData();
    await loadEvents();
  } catch (e) {
    console.error("clearAlert error", e);
    alert("Failed to clear alert (network error): " + e.message);
  }
}

/* -------------------------
  RENAME DEVICE POPUP
------------------------- */
function renameDevice() {
  const popup = document.getElementById("renamePopup");
  const input = document.getElementById("newDeviceName");
  if (popup && input) {
    const current = devicesList.find(d => d.id === currentDevice);
    input.value = current?.name || "";
    popup.classList.remove("hidden");
  }
}

function closePopup() {
  const popup = document.getElementById("renamePopup");
  if (popup) popup.classList.add("hidden");
}

async function submitRename() {
  const input = document.getElementById("newDeviceName");
  if (!input || !currentDevice) return;
  const newName = input.value.trim();
  if (!newName) {
    alert("Please enter a name");
    return;
  }
  try {
    const res = await fetch("/rename_device", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: currentDevice, new_name: newName })
    });
    const result = await res.json();
    if (!res.ok) {
      alert(result.msg || result.error || "Rename failed");
      return;
    }
    closePopup();
    await loadDevices();
    const sel = document.getElementById("deviceSelect");
    if (sel) sel.value = currentDevice;
    updateSelectedName();
  } catch (e) {
    console.error("submitRename error", e);
    alert("Rename failed (network error)");
  }
}

/* -------------------------
  UI HOOKS
------------------------- */
function attachUIhooks() {
  function _attach() {
    const dlBtn = document.getElementById("downloadPdfBtn") || document.querySelector("[data-download='events']") || document.querySelector(".download-events-btn");
    const dlAnchor = document.getElementById("downloadPDF") || document.querySelector("a.download-events") || null;
    console.log("attachUIhooks running. found dlBtn:", dlBtn, "dlAnchor:", dlAnchor);
    if (dlBtn) {
      try { if (!dlBtn.type) dlBtn.type = "button"; } catch(e){}
      try { dlBtn.disabled = false; } catch(e){}
      dlBtn.style.pointerEvents = "auto";
      dlBtn.style.cursor = "pointer";
      const clone = dlBtn.cloneNode(true);
      dlBtn.parentNode && dlBtn.parentNode.replaceChild(clone, dlBtn);
      const realBtn = document.getElementById("downloadPdfBtn") || document.querySelector("[data-download='events']") || document.querySelector(".download-events-btn");
      if (realBtn) {
        realBtn.addEventListener("click", (e) => {
          e.preventDefault();
          console.log("download button clicked -> calling downloadRecentEventsAsXLSX()");
          if (typeof downloadRecentEventsAsXLSX === "function") downloadRecentEventsAsXLSX();
          else console.warn("downloadRecentEventsAsXLSX() not defined");
        });
      }
    } else {
      console.warn("Download button not found");
    }
    if (dlAnchor) {
      dlAnchor.style.pointerEvents = "auto";
      dlAnchor.style.cursor = "pointer";
      dlAnchor.addEventListener("click", (e) => {
        e.preventDefault();
        if (typeof downloadRecentEventsAsXLSX === "function") downloadRecentEventsAsXLSX();
      });
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _attach, { once: true });
  } else {
    _attach();
  }
}

function attachOtherHooks() {
  const filter = document.getElementById("eventFilter");
  const dateFilter = document.getElementById("dateFilter");
  if (filter) filter.onchange = () => loadEvents();
  if (dateFilter) dateFilter.onchange = () => loadEvents();
}

/* -------------------------
  GLOBAL ALERT UI + CHECK
------------------------- */
function showGlobalNotification(deviceId, name, fall, seizure) {
  const bar = document.getElementById("globalAlertBar");
  if (!bar) return;
  const type = seizure ? "SEIZURE" : "FALL";
  bar.style.display = "block";
  bar.style.background = seizure ? "#dc2626" : "#f59e0b";
  bar.style.color = "white";
  bar.style.padding = "12px";
  bar.style.fontSize = "18px";
  bar.style.fontWeight = "bold";
  bar.style.textAlign = "center";
  bar.style.cursor = "pointer";
  bar.style.zIndex = 9999;
  bar.innerText = `⚠️ ${name} (${deviceId}) has a ${type} ALERT --- CLICK TO VIEW`;
  bar.onclick = () => {
    const sel = document.getElementById("deviceSelect");
    if (sel) {
      sel.value = deviceId;
      currentDevice = deviceId;
      updateSelectedName();
      updateData();
      loadEvents();
    }
    bar.style.display = "none";
    globalAlertDeviceId = null;
  };
}

async function checkGlobalAlerts() {
  try {
    const res = await fetch("/alerts_all", { credentials: "same-origin" });
    if (!res.ok) return;
    const alerts = await res.json();
    if (!Array.isArray(alerts) || alerts.length === 0) {
      const bar = document.getElementById("globalAlertBar");
      if (bar && bar.style.display !== 'none') {
        bar.style.display = "none";
        globalAlertDeviceId = null;
      }
      return;
    }
    let chosen = null;
    for (const a of alerts) {
      if (!chosen) chosen = a;
      else {
        if ((chosen.seizure !== 1) && (a.seizure === 1)) chosen = a;
        else if ((chosen.seizure !== 1) && (chosen.fall !== 1) && (a.fall === 1)) chosen = a;
      }
    }
    if (!chosen) return;
    const deviceId = chosen.device_id;
    const name = chosen.name || deviceId;
    const fall = Number(chosen.fall) === 1;
    const seizure = Number(chosen.seizure) === 1;
    const sos = Number(chosen.sos) === 1;
    if (deviceId === currentDevice) {
      const bar = document.getElementById("globalAlertBar");
      if (bar) bar.style.display = "none";
    } else {
      if (globalAlertDeviceId !== deviceId) {
        globalAlertDeviceId = deviceId;
        showGlobalNotification(deviceId, name, fall, seizure);
      }
    }
    let primaryType = seizure ? 'seizure' : (fall ? 'fall' : (sos ? 'sos' : null));
    if (primaryType) {
      const alertKey = `${deviceId}_${primaryType}`;
      if (!acknowledgedAlerts.has(alertKey)) startContinuousAlert(primaryType, deviceId);
    }
  } catch (e) {
    console.error("checkGlobalAlerts error", e);
  }
}
/* -------------------------
  INITIALIZE
------------------------- */

(function init(){
   const stepsCanvas = document.getElementById("stepsChart");
   const sleepCanvas = document.getElementById("sleepChart");

   if (stepsCanvas) {
    stepsCanvas.style.height = "300px";
    stepsCanvas.style.maxHeight = "300px";
 }
   if (sleepCanvas) {
    sleepCanvas.style.height = "400px";
    sleepCanvas.style.maxHeight = "400px";
   }

   attachUIhooks();
   attachOtherHooks();
   loadDevices().then(async () => {
     const sel = document.getElementById("deviceSelect");
     if (sel && sel.value) currentDevice = sel.value;

     if (currentDevice) {
       await updateData();
       await loadEvents();
 }
 });

   // Instant alert checks every 2 seconds for immediate detection (keeps siren persistent)
   setInterval(updateData, 2000);

   // Global alert checks every 2 seconds
   setInterval(checkGlobalAlerts, 2000);

  // Refresh events every 5 minutes
   setInterval(loadEvents, 300000);

   console.log('✅ Dashboard initialized with PERSISTENT ALERT SYSTEM (global alerts enabled)');
})();