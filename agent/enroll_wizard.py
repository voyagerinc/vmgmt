"""First-run enrollment wizard for the endpoint agent (PRD §7.2, §7.4).

Double-click experience: "Enter/Detect Server -> Enroll -> Ready." Shown by Agent_Setup.exe
after installation. On success it enrolls the device, starts/represents the Windows service
and displays a simple status: Connected / Pending / License Issue / Server Unreachable.
"""
from __future__ import annotations

import socket
import threading

import requests

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover
    tk = None

from agent import Agent

DISCOVERY_PORTS = [8084, 8080, 443, 8443]


def discover_servers(timeout: float = 0.3) -> list[str]:
    """Best-effort LAN discovery by probing /api/health on the local /24 (PRD §7.2)."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        base = ".".join(s.getsockname()[0].split(".")[:3])
        s.close()
    except Exception:
        return found
    # probe a small set of likely hosts quickly (gateway + .1..'.20)
    candidates = [f"{base}.{i}" for i in [1, 2, 10, 100, 200, 254]]
    for host in candidates:
        for port in DISCOVERY_PORTS:
            scheme = "https" if port in (443, 8443) else "http"
            url = f"{scheme}://{host}:{port}"
            try:
                r = requests.get(f"{url}/api/health", timeout=timeout, verify=False)
                if r.ok and r.json().get("status") == "ok":
                    found.append(url)
            except Exception:
                continue
    return found


def enroll(server: str, license_id: str, token: str) -> tuple[bool, str]:
    agent = Agent(server)
    if agent.enrolled:
        return True, "Connected (already enrolled)"
    ok = agent.enroll(license_id, token)
    status = (agent.STATE_DIR if hasattr(agent, "STATE_DIR") else None)
    try:
        from agent import STATE_DIR
        st = (STATE_DIR / "status.txt")
        label = st.read_text(encoding="utf-8").strip() if st.exists() else ("Connected" if ok else "License Issue")
    except Exception:
        label = "Connected" if ok else "License Issue"
    return ok, label


def run_gui():  # pragma: no cover  (UI)
    if tk is None:
        print("Tkinter unavailable; use: python agent.py --server <URL> --license <ID> --token <TOKEN>")
        return
    root = tk.Tk()
    root.title("Endpoint Agent — Enrollment")
    root.geometry("460x320")
    root.configure(bg="#0e1526")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    def L(text, **kw):
        return tk.Label(root, text=text, bg="#0e1526", fg="#e8edf7", **kw)

    L("Enroll this computer with the Management Server",
      font=("Segoe UI", 12, "bold")).pack(pady=(16, 6))

    frm = tk.Frame(root, bg="#0e1526")
    frm.pack(padx=20, fill="x")

    tk.Label(frm, text="Server URL", bg="#0e1526", fg="#93a2c4").grid(row=0, column=0, sticky="w")
    server_var = tk.StringVar()
    server_entry = tk.Entry(frm, textvariable=server_var, width=40)
    server_entry.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 8))

    def do_detect():
        status_var.set("Detecting servers on the local network…")
        def worker():
            servers = discover_servers()
            if servers:
                server_var.set(servers[0])
                status_var.set(f"Found: {', '.join(servers)}")
            else:
                status_var.set("No server auto-detected — enter the URL manually.")
        threading.Thread(target=worker, daemon=True).start()

    tk.Button(frm, text="Detect on LAN", command=do_detect).grid(row=2, column=0, sticky="w")

    tk.Label(frm, text="License ID", bg="#0e1526", fg="#93a2c4").grid(row=3, column=0, sticky="w", pady=(10, 0))
    lic_var = tk.StringVar()
    tk.Entry(frm, textvariable=lic_var, width=40).grid(row=4, column=0, columnspan=2, sticky="we")

    tk.Label(frm, text="Enrollment token", bg="#0e1526", fg="#93a2c4").grid(row=5, column=0, sticky="w", pady=(8, 0))
    tok_var = tk.StringVar()
    tk.Entry(frm, textvariable=tok_var, width=40).grid(row=6, column=0, columnspan=2, sticky="we")

    status_var = tk.StringVar(value="Ready.")
    tk.Label(root, textvariable=status_var, bg="#0e1526", fg="#7fb0ff", wraplength=420,
             justify="left").pack(padx=20, pady=12, anchor="w")

    def do_enroll():
        server = server_var.get().strip()
        if not (server and lic_var.get().strip() and tok_var.get().strip()):
            messagebox.showwarning("Missing", "Enter server URL, license ID and token.")
            return
        status_var.set("Enrolling…")
        root.update()
        ok, label = enroll(server, lic_var.get().strip(), tok_var.get().strip())
        status_var.set(f"Status: {label}")
        if ok:
            messagebox.showinfo("Enrolled", "This device is enrolled. The agent service will run in the background.")
            root.destroy()
        else:
            messagebox.showerror("Enrollment failed", label)

    tk.Button(root, text="Enroll", command=do_enroll,
              bg="#2f6df6", fg="white", font=("Segoe UI", 10, "bold"),
              relief="flat", padx=20, pady=6).pack(pady=(0, 10))

    root.mainloop()


if __name__ == "__main__":
    run_gui()
