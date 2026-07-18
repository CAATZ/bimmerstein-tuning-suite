#ifndef AppVersion
  #define AppVersion "0.1.0b8"
#endif
#ifndef AppDisplayVersion
  #define AppDisplayVersion "0.1.0 Beta 8"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.1.0.8"
#endif
#ifndef SourceDir
  #define SourceDir "..\.tmp\release-build\dist\ecueditor"
#endif
#ifndef OutputDir
  #define OutputDir "..\release\0.1.0b8"
#endif
#ifndef PackageSuffix
  #define PackageSuffix ""
#endif

[Setup]
AppId={{C07E0C75-50B4-4EC6-88EF-895305A52E89}
AppName=BimmerStein Tuning Suite
AppVersion={#AppVersion}
AppVerName=BimmerStein Tuning Suite {#AppDisplayVersion}
AppPublisher=CAATZ
VersionInfoVersion={#AppNumericVersion}
VersionInfoCompany=CAATZ
VersionInfoDescription=BimmerStein Tuning Suite Beta Installer
VersionInfoProductName=BimmerStein Tuning Suite
VersionInfoProductVersion={#AppNumericVersion}
DefaultDirName={localappdata}\Programs\BimmerStein Tuning Suite
DefaultGroupName=BimmerStein Tuning Suite
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BimmerStein-Tuning-Suite-{#AppVersion}-Windows-x64{#PackageSuffix}-Setup
SetupIconFile=..\resources\icons\app.ico
UninstallDisplayIcon={app}\BimmerStein-Tuning-Suite-{#AppVersion}.ico
LicenseFile={#SourceDir}\LICENSE
InfoBeforeFile={#SourceDir}\RELEASE_NOTES.md
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\resources\icons\app.ico"; DestDir: "{app}"; DestName: "BimmerStein-Tuning-Suite-{#AppVersion}.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\BimmerStein Tuning Suite"; Filename: "{app}\BimmerStein-Tuning-Suite.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-Tuning-Suite-{#AppVersion}.ico"
Name: "{autodesktop}\BimmerStein Tuning Suite"; Filename: "{app}\BimmerStein-Tuning-Suite.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BimmerStein-Tuning-Suite-{#AppVersion}.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\BimmerStein-Tuning-Suite.exe"; Description: "Launch BimmerStein Tuning Suite"; Flags: nowait postinstall skipifsilent
