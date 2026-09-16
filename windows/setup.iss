[Setup]
AppId={{B9B4D1B0-1E72-4C5A-8C1B-2026A1B2C3D4}}
AppName=VidLoom Video Downloader
AppVersion=1.0.0
AppPublisher=Azan Khan
DefaultDirName={autopf}\VidLoom
DefaultGroupName=VidLoom
OutputDir=dist
OutputBaseFilename=VidLoom-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\VidLoom.exe

[Files]
Source: "dist\VidLoom.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VidLoom Video Downloader"; Filename: "{app}\VidLoom.exe"
Name: "{autodesktop}\VidLoom Video Downloader"; Filename: "{app}\VidLoom.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\VidLoom.exe"; Description: "Launch VidLoom Video Downloader"; Flags: nowait postinstall skipifsilent
