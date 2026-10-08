# Cloud Deployment — License Server `http://vmgmt.voyager.co.in:8084/`

Deploy the Voyager Endpoint Management **license server** on a Linux VPS.

```
Client servers / Agents / Browsers ──HTTP:8084──▶ vmgmt.voyager.co.in:8084 (app) ──▶ SQLite/Postgres
```

| Server | Address | Port |
|---|---|---|
| Cloud license server | `http://vmgmt.voyager.co.in:8084/` | 8084 |
| On-premise client server | `http://<client LAN IP>:9084/` | 9084 |

License files (`.lic`), customer emails and agents embed `EMP_SERVER_PUBLIC_URL`, so it must be
exactly `http://vmgmt.voyager.co.in:8084`. Client servers reach the license server at the same URL
(`EMP_LICENSE_SERVER` in their `server\.env`, created automatically).

---

## 1. DNS
Create an **A record** pointing the host to your VPS public IP:

| Type | Name            | Value (VPS IP)   | TTL  |
|------|-----------------|------------------|------|
| A    | vmgmt           | `203.0.113.45`   | 300  |

(Full host `vmgmt.voyager.co.in`.) Verify it resolves before continuing:
```bash
dig +short vmgmt.voyager.co.in      # should print your VPS IP
```

## 2. System packages
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## 3. Get the code & install
```bash
cd /root/projects            # or wherever you keep it
git clone git@github.com:voyagerinc/vmgmt.git   # (or: cd vmgmt && git pull)
cd vmgmt/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure (.env)
```bash
cp ../deploy/env.production.example .env
# the important lines (already set in the example):
#   EMP_HOST=0.0.0.0
#   EMP_PORT=8084
#   EMP_SERVER_PUBLIC_URL=http://vmgmt.voyager.co.in:8084
nano .env
```
Do a one-off first run to create the DB + admin, then stop it (Ctrl-C):
```bash
python run_server.py --no-browser      # prints FIRST_RUN.txt credentials
cat FIRST_RUN.txt                       # save the Super Admin login, then Ctrl-C
```

## 5. Run as a service (systemd)
```bash
sudo cp /root/projects/vmgmt/deploy/vmgmt.service /etc/systemd/system/vmgmt.service
# edit paths/User in the unit if your location differs
sudo systemctl daemon-reload
sudo systemctl enable --now vmgmt
sudo systemctl status vmgmt            # should be active (running)
curl -s http://127.0.0.1:8084/api/health   # {"status":"ok",...}
```

## 6. Firewall
Open port 8084 so client servers and agents can reach the license server.
```bash
sudo ufw allow OpenSSH
sudo ufw allow 8084/tcp
sudo ufw enable
```
Verify from outside the VPS:
```bash
curl -s http://vmgmt.voyager.co.in:8084/api/meta   # "server_url":"http://vmgmt.voyager.co.in:8084"
```

## 7. Done — first login
Open **http://vmgmt.voyager.co.in:8084/**, sign in as the Super Admin from `FIRST_RUN.txt`, then:
1. Settings → **Email setup** (SMTP) so license/recovery mails deliver.
2. **Licenses & Tenants → + Customer** to provision a company (creates admin + license, emails the `.lic`).
3. On the customer's client server: **Licenses & Tenants → Activate from license server**, attach the
   `.lic` file and activate.

---

## Optional: HTTPS on 443 with Nginx
If you later want `https://vmgmt.voyager.co.in/`, install `nginx certbot python3-certbot-nginx`, then:
```bash
sudo cp /root/projects/vmgmt/deploy/nginx-vmgmt.conf /etc/nginx/sites-available/vmgmt
sudo ln -s /etc/nginx/sites-available/vmgmt /etc/nginx/sites-enabled/vmgmt
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d vmgmt.voyager.co.in --redirect -m you@voyager.co.in --agree-tos
```
Keep port 8084 open as well — existing client servers and `.lic` files use
`http://vmgmt.voyager.co.in:8084`.

## Updating later
From the console: **Settings → Update from GitHub & restart**, or:
```bash
cd /root/projects/vmgmt && git pull
cd server && source .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart vmgmt
```

## Notes
- **Agent `.exe`:** the Windows `VoyagerAgent.exe` is built on a Windows machine (PyInstaller) and
  is not in git. Copy it to `server/agent_dist/VoyagerAgent.exe` on the VPS so the agent download
  serves the `.exe`; otherwise it serves the source+`.bat` fallback.
- **Database:** SQLite is fine to start. For scale, create a Postgres DB and set
  `EMP_DATABASE_URL=postgresql+psycopg2://vmgmt:PASS@localhost:5432/vmgmt` in `.env`, then restart.
- **Backups:** back up `server/data/` (SQLite DB, evidence, keys `.secret`/`.evidence_key`) or your
  Postgres dumps + `server/data/evidence/`. Losing `.secret`/`.evidence_key` invalidates tokens and
  encrypted evidence.
- **Logs:** `journalctl -u vmgmt -f`.
