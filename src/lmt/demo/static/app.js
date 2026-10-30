// lmt demo: fills the page from transcript.json and follows the audio
// (design/internal/g3-stage1.md §2a, "The page"). Every string goes in through textContent.
"use strict";

const audio = document.getElementById("audio");
const errorBox = document.getElementById("error");
const speakerList = document.getElementById("speakers");
const segmentList = document.getElementById("segments");
let buttons = [];

function pad(n) {
  return String(n).padStart(2, "0");
}

// The start rounded down to the second, as HH:MM:SS, as the text export writes it.
function clock(t) {
  const seconds = Math.floor(Math.round(t * 1000) / 1000);
  return pad(Math.floor(seconds / 3600)) + ":" + pad(Math.floor(seconds / 60) % 60) + ":" + pad(seconds % 60);
}

// Speaking time as M:SS.
function duration(t) {
  const seconds = Math.floor(t);
  return Math.floor(seconds / 60) + ":" + pad(seconds % 60);
}

function span(className, text) {
  const el = document.createElement("span");
  el.className = className;
  el.textContent = text;
  return el;
}

function showError(text) {
  errorBox.textContent = "The transcript can't be read: " + text;
  errorBox.hidden = false;
}

function render(transcript) {
  const labels = new Map();
  const spoken = new Map();
  for (const speaker of transcript.speakers) {
    labels.set(speaker.id, speaker.label);
    spoken.set(speaker.id, 0);
  }
  for (const segment of transcript.segments) {
    spoken.set(segment.speaker, (spoken.get(segment.speaker) || 0) + (segment.end - segment.start));
  }

  speakerList.replaceChildren();
  for (const speaker of transcript.speakers) {
    const li = document.createElement("li");
    li.dataset.speaker = speaker.id;
    li.textContent = speaker.label + " · " + duration(spoken.get(speaker.id));
    speakerList.append(li);
  }

  segmentList.replaceChildren();
  buttons = transcript.segments.map((segment, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "segment";
    button.dataset.index = String(i);
    button.dataset.start = String(segment.start);
    button.dataset.speaker = segment.speaker;
    button.append(
      span("time", clock(segment.start)),
      span("speaker", labels.get(segment.speaker) || segment.speaker),
      span("text", segment.text),
    );
    button.addEventListener("click", () => {
      audio.currentTime = segment.start;
      // A refused play (autoplay rules) leaves the controls as they are.
      audio.play().catch(() => {});
    });
    const li = document.createElement("li");
    li.append(button);
    segmentList.append(li);
    return button;
  });
  markCurrent();
}

// The last segment whose start is at or before the playhead, so a segment stays current through the
// pause after it; none before the first.
function markCurrent() {
  const now = audio.currentTime;
  let current = null;
  for (const button of buttons) {
    if (Number(button.dataset.start) <= now) {
      current = button;
    } else {
      break;
    }
  }
  for (const button of buttons) {
    if (button !== current) {
      button.removeAttribute("aria-current");
    }
  }
  if (current && current.getAttribute("aria-current") !== "true") {
    current.setAttribute("aria-current", "true");
    current.scrollIntoView({block: "nearest"});
  }
}

audio.addEventListener("timeupdate", markCurrent);
audio.addEventListener("seeked", markCurrent);

async function main() {
  let response;
  try {
    response = await fetch("transcript.json", {cache: "no-store"});
  } catch (err) {
    showError(String(err));
    return;
  }
  if (!response.ok) {
    showError(await response.text());
    return;
  }
  render(await response.json());
}

main();
