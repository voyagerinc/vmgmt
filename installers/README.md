# Installers — Zero-Complexity Deployment (PRD §7)

Goal (PRD §7.4):
- **Server:** Double-click → Install → Activate License → Create Admin → Ready.
- **Agent:** Double-click → Enter/Detect Server → Enroll → Ready.

No command line, database commands, package managers, or config-file editing for a normal install.

## Build prerequisites (build machine only)
- Windows 10/11 or Server 2016+ (x64)
- Python 3.11+ with the `server/.venv` and `agent` dependencies installed
- [PyInstaller](https://pyinstaller.org): `pip install pyinstaller`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for the `.exe` wizard)
- A code-signing certificate (PRD §24, §30 — installers/updates must be signed)

## 1. Bundle the executables (PyInstaller)
```powershell
cd "Emp Monitoring"
server\.venv\Scripts\python.exe installers\build.py server   # -> installers\dist\ManagementServer\
server\.venv\Scripts\python.exe installers\build.py agent    # -> installers\dist\AgentEnroll\, AgentService\
```

## 2. Wrap into double-click installers (Inno Setup)
Open `installers\server.iss` and `installers\agent.iss` in Inno Setup and **Compile**, or:
```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installers\server.iss
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installers\agent.iss
```
Output: `Server_Setup.exe` and `Agent_Setup.exe` (under the Inno `Output\` folder).

## 3. Sign (required before distribution)
```powershell
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 Server_Setup.exe
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 Agent_Setup.exe
```

## What each installer does
**Server_Setup.exe** (`server.iss`)
1. Pre-flight check (Windows version; extend with CPU/RAM/disk/port checks as needed).
2. Installs the bundled Management Server (embedded Python runtime — no separate install).
3. Registers the `EndpointMgmtServer` Windows service (auto-start). SQLite DB is created
   automatically on first start; point `EMP_DATABASE_URL` at PostgreSQL for production.
4. Optional firewall rule (explicit consent via a Task; removed on uninstall).
5. Opens `http://127.0.0.1:9084/` — the first-run wizard activates the license and creates the
   first Customer Owner (first-run credentials are also written to `FIRST_RUN.txt`).

**Agent_Setup.exe** (`agent.iss`)
1. Pre-flight (Windows version / admin privileges).
2. Installs the agent + enrollment wizard.
3. Launches **AgentEnroll.exe** — detect the server on the LAN or enter its URL, then enter the
   customer **License ID** and **enrollment token**. The device registers and receives a unique
   device certificate.
4. Registers the `EndpointAgent` Windows service (auto-start). No employee interaction afterward.

## Silent / enterprise deployment (PRD §7.3, future)
Inno supports `/VERYSILENT`. For MSI/GPO/Intune, repackage the bundle as an MSI and pass the
server URL, license ID and enrollment token via registry/MST. Not required for first release.
