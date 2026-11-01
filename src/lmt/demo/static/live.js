"use strict";
// lmt app's page (G5, F12). The app loads a session for the chosen engine as soon as the page opens (or the engine
// changes), and shows its progress; Start then begins at once, or as soon as the models are loaded. The text shows
// as it's spoken (`text` events), each line's speaker once it's final (`words`), and the saved transcript's link.
const $ = (id) => document.getElementById(id);
const ENGINE_NAMES = { kyutai: "Kyutai STT 1B", apple: "Apple's recognizer" };
let source = null;
let pending = [];           // words shown before their label
const segments = new Map(); // segment number -> its <li>
let started = false;        // this page pressed Start for the current session
let ready = false;
let creep = null;           // the bar's slow advance through the long speaker-model stage

function status(text) { $("status").textContent = text; }
function error(text) { $("error").textContent = text || ""; $("error").hidden = !text; }
function running(on) { $("start").disabled = on; $("stop").disabled = !on; $("engine").disabled = on; }
function showPending() { $("provisional").textContent = pending.join(" "); }

function progress(value, label) {
  clearInterval(creep);
  creep = null;
  $("loading").hidden = false;
  $("loadbar").value = value;
  $("loadstage").textContent = label;
}

function loaded() {
  clearInterval(creep);
  creep = null;
  ready = true;
  $("loadbar").value = 1;
  $("loadstage").textContent = "Ready";
  setTimeout(() => { if (ready) $("loading").hidden = true; }, 600);
  if (!started && $("saved").hidden) status("Ready: press Start and speak");  // "Saved" stays while its link shows
}

function onEvent(ev) {
  if (ev.type === "loading" && ev.stage === "engine") {
    progress(0.3, `Loading ${ENGINE_NAMES[ev.engine] || ev.engine}…`);
  } else if (ev.type === "loading" && ev.stage === "speakers") {
    progress(0.4, "Loading the speaker models…");
    // About 15 s on the M4: creep toward 95 %, so the bar keeps moving.
    creep = setInterval(() => { const b = $("loadbar"); b.value = Math.min(0.95, b.value + 0.55 / 30); }, 500);
  } else if (ev.type === "ready") {
    loaded();
  } else if (ev.type === "start") {
    status("Listening…");
  } else if (ev.type === "text") {
    if (ev.starts_word || pending.length === 0) pending.push(ev.text.trim());
    else pending[pending.length - 1] += ev.text;
    showPending();
  } else if (ev.type === "words") {
    let li = segments.get(ev.segment);
    if (!li) {
      li = document.createElement("li");
      li.dataset.segment = ev.segment;
      const who = document.createElement("strong");
      who.textContent = ev.label + ": ";
      li.append(who, document.createElement("span"));
      segments.set(ev.segment, li);
      $("lines").append(li);
    }
    const span = li.querySelector("span");
    const words = ev.words.map((w) => w.text);
    span.textContent = (span.textContent ? span.textContent + " " : "") + words.join(" ");
    pending = pending.slice(words.length);
    showPending();
  } else if (ev.type === "stop") {
    status("Saving…");
  }
}

function listen() {
  if (source) source.close();
  source = new EventSource("api/events");
  source.onmessage = (m) => onEvent(JSON.parse(m.data));
  source.addEventListener("end", (m) => {
    const end = JSON.parse(m.data);
    source.close();
    source = null;
    if (!started) {          // a session loaded ahead and cancelled (the engine changed), or one that failed to load
      if (end.code) { error(end.error || `loading failed (exit ${end.code})`); status("Couldn't load"); }
      return;
    }
    started = false;
    running(false);
    pending = [];
    showPending();
    if (end.code === 0 && end.view) {
      status("Saved");
      $("saved").hidden = false;
    } else {
      status("Stopped");
      error(end.error || `the session exited with ${end.code}`);
    }
    prepare();               // the app is loading the next one; show its progress
  });
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body || {}) });
  return [r.ok, await r.json()];
}

async function prepare() {
  ready = false;
  progress(0.05, "Starting…");
  const [ok, doc] = await post("api/prepare", { engine: $("engine").value });
  if (!ok) { error(doc.error || "couldn't load the models"); return; }
  if (doc.session && doc.session.stage === "ready") loaded();
  listen();
}

$("engine").addEventListener("change", () => { if (!started) prepare(); });

$("start").addEventListener("click", async () => {
  error("");
  $("saved").hidden = true;
  $("lines").textContent = "";
  segments.clear();
  pending = [];
  showPending();
  const [ok, doc] = await post("api/start", { engine: $("engine").value });
  if (!ok) { error(doc.error || "couldn't start"); return; }
  started = true;
  running(true);
  status(ready ? "Starting…" : "Starting as soon as the models are loaded…");
  listen();
});

$("stop").addEventListener("click", async () => {
  $("stop").disabled = true;
  status("Stopping…");
  await post("api/stop");
});

fetch("api/state").then((r) => r.json()).then((s) => {
  if (s.running) { started = true; running(true); status("Listening…"); $("loading").hidden = true; listen(); }
  else prepare();
});
