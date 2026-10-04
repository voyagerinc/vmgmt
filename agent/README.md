# Endpoint Agent

Lightweight Windows service that enrolls with the Management Server and performs **authorized,
policy-driven** collection: device identity/heartbeat, hardware & software inventory, resource
health, policy-gated application/website activity, and signed-job screenshot capture. Buffers
locally during outages and retries with backoff (PRD §10).

## What it does NOT do (PRD §3.2, §17.3, §18)
No credential/password/private-key collection. No security-control bypass or covert persistence.
It only collects categories that an **enabled** server policy references (data minimization), and
only captures a screenshot for a **signed, policy-approved** job.

## Run (development / manual)
```powershell
cd agent
pip install -r requirements.txt
python agent.py --server http://127.0.0.1:9084 --license <LICENSE_ID> --token <ENROLL_TOKEN>
```
After the first enrollment the device identity is stored in
`%PROGRAMDATA%\EndpointAgent\agent_state.json`; subsequent runs need only `--server` (or run as
the installed service, which reads the stored URL). Use `--reenroll` to re-register.

## First-run wizard (what Agent_Setup.exe launches)
```powershell
python enroll_wizard.py
```
Detect the server on the LAN or type its URL, enter the License ID and enrollment token, click
**Enroll**. Status shown: **Connected / Pending / License Issue / Server Unreachable** (PRD §7.2).

## Windows service
```powershell
python agent_service.py install
python agent_service.py start
```
(The installer registers `EndpointAgent` as auto-start.)

## Files
```
agent.py          enrollment + heartbeat loop + offline queue + job handling
collectors.py     identity, hardware, health, software, active-window, screenshot
enroll_wizard.py  tkinter first-run enrollment UI with LAN discovery
agent_service.py  pywin32 Windows service wrapper
```

## Resource targets (PRD §10.2)
Idle CPU <1% avg, RAM <~100 MB, batched/compressed uploads, bounded local queue. Heartbeat
interval is server-controlled (default 60s). Screenshot and full software snapshots are rate-limited.

## Local state & logs
`%PROGRAMDATA%\EndpointAgent\` — `agent_state.json`, `status.txt`, `agent.log`.
