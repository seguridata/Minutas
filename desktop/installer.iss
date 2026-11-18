; Instalador de la app de escritorio. No se compila a mano: lo llama build.ps1.
; Se instala por usuario (sin permisos de administrador) en %LOCALAPPDATA%\Programs\Minutas.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\Minutas"
#endif

[Setup]
; No cambiar AppId: con él Windows reconoce las actualizaciones de la misma app
AppId={{FA8ED81F-E094-4DA1-84B5-01E4DEFFF51C}
AppName=Minutas SeguriData
AppVersion={#AppVersion}
AppPublisher=SeguriData
DefaultDirName={localappdata}\Programs\Minutas
DefaultGroupName=Minutas SeguriData
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Minutas-Setup
SetupIconFile=minutas\assets\icon.ico
UninstallDisplayIcon={app}\Minutas.exe
UninstallDisplayName=Minutas SeguriData
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Al actualizar, quita los archivos de la versión anterior para no mezclar bibliotecas
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Minutas SeguriData"; Filename: "{app}\Minutas.exe"
Name: "{autodesktop}\Minutas SeguriData"; Filename: "{app}\Minutas.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Minutas.exe"; Description: "{cm:LaunchProgram,Minutas SeguriData}"; Flags: nowait postinstall skipifsilent
