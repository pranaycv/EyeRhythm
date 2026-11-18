; EyesRhythm Inno Setup script
; Compile with:  ISCC.exe EyeRhythm.iss
; Or simply run build_installer.bat
;
; Per-user install (no admin): %LOCALAPPDATA%\EyesRhythm
; Same freeze as the portable zip (dist\EyesRhythm).

#define MyAppName "EyesRhythm"
; Keep in lockstep with app/version.py (tests/test_version.py checks this).
#define MyAppVersion "1.6.0"
#define MyAppPublisher "EyesRhythm Contributors"
#define MyAppURL "https://github.com/pranaycv/EyeRhythm"
#define MyAppExeName "EyesRhythm.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; No admin: install next to other per-user apps.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output (relative to this .iss file)
OutputDir=Output
OutputBaseFilename=EyesRhythm-Setup
SetupIconFile=..\..\assets\EyeRhythm.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Checked by default so Setup matches the promise of a Desktop shortcut.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller folder
Source: "..\..\dist\EyesRhythm\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; {autodesktop} follows OneDrive / redirected Desktop folders better than {userdesktop}.
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Clear Mark-of-the-Web on DLLs before first launch (numpy / OpenCV / pythonnet).
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ChildItem -LiteralPath '{app}' -Recurse -Include *.dll,*.exe,*.pyd -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard();
begin
  // Hide per-file paths on the Installing page (progress bar stays).
  WizardForm.FilenameLabel.Visible := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpInstalling then
  begin
    WizardForm.FilenameLabel.Visible := False;
    // Keep StatusLabel ("Installing...") — only suppress the file list line.
    WizardForm.StatusLabel.Caption := 'Installing EyesRhythm…';
  end;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  // Inno refreshes the filename caption on each file — clear it every tick.
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.FilenameLabel.Visible := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Windows will prompt for camera permission on first use.
  end;
end;
