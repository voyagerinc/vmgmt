; Inno Setup script — Agent_Setup.exe (PRD §7.2 Zero-Complexity Installation)
; Build: compile with Inno Setup 6 after running `python installers/build.py agent`.
; Double-click installer: detects Windows/privileges, installs the agent, registers the
; auto-start service, then launches the enrollment wizard (Detect/Enter server -> Enroll -> Ready).
; No employee interaction is required after a successful install.

#define AppName "Endpoint Management Agent"
#define AppVer "4.0.0"
#define SvcName "EndpointAgent"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\EndpointMgmt\Agent
DisableProgramGroupPage=yes
OutputBaseFilename=Agent_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupLogging=yes

[Files]
Source: "dist\AgentEnroll\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "dist\AgentService\*"; DestDir: "{app}\svc"; Flags: recursesubdirs ignoreversion

[Run]
; Launch enrollment wizard first so the device is enrolled before the service starts.
Filename: "{app}\AgentEnroll.exe"; Description: "Enroll this computer"; Flags: nowait postinstall skipifsilent
; Register + start the background service.
Filename: "{app}\svc\AgentService.exe"; Parameters: "install"; Flags: runhidden
Filename: "sc.exe"; Parameters: "config {#SvcName} start= auto"; Flags: runhidden
Filename: "sc.exe"; Parameters: "start {#SvcName}"; Flags: runhidden

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#SvcName}"; Flags: runhidden; RunOnceId: "StopAgent"
Filename: "{app}\svc\AgentService.exe"; Parameters: "remove"; Flags: runhidden; RunOnceId: "RemoveAgent"

[Code]
function InitializeSetup(): Boolean;
var Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  if Version.Major < 10 then
  begin
    MsgBox('This agent requires Windows 10 or newer.', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
