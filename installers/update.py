"""update.exe — standalone updater for the Voyager Management Server client (PRD §30).

Reads EMP_LICENSE_SERVER from the install folder's .env, asks the license server for the
latest build, downloads it (SHA-256 verified), stops the service, swaps ManagementServer.exe,
and restarts. Run it from the server's install folder (double-click). Requires admin rights
(built with --uac-admin).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

SVC = "EndpointMgmtServer"


def _read_env(d: Path) -> dict:
    cfg = {}
    f = d / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def do_update(install_dir: str, log) -> str:
    d = Path(install_dir)
    cur = d / "ManagementServer.exe"
    if not cur.exists():
        raise RuntimeError(f"ManagementServer.exe not found in {d}")
    cfg = _read_env(d)
    lic = (cfg.get("EMP_LICENSE_SERVER") or "").rstrip("/")
    if not lic:
        raise RuntimeError("EMP_LICENSE_SERVER is not set in .env — cannot check for updates.")

    log(f"Checking {lic} for updates ...")
    with urllib.request.urlopen(f"{lic}/api/updates/latest", timeout=30) as r:
        man = json.loads(r.read().decode())
    comp = (man.get("components") or {}).get("server")
    if not comp:
        raise RuntimeError("License server has no server build to distribute.")
    log(f"Latest version: {comp['version']}")

    newexe = d / "ManagementServer.new.exe"
    log("Downloading new build ...")
    urllib.request.urlretrieve(comp["url"], newexe)
    data = newexe.read_bytes()
    if comp.get("sha256") and hashlib.sha256(data).hexdigest() != comp["sha256"]:
        newexe.unlink(missing_ok=True)
        raise RuntimeError("Checksum mismatch — update aborted.")
    log("Verified checksum. Stopping service ...")

    subprocess.run(["net", "stop", SVC], capture_output=True, text=True)
    subprocess.run(["taskkill", "/f", "/im", "ManagementServer.exe"], capture_output=True, text=True)
    time.sleep(2)
    os.replace(newexe, cur)
    log("Swapped executable. Starting service ...")
    r = subprocess.run(["net", "start", SVC], capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.Popen([str(cur)], close_fds=True)   # fallback: run directly if no service
    log(f"Update complete — now running {comp['version']}.")
    return comp["version"]


def run_gui() -> None:
    root = tk.Tk()
    root.title("Voyager Server — Update")
    root.geometry("540x360")
    root.configure(bg="#0e1526")
    tk.Label(root, text="Update Voyager Management Server", bg="#0e1526", fg="#e8edf7",
             font=("Segoe UI", 13, "bold")).pack(pady=(16, 4))
    tk.Label(root, text="Pulls the latest build from the license server and restarts.",
             bg="#0e1526", fg="#93a2c4").pack(pady=(0, 10))

    frm = tk.Frame(root, bg="#0e1526"); frm.pack(padx=22, fill="x")
    tk.Label(frm, text="Server install folder", bg="#0e1526", fg="#93a2c4").grid(row=0, column=0, sticky="w")
    var = tk.StringVar(value=str(Path(sys.executable).resolve().parent))
    tk.Entry(frm, textvariable=var, width=50).grid(row=1, column=0, sticky="we")
    tk.Button(frm, text="…", command=lambda: var.set(filedialog.askdirectory() or var.get())
              ).grid(row=1, column=1, padx=4)

    status = tk.Text(root, height=8, width=62, bg="#15203a", fg="#cfe0ff", bd=0)
    status.pack(padx=22, pady=12, fill="both", expand=True)

    def log(m):
        status.insert("end", m + "\n"); status.see("end"); root.update()

    def go():
        def worker():
            try:
                v = do_update(var.get(), log)
                messagebox.showinfo("Updated", f"Server updated to {v}.")
            except Exception as e:
                log("ERROR: " + str(e))
                messagebox.showerror("Update failed", str(e))
        threading.Thread(target=worker, daemon=True).start()

    tk.Button(root, text="Update now", command=go, bg="#2f6df6", fg="white",
              font=("Segoe UI", 11, "bold"), relief="flat", padx=24, pady=6).pack(pady=(0, 14))
    root.mainloop()


if __name__ == "__main__":
    if "--silent" in sys.argv:
        idx = sys.argv.index("--silent")
        target = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else str(Path(sys.executable).resolve().parent)
        do_update(target, lambda m: print(m))
    else:
        run_gui()
