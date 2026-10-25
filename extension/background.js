// Voyager Mail Monitor - service worker.
// Receives metadata from the content scripts and forwards it to the local agent receiver.
// The agent listens on 127.0.0.1:47113 and requires the shared token header.

const AGENT_URL = "http://127.0.0.1:47113/webmail";
const TOKEN = "voyager-webmail-v1"; // must match WEBMAIL_TOKEN in agent/trackers.py

// de-dupe: the same send can fire both a click and Ctrl+Enter handler
const recent = new Map();
function seen(key) {
  const now = Date.now();
  for (const [k, t] of recent) if (now - t > 15000) recent.delete(k);
  if (recent.has(key)) return true;
  recent.set(key, now);
  return false;
}

async function report(payload) {
  const key = JSON.stringify([payload.provider, payload.to, payload.subject, payload.attachments]);
  if (seen(key)) return { ok: true, deduped: true };
  try {
    const r = await fetch(AGENT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Voyager-Token": TOKEN },
      body: JSON.stringify(payload),
    });
    return { ok: r.ok, status: r.status };
  } catch (e) {
    // agent not running / receiver off - fail quietly, nothing the user can act on
    return { ok: false, error: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "voyager-mail") {
    report(msg.payload).then(sendResponse);
    return true; // async response
  }
});
