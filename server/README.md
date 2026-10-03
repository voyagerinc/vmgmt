# Management Server

Tenant-aware FastAPI application: REST API, policy & alert engine, Cloud License Portal,
web Admin Portal, reporting, audit and the agent-facing endpoints.

## Run (development)
```powershell
cd server
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python run_server.py            # opens http://127.0.0.1:8080/
```
- First run auto-creates the SQLite DB, a **Platform Super Admin**, a **Demo tenant** (Enterprise
  license), sample policies, and an enrollment token. Credentials print to the console and to
  `FIRST_RUN.txt`.
- Interactive API docs: `/docs` (OpenAPI). Health: `/api/health`.
- `--no-browser` to skip auto-opening; `--reload` for autoreload.

## Configuration (`server/.env`, prefix `EMP_`)
| Variable | Default | Notes |
|----------|---------|-------|
| `EMP_DEPLOYMENT_MODEL` | `on_premise` | `on_premise` / `cloud_vps` / `hybrid` / `central_saas` |
| `EMP_SERVER_PUBLIC_URL` | `http://127.0.0.1:8080` | Used in license packages & links |
| `EMP_HOST` / `EMP_PORT` | `127.0.0.1` / `8080` | Bind address |
| `EMP_DATABASE_URL` | SQLite file | Set a PostgreSQL DSN for production (PRD §28) |
| `EMP_SECRET_KEY` | auto (`data/.secret`) | JWT + license HMAC key |
| `EMP_EVIDENCE_KEY` | auto (`data/.evidence_key`) | Fernet key for evidence-at-rest |
| `EMP_REQUIRE_MFA` | `false` | Enforce MFA for admin login |
| `EMP_OFFLINE_AFTER_SECONDS` | `300` | Device marked offline after no heartbeat |
| `EMP_DEFAULT_EVIDENCE_RETENTION_DAYS` | `30` | Evidence auto-purge (tenant can override) |

## Layout
```
app/
  config.py        settings / deployment model
  database.py      engine + session (SQLite/PostgreSQL)
  models.py        full data model (PRD §27)
  schemas.py       API request/response contracts (PRD §26)
  security.py      argon2 hashing, JWT, license HMAC signing, evidence Fernet crypto
  deps.py          auth, RBAC, tenant scoping, agent device auth
  audit.py         audit-event helper (PRD §25)
  bootstrap.py     first-run provisioning (PRD §7.1)
  main.py          app wiring, scheduler (offline check + retention), static console
  services/        license_service, policy_engine, alert_engine, retention
  routers/         auth, portal(license), users, agents, devices, directory,
                   inventory, policies, alerts, evidence, reports
  static/          web Admin Portal (SPA)
run_server.py      launcher (also `service install|start|stop|remove`)
server_service.py  Windows service wrapper (uvicorn)
```

## Windows service
```powershell
python run_server.py service install
python run_server.py service start
```
(The Server_Setup.exe installer does this automatically.)

## Security model (PRD §24)
- Argon2 password hashing; JWT access/refresh; account lockout after repeated failures.
- RBAC via 7 roles (platform super admin → auditor); strict **tenant isolation** on every query.
- Per-device identity (`X-Device-Id` + `X-Device-Cert`) for agent calls; revocable.
- HMAC-signed licenses & signed screenshot jobs; evidence encrypted at rest (Fernet).
- Every privileged action is written to the append-only audit log with actor/IP/correlation id.
- Data minimization: agents only collect categories referenced by an **enabled** policy.

## API groups (PRD §26.1)
`/api/auth` · `/api/tenants` + `/api/licenses` (portal) · `/api/users` · `/api/agents`
(enroll/heartbeat/screenshot) · `/api/devices` · `/api/employees` + `/api/assets` ·
`/api/software` + `/api/activity` + `/api/health` · `/api/policies` · `/api/alerts` +
`/api/notifications` · `/api/screenshots` + `/api/remote` · `/api/reports` + `/api/dashboard`
· `/api/audit`.
