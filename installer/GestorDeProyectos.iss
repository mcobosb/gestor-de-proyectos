#define AppName "Gestor de Proyectos"
#define AppVersion "2.2.0"
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

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Types]
Name: "full"; Description: "Instalación completa"
Name: "compact"; Description: "Solo el programa base"
Name: "custom"; Description: "Instalación personalizada"; Flags: iscustom

[Components]
Name: "core"; Description: "Programa base y catálogos de consulta"; Types: full compact custom; Flags: fixed
Name: "price_justification"; Description: "Justificación de precios"; Types: full; Flags: disablenouninstallwarning
Name: "rcd"; Description: "RCDs (cruce LER–BCCA2023 opcional)"; Types: full; Flags: disablenouninstallwarning
Name: "safety_health"; Description: "Seguridad y Salud (correspondencias automáticas: usar BCCA)"; Types: full; Flags: disablenouninstallwarning
Name: "pptp"; Description: "PPTP (correspondencias automáticas: usar BCCA)"; Types: full; Flags: disablenouninstallwarning
Name: "planning"; Description: "Planificación"; Types: full; Flags: disablenouninstallwarning
Name: "certifications"; Description: "Certificaciones"; Types: full; Flags: disablenouninstallwarning
Name: "invoicing"; Description: "Facturación"; Types: full; Flags: disablenouninstallwarning

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[InstallDelete]
; Retira solo la entrada compilada de módulos desmarcados en una actualización.
Type: files; Name: "{app}\tools\price_justification\__init__*.pyd"; Components: not price_justification
Type: files; Name: "{app}\tools\rcd\__init__*.pyd"; Components: not rcd
Type: files; Name: "{app}\tools\safety_health\__init__*.pyd"; Components: not safety_health
Type: files; Name: "{app}\tools\pptp\__init__*.pyd"; Components: not pptp
Type: files; Name: "{app}\tools\planning\__init__*.pyd"; Components: not planning
Type: files; Name: "{app}\tools\certifications\__init__*.pyd"; Components: not certifications
Type: files; Name: "{app}\tools\invoicing\__init__*.pyd"; Components: not invoicing

[Files]
#define AppDist "..\build\nuitka\bc3_gui.dist"
Source: "{#AppDist}\*"; DestDir: "{app}"; Excludes: "\tools\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "{#AppDist}\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion; Components: core
Source: "{#AppDist}\tools\_shared\*"; DestDir: "{app}\tools\_shared"; Flags: ignoreversion recursesubdirs; Components: core
Source: "{#AppDist}\tools\bim\*"; DestDir: "{app}\tools\bim"; Flags: ignoreversion recursesubdirs; Components: core
Source: "{#AppDist}\tools\document_structure\*"; DestDir: "{app}\tools\document_structure"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
; Consultas utiliza estos CSV aun cuando no estén instalados PPTP o Seguridad y Salud.
Source: "{#AppDist}\tools\pptp\docs\unes.csv"; DestDir: "{app}\tools\pptp\docs"; Flags: ignoreversion; Components: core
Source: "{#AppDist}\tools\safety_health\datos\ntps.csv"; DestDir: "{app}\tools\safety_health\datos"; Flags: ignoreversion; Components: core
Source: "{#AppDist}\tools\price_justification\*"; DestDir: "{app}\tools\price_justification"; Flags: ignoreversion recursesubdirs; Components: price_justification
Source: "{#AppDist}\tools\rcd\*"; DestDir: "{app}\tools\rcd"; Flags: ignoreversion recursesubdirs; Components: rcd
Source: "{#AppDist}\tools\safety_health\*"; DestDir: "{app}\tools\safety_health"; Excludes: "\datos\ntps.csv"; Flags: ignoreversion recursesubdirs; Components: safety_health
Source: "{#AppDist}\tools\pptp\*"; DestDir: "{app}\tools\pptp"; Excludes: "\docs\unes.csv"; Flags: ignoreversion recursesubdirs; Components: pptp
Source: "{#AppDist}\tools\planning\*"; DestDir: "{app}\tools\planning"; Flags: ignoreversion recursesubdirs; Components: planning
Source: "{#AppDist}\tools\certifications\*"; DestDir: "{app}\tools\certifications"; Flags: ignoreversion recursesubdirs; Components: certifications
Source: "{#AppDist}\tools\invoicing\*"; DestDir: "{app}\tools\invoicing"; Flags: ignoreversion recursesubdirs; Components: invoicing
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\MANUAL_USUARIO.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"; Description: "Iniciar {#AppName}"; Flags: postinstall nowait skipifsilent
