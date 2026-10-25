// Outlook web (office.com / office365.com / live.com) compose capture (metadata only).
(function () {
  const G = window.__voyagerMail;
  if (!G) return;

  const acct = () => {
    const el = document.querySelector('[aria-label*="@"][role="button"], #O365_MainLink_Me, [aria-label*="Account manager"]');
    const m = el && (el.getAttribute("aria-label") || "").match(/[^\s]+@[^\s)]+/);
    return m ? m[0] : "";
  };

  function composeRoot(fromEl) {
    return (fromEl && fromEl.closest('[role="dialog"], [aria-label*="Message"], form')) ||
           document.querySelector('[aria-label="Message body"], [role="dialog"]') || document;
  }

  function wellEmails(root, labels) {
    const set = new Set();
    labels.forEach((lbl) => {
      root.querySelectorAll(`[aria-label^="${lbl}"]`).forEach((w) => {
        // recipient pills carry the address in title/aria-label/text
        w.querySelectorAll('[role="option"], [title*="@"], span').forEach((p) => {
          const v = p.getAttribute("title") || p.getAttribute("aria-label") || p.textContent || "";
          G.emails(v).forEach((e) => set.add(e));
        });
      });
    });
    return Array.from(set);
  }

  function scrape(root) {
    const to = wellEmails(root, ["To", "An"]);
    const cc = wellEmails(root, ["Cc"]);
    const bcc = wellEmails(root, ["Bcc"]);
    const subjEl = root.querySelector('input[aria-label^="Subject"], input[aria-label^="Add a subject"], [aria-label="Subject"] input');
    const subject = subjEl ? (subjEl.value || subjEl.getAttribute("value") || "") : "";
    const attachments = [];
    root.querySelectorAll('[aria-label^="Attachment"], [role="listitem"][title], [data-is-focusable][title*="."]').forEach((n) => {
      const t = (n.getAttribute("title") || n.getAttribute("aria-label") || "").trim();
      const name = t && t.split(".").length > 1 ? t.replace(/^Attachment[:,]?\s*/i, "") : "";
      if (name) attachments.push(name);
    });
    return { provider: "outlook-web", account: acct(), to, cc, bcc, subject,
             attachments: Array.from(new Set(attachments)) };
  }

  function onSend(target) {
    G.debounceWatch(() => {
      const data = scrape(composeRoot(target));
      if (data.to.length || data.subject || data.attachments.length) G.send(data);
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest('button[aria-label^="Send"], [aria-label="Send"][role="menuitem"], [title="Send"]');
    if (btn) onSend(btn);
  }, true);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") onSend(e.target);
  }, true);
})();
