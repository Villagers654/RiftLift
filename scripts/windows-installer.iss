#ifndef AppVersion
  #error AppVersion must be supplied by build-windows.ps1
#endif
#ifndef BundleDir
  #error BundleDir must be supplied by build-windows.ps1
#endif

[Setup]
AppId={{76D8E09B-371D-4723-BB67-AC3481EE7712}
AppName=RiftLift
AppVersion={#AppVersion}
AppPublisher=Villagers654
AppPublisherURL=https://github.com/Villagers654/RiftLift
DefaultDirName={localappdata}\Programs\RiftLift
DefaultGroupName=RiftLift
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile={#BundleDir}\_internal\assets\riftlift.ico
UninstallDisplayIcon={app}\RiftLift.exe
OutputBaseFilename=RiftLift-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\RiftLift"; Filename: "{app}\RiftLift.exe"; WorkingDir: "{app}"; AppUserModelID: "Villagers654.RiftLift"
Name: "{userdesktop}\RiftLift"; Filename: "{app}\RiftLift.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "Villagers654.RiftLift"

[Run]
Filename: "{app}\RiftLift.exe"; Description: "Open RiftLift"; Flags: nowait postinstall skipifsilent

; User games, settings and sign-in live outside {app} and survive uninstall.

[Code]
procedure RemoveOwnedProtocol(Scheme: String);
var
  Command: String;
begin
  if RegQueryStringValue(HKCU, 'Software\Classes\' + Scheme + '\shell\open\command', '', Command) then
    if (Pos('"' + Lowercase(ExpandConstant('{app}\RiftLift.exe')) + '"', Lowercase(Command)) = 1) or
       (Pos(Lowercase(ExpandConstant('{app}\RiftLift.exe')) + ' ', Lowercase(Command)) = 1) then
      RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\' + Scheme);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RemoveOwnedProtocol('oculus');
    RemoveOwnedProtocol('oculus-client');
  end;
end;
