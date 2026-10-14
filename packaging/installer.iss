; SnipForge installer (Inno Setup 6). Built by: python scripts/build.py --installer
;
; Installs per user by default (no admin rights needed), into
; %LOCALAPPDATA%\Programs\SnipForge. IT can deploy machine-wide with:
;   SnipForge-<version>-setup.exe /VERYSILENT /ALLUSERS

#define AppName "SnipForge"
#define AppExe "SnipForge.exe"
#define AppUserModelID "SnipForge.SnipForge"
#define RepoUrl "https://github.com/nighthawk909/snipforge"
#ifndef AppVersion
  #define AppVersion "0.0.0"
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
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer

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
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart
; Remove the startup entry on uninstall whether the installer or the tray menu created it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopSnipForge"

[Messages]
FinishedLabel=Setup has finished installing [name].%n%nTo pin it to the taskbar: open Start, right-click {#AppName}, and choose Pin to taskbar.%n%nPress Win+Shift+S or Print Screen to snip.
