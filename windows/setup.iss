[Setup]
AppId={{B9B4D1B0-1E72-4C5A-8C1B-2026A1B2C3D4}}
AppName=VidLoom Video Downloader
AppVersion=1.1.0
AppPublisher=Azan Khan
DefaultDirName={autopf}\VidLoom
DefaultGroupName=VidLoom
OutputDir=dist
OutputBaseFilename=VidLoom-Setup
SetupIconFile=vidloom.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\VidLoom.exe
DisableProgramGroupPage=yes

[Files]
Source: "dist\VidLoom.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VidLoom Video Downloader"; Filename: "{app}\VidLoom.exe"; IconFilename: "{app}\VidLoom.exe"
Name: "{autodesktop}\VidLoom Video Downloader"; Filename: "{app}\VidLoom.exe"; IconFilename: "{app}\VidLoom.exe"

[Run]
Filename: "{app}\VidLoom.exe"; Description: "Launch VidLoom Video Downloader"; Flags: nowait postinstall skipifsilent
