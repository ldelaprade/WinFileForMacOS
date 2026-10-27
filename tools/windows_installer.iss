[Setup]
AppId={{36B0910F-7C43-4A59-B230-967076A369E7}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableWelcomePage=yes
OutputDir={#ProjectRoot}\dist
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile={#ProjectRoot}\resources\icons\application\windows\application.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "{#ProjectRoot}\dist\{#AppName}.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent