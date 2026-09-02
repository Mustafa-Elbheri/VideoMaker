#define MyAppPublisher "Afaq Makkah"
#define MyRepository "Mustafa-Elbheri/VideoMaker"
#define MyLatestInstallerURL "https://github.com/" + MyRepository + "/releases/latest/download/VideoMakerSetup.exe"
#define MyInstallerFileName "VideoMakerSetup.exe"

[Setup]
AppId={{6B0B31F3-23C4-4F58-BF6B-A3F2D8AD7B25}
AppName={cm:AppName}
AppVersion=1.0
AppVerName={cm:AppName}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/{#MyRepository}
AppSupportURL=https://github.com/{#MyRepository}/issues
AppUpdatesURL=https://github.com/{#MyRepository}/releases/latest
CreateAppDir=no
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=no
Uninstallable=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=installer_dist
OutputBaseFilename=VideoMakerOnlineSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=yes
SetupLogging=yes
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Video Maker Online Setup
VersionInfoProductName=Video Maker Online Setup
VersionInfoProductVersion=1.0.0.0
VersionInfoVersion=1.0.0.0

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
arabic.BeveledLabel=آفاق مكة
english.BeveledLabel=Afaq Makkah
french.BeveledLabel=Afaq Makkah

[CustomMessages]
arabic.AppName=مثبت صانع الفيديو
english.AppName=Video Maker Installer
french.AppName=Installateur Créateur vidéo
arabic.ReadyMemo=سيتم تنزيل أحدث إصدار من صانع الفيديو وتثبيته تلقائيا.
english.ReadyMemo=The latest version of Video Maker will be downloaded and installed automatically.
french.ReadyMemo=La dernière version de Créateur vidéo sera téléchargée et installée automatiquement.
arabic.DownloadPageCaption=تنزيل صانع الفيديو
english.DownloadPageCaption=Downloading Video Maker
french.DownloadPageCaption=Téléchargement de Créateur vidéo
arabic.DownloadPageDescription=يرجى الانتظار أثناء تنزيل أحدث إصدار.
english.DownloadPageDescription=Please wait while the latest version is downloaded.
french.DownloadPageDescription=Veuillez patienter pendant le téléchargement de la dernière version.
arabic.DownloadProgress=تم تنزيل {0} من {1} ميجابايت
english.DownloadProgress=Downloaded {0} of {1} MB
french.DownloadProgress={0} sur {1} Mo téléchargés
arabic.DownloadProgressUnknown=تم تنزيل {0} ميجابايت
english.DownloadProgressUnknown=Downloaded {0} MB
french.DownloadProgressUnknown={0} Mo téléchargés
arabic.InstallPageCaption=تثبيت صانع الفيديو
english.InstallPageCaption=Installing Video Maker
french.InstallPageCaption=Installation de Créateur vidéo
arabic.InstallPageDescription=يرجى الانتظار أثناء تشغيل مثبت أحدث إصدار.
english.InstallPageDescription=Please wait while the latest installer runs.
french.InstallPageDescription=Veuillez patienter pendant l'exécution du dernier installateur.
arabic.InstallStatus=يتم تثبيت أحدث إصدار من صانع الفيديو...
english.InstallStatus=Installing the latest version of Video Maker...
french.InstallStatus=Installation de la dernière version de Créateur vidéo...
arabic.DownloadFailed=تعذر تنزيل أحدث إصدار. تحقق من اتصال الإنترنت ثم حاول مرة أخرى.
english.DownloadFailed=Could not download the latest version. Check your internet connection and try again.
french.DownloadFailed=Impossible de télécharger la dernière version. Vérifiez votre connexion Internet puis réessayez.
arabic.InstallFailed=تعذر تشغيل مثبت أحدث إصدار. رمز الخطأ: {0}
english.InstallFailed=Could not run the latest installer. Error code: {0}
french.InstallFailed=Impossible d'exécuter le dernier installateur. Code d'erreur : {0}
arabic.Cancelled=تم إلغاء التنزيل.
english.Cancelled=The download was cancelled.
french.Cancelled=Le téléchargement a été annulé.
arabic.LanguageArgument=arabic
english.LanguageArgument=english
french.LanguageArgument=french

[Code]
var
  DownloadPage: TDownloadWizardPage;
  DownloadedInstallerPath: String;

function MbText(const Bytes: Int64): String;
var
  Tenths: Int64;
begin
  Tenths := (Bytes * 10) div 1048576;
  Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10);
end;

function InstallerArguments(): String;
begin
  Result :=
    '/SP- /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS ' +
    '/TASKS=desktopicon,startmenuicon /LANG=' + ExpandConstant('{cm:LanguageArgument}');
end;

function OnDownloadProgress(const Url, Filename: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
    DownloadPage.SetText(
      ExpandConstant('{cm:DownloadPageDescription}'),
      FmtMessage(ExpandConstant('{cm:DownloadProgress}'), [MbText(Progress), MbText(ProgressMax)]))
  else
    DownloadPage.SetText(
      ExpandConstant('{cm:DownloadPageDescription}'),
      FmtMessage(ExpandConstant('{cm:DownloadProgressUnknown}'), [MbText(Progress)]));
  Result := True;
end;

procedure InitializeWizard();
begin
  WizardForm.ReadyMemo.Lines.Clear;
  WizardForm.ReadyMemo.Lines.Add(ExpandConstant('{cm:ReadyMemo}'));

  DownloadPage := CreateDownloadPage(
    ExpandConstant('{cm:DownloadPageCaption}'),
    ExpandConstant('{cm:DownloadPageDescription}'),
    @OnDownloadProgress);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function DownloadLatestInstaller(): Boolean;
begin
  Result := False;
  DownloadedInstallerPath := ExpandConstant('{tmp}\{#MyInstallerFileName}');
  DeleteFile(DownloadedInstallerPath);

  DownloadPage.Clear;
  DownloadPage.Add('{#MyLatestInstallerURL}', '{#MyInstallerFileName}', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      Result := True;
    except
      if DownloadPage.AbortedByUser then
        MsgBox(ExpandConstant('{cm:Cancelled}'), mbInformation, MB_OK)
      else
        MsgBox(ExpandConstant('{cm:DownloadFailed}') + #13#10 + GetExceptionMessage, mbError, MB_OK);
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
    Result := DownloadLatestInstaller();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    WizardForm.StatusLabel.Caption := ExpandConstant('{cm:InstallStatus}');
    WizardForm.FilenameLabel.Caption := ExtractFileName(DownloadedInstallerPath);
    WizardForm.ProgressGauge.Style := npbstMarquee;

    if not Exec(DownloadedInstallerPath, InstallerArguments(), '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
      RaiseException(FmtMessage(ExpandConstant('{cm:InstallFailed}'), [IntToStr(ResultCode)]));

    if ResultCode <> 0 then
      RaiseException(FmtMessage(ExpandConstant('{cm:InstallFailed}'), [IntToStr(ResultCode)]));
  end;
end;
