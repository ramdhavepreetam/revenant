; Inno Setup script for the Revenant Windows installer.
; Built in CI (see .github/workflows/build-installers.yml) with:
;   ISCC.exe packaging\revenant.iss
; Installs dist_bin\revenant.exe and adds its folder to the user PATH so
; `revenant` works from any terminal.

[Setup]
AppName=Revenant
AppVersion=0.1.0
AppPublisher=Preetam Ramdhave
DefaultDirName={autopf}\Revenant
DefaultGroupName=Revenant
DisableProgramGroupPage=yes
OutputDir=..\dist_installers
OutputBaseFilename=Revenant-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
ChangesEnvironment=yes

[Files]
Source: "..\dist_bin\revenant.exe"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "addtopath"; Description: "Add Revenant to your PATH (recommended)"; GroupDescription: "Options:"

[Registry]
; Append the install dir to the per-user PATH when the task is selected.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Tasks: addtopath; \
  Check: NeedsAddPath('{app}')

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + ExpandConstant(Param) + ';', ';' + OrigPath + ';') = 0;
end;
