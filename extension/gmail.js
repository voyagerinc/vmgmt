// Gmail compose capture (metadata only).
(function () {
  const G = window.__voyagerMail;
  if (!G) return;
  const acct = () => {
    // the signed-in address is in the account button aria-label on most layouts
    const a = document.querySelector('a[aria-label*="@"], [aria-label*="Google Account"]');
    const m = a && (a.getAttribute("aria-label") || "").match(/[^\s]+@[^\s)]+/);
    return m ? m[0] : "";
  };

  function compose(fromEl) {
    // nearest compose dialog containing the send button / event target
    return fromEl.closest('div[role="dialog"]') ||
           document.querySelector('div[role="dialog"]') || document;
  }

  function scrape(dlg) {
    const to = G.chipEmails(dlg, ['div[name="to"] [email]', '[aria-label^="To"] [email]']);
    const cc = G.chipEmails(dlg, ['div[name="cc"] [email]', '[aria-label^="Cc"] [email]']);
    const bcc = G.chipEmails(dlg, ['div[name="bcc"] [email]', '[aria-label^="Bcc"] [email]']);
    // fallback: any recipient chips in the dialog header
    if (!to.length) {
      G.chipEmails(dlg, ['[email]']).forEach((e) => to.push(e));
    }
    const subjEl = dlg.querySelector('input[name="subjectbox"]');
    const subject = subjEl ? subjEl.value : "";
    const attachments = [];
    // Gmail renders each attachment chip; filenames carry a .ext
    dlg.querySelectorAll('[aria-label^="Attachment:"], .dCJP6, [class*="aZo"] [download]').forEach((n) => {
      const t = (n.getAttribute("aria-label") || "").replace(/^Attachment:\s*/, "") ||
                (n.getAttribute("download") || "") || G.text(n);
      const name = (t || "").split(".").length > 1 ? t.trim() : "";
      if (name) attachments.push(name);
    });
    return { provider: "gmail", account: acct(), to, cc, bcc, subject,
             attachments: Array.from(new Set(attachments)) };
  }

  function onSend(target) {
    G.debounceWatch(() => {
      const data = scrape(compose(target));
      if (data.to.length || data.subject || data.attachments.length) G.send(data);
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[role="button"][data-tooltip*="Send"], [aria-label^="Send"], [data-tooltip^="Send"]');
    if (btn) onSend(btn);
  }, true);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") onSend(e.target);
  }, true);
})();
