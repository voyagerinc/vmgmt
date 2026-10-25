// Shared helpers for the Voyager webmail content scripts.
// IMPORTANT: capture metadata only (recipients, subject, attachment file names).
// Never read or transmit the message body.

(function () {
  const G = (window.__voyagerMail = window.__voyagerMail || {});

  G.send = function (payload) {
    try {
      chrome.runtime.sendMessage({ type: "voyager-mail", payload }, function () {
        void chrome.runtime.lastError; // swallow "receiving end" noise
      });
    } catch (e) {
      /* extension context torn down - ignore */
    }
  };

  // Split "a@x.com, b@y.com; c@z.com (Name)" style strings into clean addresses.
  G.emails = function (text) {
    if (!text) return [];
    const out = [];
    const re = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
    let m;
    while ((m = re.exec(text))) out.push(m[0]);
    return Array.from(new Set(out));
  };

  G.text = function (el) {
    return el ? (el.textContent || "").trim() : "";
  };

  // Collect email chips under a container (Gmail/Outlook both use chip widgets).
  G.chipEmails = function (root, selectors) {
    const set = new Set();
    if (!root) return [];
    selectors.forEach((sel) => {
      root.querySelectorAll(sel).forEach((n) => {
        const v = n.getAttribute("email") || n.getAttribute("data-address") ||
                  n.getAttribute("aria-label") || n.textContent || "";
        G.emails(v).forEach((e) => set.add(e));
      });
    });
    return Array.from(set);
  };

  G.debounceWatch = function (fn) {
    // run fn shortly after a Send gesture so late DOM is captured, then once more
    setTimeout(fn, 0);
    setTimeout(fn, 150);
  };
})();
