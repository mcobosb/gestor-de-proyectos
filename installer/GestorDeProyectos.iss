#define AppName "Gestor de Proyectos"
#define AppVersion "1.5.0"
#define AppPublisher "Manuel Cobos Budia"
#define AppExecutable "GestorDeProyectos.exe"

[Setup]
AppId={{9A58E46D-14C2-467B-8E08-02A1479AD457}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Gestor de Proyectos
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExecutable}
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=GestorDeProyectos-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\build\nuitka\bc3_gui.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\MANUAL_USUARIO.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"; Description: "Iniciar {#AppName}"; Flags: postinstall nowait skipifsilent
