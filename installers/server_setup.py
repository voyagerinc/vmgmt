"""Server_Setup.exe — double-click installer for the Voyager Management Server (PRD §7.1).

Bundles ManagementServer.exe inside itself. Asks for install folder, port, public URL and
(optional) license server, then: copies the server, writes .env, adds a firewall rule,
registers + starts the Windows service, and opens the admin console. Requires admin rights
(built with --uac-admin so Windows prompts for elevation on launch).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

BUNDLED = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ManagementServer.exe"
UPDATER = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "update.exe"
SVC = "EndpointMgmtServer"
DEFAULT_DIR = r"C:\VoyagerServer"


def install(dest: str, port: str, pub_url: str, lic_url: str, log) -> None:
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    log(f"Installing to {dest_p} ...")

    target = dest_p / "ManagementServer.exe"
    shutil.copy2(BUNDLED, target)
    log("Copied ManagementServer.exe")
    if UPDATER.exists():
        shutil.copy2(UPDATER, dest_p / "update.exe")
        log("Copied update.exe")

    env = dest_p / ".env"
    env.write_text(
        "EMP_HOST=0.0.0.0\n"
        f"EMP_PORT={port}\n"
        f"EMP_SERVER_PUBLIC_URL={pub_url}\n"
        + (f"EMP_LICENSE_SERVER={lic_url}\n" if lic_url.strip() else "")
        + "EMP_DEPLOYMENT_MODEL=on_premise\n",
        encoding="utf-8")
    log("Wrote configuration (.env)")

    subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule",
                    f"name=Voyager Endpoint Server {port}", "dir=in", "action=allow",
                    "protocol=TCP", f"localport={port}"], capture_output=True, text=True)
    log(f"Added firewall rule for port {port}")

    r = subprocess.run([str(target), "service", "install"], capture_output=True, text=True)
    log("Registered Windows service: " + (r.stdout or r.stderr or "ok").strip()[:120])
    subprocess.run(["sc", "config", SVC, "start=", "auto"], capture_output=True, text=True)
    subprocess.run(["sc", "start", SVC], capture_output=True, text=True)
    log("Service started (auto-start on boot).")

    webbrowser.open(f"http://localhost:{port}/")
    log("Done. Opening the admin console in your browser...")
    log("First-run credentials are in FIRST_RUN.txt inside the install folder.")


def run_gui() -> None:
    root = tk.Tk()
    root.title("Voyager Management Server — Setup")
    root.geometry("560x460")
    root.configure(bg="#0e1526")

    def L(t, **kw):
        return tk.Label(root, text=t, bg="#0e1526", fg="#e8edf7", **kw)

    L("Voyager Endpoint Management Server", font=("Segoe UI", 14, "bold")).pack(pady=(16, 2))
    L("Double-click install — no Python or other software required.",
      fg="#93a2c4").pack(pady=(0, 10))

    frm = tk.Frame(root, bg="#0e1526"); frm.pack(padx=22, fill="x")
    fields = {}
    rows = [("Install folder", DEFAULT_DIR, "dir"),
            ("Server port", "9084", None),
            ("This server's address (agents connect here)", "http://SERVER-IP:9084", None),
            ("License server URL (optional)", "http://vmgmt.voyager.co.in:9084", None)]
    for i, (label, default, kind) in enumerate(rows):
        tk.Label(frm, text=label, bg="#0e1526", fg="#93a2c4").grid(row=i*2, column=0, sticky="w", pady=(8, 0))
        var = tk.StringVar(value=default)
        e = tk.Entry(frm, textvariable=var, width=52)
        e.grid(row=i*2+1, column=0, sticky="we")
        fields[label] = var
        if kind == "dir":
            tk.Button(frm, text="…", command=lambda v=var: v.set(filedialog.askdirectory() or v.get())
                      ).grid(row=i*2+1, column=1, padx=4)

    status = tk.Text(root, height=8, width=64, bg="#15203a", fg="#cfe0ff", bd=0)
    status.pack(padx=22, pady=12, fill="both", expand=True)

    def log(msg):
        status.insert("end", msg + "\n"); status.see("end"); root.update()

    def go():
        vals = {k: v.get() for k, v in fields.items()}
        def worker():
            try:
                install(vals["Install folder"], vals["Server port"],
                        vals["This server's address (agents connect here)"],
                        vals["License server URL (optional)"], log)
                messagebox.showinfo("Setup complete",
                                    "The server is installed and running.\nAdmin console: "
                                    f"http://localhost:{vals['Server port']}/")
            except Exception as e:
                log("ERROR: " + str(e))
                messagebox.showerror("Setup failed", str(e))
        threading.Thread(target=worker, daemon=True).start()

    tk.Button(root, text="Install", command=go, bg="#2f6df6", fg="white",
              font=("Segoe UI", 11, "bold"), relief="flat", padx=24, pady=6).pack(pady=(0, 14))
    root.mainloop()


if __name__ == "__main__":
    run_gui()
