; Windows installer for CytoGate (Inno Setup 6).
;   iscc packaging\cytogate.iss /DAppVersion=0.9.3 /DSourceDir=dist\CytoGate /DOutputDir=installer
; Installs for the current user (no administrator rights needed); an administrator
; can still choose "install for all users" when asked.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CytoGate"
#endif
#ifndef OutputDir
  #define OutputDir "..\installer"
#endif

[Setup]
AppId={{6C3E2A51-8F0B-4C9D-9B5E-0C7A2F1D4E83}
AppName=CytoGate
AppVersion={#AppVersion}
AppVerName=CytoGate {#AppVersion}
AppPublisher=CytoGate
DefaultDirName={autopf}\CytoGate
DefaultGroupName=CytoGate
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=CytoGate-{#AppVersion}-Setup
SetupIconFile=cytogate.ico
UninstallDisplayIcon={app}\CytoGate.exe
UninstallDisplayName=CytoGate {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(CompilerPath + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "wspassoc"; Description: "Open FlowJo workspace files (.wsp) with CytoGate"; GroupDescription: "File types:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CytoGate"; Filename: "{app}\CytoGate.exe"
Name: "{group}\{cm:UninstallProgram,CytoGate}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CytoGate"; Filename: "{app}\CytoGate.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.wsp\OpenWithProgids"; ValueType: string; ValueName: "CytoGate.Workspace"; ValueData: ""; Flags: uninsdeletevalue; Tasks: wspassoc
Root: HKA; Subkey: "Software\Classes\CytoGate.Workspace"; ValueType: string; ValueName: ""; ValueData: "FlowJo workspace"; Flags: uninsdeletekey; Tasks: wspassoc
Root: HKA; Subkey: "Software\Classes\CytoGate.Workspace\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\CytoGate.exe,0"; Tasks: wspassoc
Root: HKA; Subkey: "Software\Classes\CytoGate.Workspace\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\CytoGate.exe"" ""%1"""; Tasks: wspassoc

[Run]
Filename: "{app}\CytoGate.exe"; Description: "{cm:LaunchProgram,CytoGate}"; Flags: nowait postinstall skipifsilent
