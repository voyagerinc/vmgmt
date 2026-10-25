# Voyager Mail Monitor (browser extension)

Captures **outbound webmail metadata** that a background agent cannot see (the browser
encrypts the traffic and sandboxes the page). On every send from Gmail or Outlook web, the
extension reads **recipients (To/Cc/Bcc), subject, and attachment file names** and POSTs them
to the local Voyager agent at `http://127.0.0.1:47113/webmail`.

- **Metadata only.** It never reads or transmits the message body.
- **Localhost only.** Data goes to the agent on the same PC; the agent then syncs it to the
  server as a normal `email` / `email_sent` event. The token (`voyager-webmail-v1`) must match
  `WEBMAIL_TOKEN` in `agent/trackers.py`.
- **Supported:** Gmail (`mail.google.com`), Outlook web
  (`outlook.office.com`, `outlook.office365.com`, `outlook.live.com`).

## How it updates (answer to "when does the agent update the extension")

The **agent does not install or update the extension** — it only runs the localhost receiver.
The extension is deployed and auto-updated by the **browser's enterprise policy**, which the
agent/installer writes once. After that the browser refreshes it automatically (every few
hours and at launch) from the update URL you host:

### Chrome / Edge (force-install via registry, applied by the agent installer or GPO)
```
HKLM\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist
  1 = "<EXTENSION_ID>;https://vmgmt.voyager.co.in/ext/updates.xml"

HKLM\SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist
  1 = "<EXTENSION_ID>;https://vmgmt.voyager.co.in/ext/updates.xml"
```
Host `updates.xml` (Chrome `gupdate` manifest) + the packed `.crx` on the server. Bump the
`version` in `manifest.json` and the `.crx`, and every managed browser picks it up on its next
update check — no agent redeploy needed.

### Firefox (ExtensionSettings policy)
```
policies.json:
  "ExtensionSettings": {
    "voyager-mail@voyager.co.in": {
      "installation_mode": "force_installed",
      "install_url": "https://vmgmt.voyager.co.in/ext/voyager_mail.xpi"
    }
  }
```

## Load unpacked (for testing)
`chrome://extensions` -> Developer mode -> Load unpacked -> select this folder. Start the agent,
compose a mail in Gmail/Outlook web, hit Send, and the event appears under the device's Email
activity.
