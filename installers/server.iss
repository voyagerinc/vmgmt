; Inno Setup script — Server_Setup.exe (PRD §7.1 Zero-Complexity Installation)
; Build: compile with Inno Setup 6 after running `python installers/build.py server`.
; Produces a double-click installer that: runs pre-flight checks, installs the bundled
; Management Server, registers it as an auto-start Windows service, and opens the
; first-run wizard (license activation + first admin) in the browser.

#define AppName "Endpoint Management Server"
#define AppVer "4.0.0"
#define SvcName "EndpointMgmtServer"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\EndpointMgmt\Server
DefaultGroupName=Endpoint Management
DisableProgramGroupPage=yes
OutputBaseFilename=Server_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName={#AppName}

[Files]
Source: "dist\ManagementServer\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Dirs]
Name: "{app}\data"; Permissions: service-modify
Name: "{app}\logs"; Permissions: service-modify

[Tasks]
Name: "firewall"; Description: "Allow inbound connections on the server port (recommended)"; GroupDescription: "Network"

[Run]
; Pre-flight + service registration + start (service wrapper is bundled as ManagementServer.exe entrypoint).
Filename: "{app}\ManagementServer.exe"; Parameters: "service install"; Flags: runhidden; StatusMsg: "Registering Windows service..."
Filename: "sc.exe"; Parameters: "config {#SvcName} start= auto"; Flags: runhidden
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Endpoint Mgmt Server"" dir=in action=allow protocol=TCP localport=8080"; Flags: runhidden; Tasks: firewall
Filename: "sc.exe"; Parameters: "start {#SvcName}"; Flags: runhidden; StatusMsg: "Starting Management Server..."
; Open the first-run wizard / admin console in the default browser.
Filename: "http://127.0.0.1:8080/"; Description: "Open the Admin Portal"; Flags: shellexec postinstall nowait

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#SvcName}"; Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\ManagementServer.exe"; Parameters: "service remove"; Flags: runhidden; RunOnceId: "RemoveSvc"

[Code]
function InitializeSetup(): Boolean;
var Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  { Pre-flight: require Windows 10 / Server 2016 or newer (PRD §7.1). }
  if Version.Major < 10 then
  begin
    MsgBox('This product requires Windows 10 / Windows Server 2016 or newer.', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
