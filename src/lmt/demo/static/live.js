"use strict";
// lmt app's page (G5, F12): Start and Stop a live session, its text as it's spoken (`text` events), each line's
// speaker once it's final (`words` events), and a link to the saved transcript in F8's page when it ends.
const $ = (id) => document.getElementById(id);
let source = null;
let pending = [];          // words shown before their label, as [text]
const segments = new Map(); // segment number -> its <li>

function status(text) { $("status").textContent = text; }
function error(text) { $("error").textContent = text || ""; $("error").hidden = !text; }
function running(on) { $("start").disabled = on; $("stop").disabled = !on; $("engine").disabled = on; }

function showPending() { $("provisional").textContent = pending.join(" "); }

function onEvent(ev) {
  if (ev.type === "start") {
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
  });
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body || {}) });
  return [r.ok, await r.json()];
}

$("start").addEventListener("click", async () => {
  error("");
  $("saved").hidden = true;
  $("lines").textContent = "";
  segments.clear();
  pending = [];
  showPending();
  const [ok, doc] = await post("api/start", { engine: $("engine").value });
  if (!ok) { error(doc.error || "couldn't start"); return; }
  running(true);
  status("Starting…");
  listen();
});

$("stop").addEventListener("click", async () => {
  $("stop").disabled = true;
  status("Stopping…");
  await post("api/stop");
});

fetch("api/state").then((r) => r.json()).then((s) => {
  if (s.running) { running(true); status("Listening…"); listen(); }
});
