# Cloud Deployment — vmgmt.voyage.co.in

Deploy the Voyager Endpoint Management Server on a Linux VPS behind Nginx with HTTPS.

```
Agents / Browsers ──HTTPS:443──▶ Nginx (TLS) ──HTTP──▶ 127.0.0.1:8084 (app) ──▶ SQLite/Postgres
                   vmgmt.voyage.co.in
```

The app listens only on localhost; Nginx terminates TLS and reverse-proxies to it. Agents and
license keys embed `https://vmgmt.voyage.co.in`, so every endpoint connects over the domain.

---

## 1. DNS
Create an **A record** pointing the host to your VPS public IP:

| Type | Name            | Value (VPS IP)   | TTL  |
|------|-----------------|------------------|------|
| A    | vmgmt.voyage    | `203.0.113.45`   | 300  |

(Full host `vmgmt.voyage.co.in`.) Verify it resolves before continuing:
```bash
dig +short vmgmt.voyage.co.in      # should print your VPS IP
```

## 2. System packages
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
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
# edit .env — the important line:
#   EMP_SERVER_PUBLIC_URL=https://vmgmt.voyage.co.in
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

## 6. Nginx reverse proxy
```bash
sudo cp /root/projects/vmgmt/deploy/nginx-vmgmt.conf /etc/nginx/sites-available/vmgmt
sudo ln -s /etc/nginx/sites-available/vmgmt /etc/nginx/sites-enabled/vmgmt
sudo nginx -t && sudo systemctl reload nginx
```

## 7. HTTPS (Let's Encrypt)
```bash
sudo certbot --nginx -d vmgmt.voyage.co.in --redirect -m you@voyager.co.in --agree-tos
```
Certbot rewrites the Nginx site to add the 443 server + HTTP→HTTPS redirect and installs a
renewal timer. Verify:
```bash
curl -s https://vmgmt.voyage.co.in/api/health
```

## 8. Firewall
Expose only 80/443; keep 8084 private (it is bound to localhost anyway).
```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'      # opens 80 + 443
sudo ufw enable
```

## 9. Done — first login
Open **https://vmgmt.voyage.co.in/**, sign in as the Super Admin from `FIRST_RUN.txt`, then:
1. Settings → **Email setup** (SMTP) so license/recovery mails deliver.
2. **Licenses & Tenants → + Customer** to provision a company (creates admin + license key, emails it).
3. The company owner signs in, **activates the license**, then **Downloads** the agent.

Because `EMP_SERVER_PUBLIC_URL=https://vmgmt.voyage.co.in`, every downloaded agent and license
key already points at the domain over HTTPS — nothing else to change on endpoints.

---

## Updating later
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
- **Logs:** `journalctl -u vmgmt -f` for the app; `/var/log/nginx/` for Nginx.
