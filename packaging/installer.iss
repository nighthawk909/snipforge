; SnipForge installer (Inno Setup 6). Built by: python scripts/build.py --installer
;
; Installs per user by default (no admin rights needed), into
; %LOCALAPPDATA%\Programs\SnipForge. IT can deploy machine-wide (needs admin) with:
;   SnipForge-<version>-setup.exe /VERYSILENT /ALLUSERS /TASKS="autostart"

#define AppName "SnipForge"
#define AppExe "SnipForge.exe"
#define AppUserModelID "SnipForge.SnipForge"
; scripts/build.py passes RepoUrl from snipforge/project.py; this default is for builds by hand.
#ifndef RepoUrl
  #define RepoUrl "https://github.com/nighthawk909/snipforge"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Windows file versions must be four numbers, so previews (0.6.0-preview.1) pass 0.6.0.1.
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\SnipForge"
#endif

[Setup]
; Never change AppId: upgrades and uninstall rely on it.
AppId={{23E25F09-747F-4B3D-B9D0-E81D15E709B3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SnipForge contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\assets\snipforge.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#FileVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer
; scripts/build.py --sign passes /DSign and the "snipforge" sign command (/Ssnipforge=...), which
; signs Setup.exe and the uninstaller with Azure Artifact Signing.
#ifdef Sign
SignTool=snipforge
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start SnipForge when I sign in (keeps Win+Shift+S working)"; GroupDescription: "Startup:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The AppUserModelID matches the running app, so a pinned taskbar icon and the open window share one button.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Screenshot and markup"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon; Comment: "Screenshot and markup"

[Registry]
; HKA = this user for a per-user install, the whole machine for /ALLUSERS, so IT deployments
; start SnipForge for everyone who signs in.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart
; Remove startup entries on uninstall, whether the installer or the tray menu created them.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue
; App settings (tool styles, recent Teams recipients) are removed on uninstall.
Root: HKCU; Subkey: "Software\SnipForge"; ValueType: none; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; A silent per-user upgrade closes the running app; start it again so Win+Shift+S keeps working.
; SnipForge passes /RELAUNCH=0 when it installs an update as you quit it.
Filename: "{app}\{#AppExe}"; Parameters: "--background"; Flags: nowait; Check: RelaunchAfterSilentInstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopSnipForge"

[UninstallDelete]
; Temporary copies made for drag-out and sharing. The user's own snips in Pictures are kept.
Type: filesandordirs; Name: "{%TEMP}\SnipForge"
; Small theme images SnipForge regenerates on start.
Type: filesandordirs; Name: "{localappdata}\SnipForge\cache"

[Code]
function RelaunchAfterSilentInstall: Boolean;
begin
  Result := WizardSilent and not IsAdminInstallMode and (ExpandConstant('{param:RELAUNCH|1}') <> '0');
end;

[Messages]
FinishedLabel=Setup has finished installing [name].%n%nTo pin it to the taskbar: open Start, right-click {#AppName}, and choose Pin to taskbar.%n%nPress Win+Shift+S or Print Screen to snip.
