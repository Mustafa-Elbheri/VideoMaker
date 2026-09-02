#ifndef ReleaseDir
#define ReleaseDir "."
#endif

#ifndef OutputDir
#define OutputDir "."
#endif

[Setup]
AppId={{2D53B6EE-8A3D-4FBF-8B20-9C2475F1E0B2}
AppName={cm:AppName}
AppVersion=1.3.32
AppVerName={cm:AppName} 1.3.32
AppPublisher=Afaq Makkah
AppPublisherURL=https://www.facebook.com/mustafa.albheri/
AppSupportURL=https://www.facebook.com/mustafa.albheri/
AppUpdatesURL=https://t.me/elbheri100
DefaultDirName={autopf}\Afaq Makkah\Video Maker
DefaultGroupName={cm:AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=VideoMakerSetup
UninstallDisplayIcon={app}\VideoMaker.exe
UninstallDisplayName={cm:AppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=no
WizardStyle=modern
ShowLanguageDialog=yes
VersionInfoCompany=Afaq Makkah
VersionInfoDescription=Video Maker Setup 1.3.32
VersionInfoProductName=Video Maker
VersionInfoProductVersion=1.3.32.0
VersionInfoVersion=1.3.32.0

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"; LicenseFile: "license_ar.txt"
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "license_en.txt"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"; LicenseFile: "license_fr.txt"

[Messages]
arabic.LicenseAccepted=نعم سأوافق على استخدامه وفق الشريعة الإسلامية
arabic.LicenseNotAccepted=لا أوافق
english.LicenseAccepted=Yes I will use it according to Islamic law
english.LicenseNotAccepted=I do not agree
french.LicenseAccepted=Oui je l'utiliserai conformément à la loi islamique
french.LicenseNotAccepted=Je n'accepte pas
arabic.BeveledLabel=آفاق مكة
english.BeveledLabel=Afaq Makkah
french.BeveledLabel=Afaq Makkah

[CustomMessages]
arabic.AppName=صانع الفيديو
english.AppName=Video Maker
french.AppName=Créateur vidéo
arabic.ShortcutName=صانع الفيديو
english.ShortcutName=Video Maker
french.ShortcutName=Créateur vidéo
arabic.CreateDesktopShortcut=إنشاء اختصار على سطح المكتب
english.CreateDesktopShortcut=Create a desktop shortcut
french.CreateDesktopShortcut=Créer un raccourci sur le bureau
arabic.CreateStartMenuShortcut=إنشاء اختصار في قائمة ابدأ
english.CreateStartMenuShortcut=Create a Start Menu shortcut
french.CreateStartMenuShortcut=Créer un raccourci dans le menu Démarrer
arabic.AcceptUseAgreement=نعم سأوافق على استخدامه وفق الشريعة الإسلامية
english.AcceptUseAgreement=Yes I will use it according to Islamic law
french.AcceptUseAgreement=Oui je l'utiliserai conformément à la loi islamique
arabic.LaunchApp=تشغيل صانع الفيديو الآن
english.LaunchApp=Launch Video Maker now
french.LaunchApp=Lancer Créateur vidéo maintenant
arabic.LaunchBlocked=تم تثبيت صانع الفيديو بنجاح، لكن ويندوز منع تشغيله الآن بسبب سياسة التحكم في التطبيقات. افتح البرنامج من اختصار سطح المكتب أو قائمة ابدأ. إذا ظهرت نفس الرسالة عند فتحه من الاختصار فالجهاز يمنع البرامج غير الموقعة، ويحتاج السماح للبرنامج أو توقيعه رقميا.
english.LaunchBlocked=Video Maker was installed successfully, but Windows blocked launching it now because of an application control policy. Open it from the desktop shortcut or the Start Menu. If the same message appears from the shortcut, this device blocks unsigned applications and the program must be allowed or digitally signed.
french.LaunchBlocked=Créateur vidéo a été installé avec succès, mais Windows a bloqué son lancement à cause d'une stratégie de contrôle des applications. Ouvrez le programme depuis le raccourci du bureau ou le menu Démarrer. Si le même message apparaît depuis le raccourci, cet ordinateur bloque les applications non signées et le programme doit être autorisé ou signé numériquement.
arabic.LanguageCode=ar
english.LanguageCode=en
french.LanguageCode=fr

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopShortcut}"; GroupDescription: "{cm:AppName}"
Name: "startmenuicon"; Description: "{cm:CreateStartMenuShortcut}"; GroupDescription: "{cm:AppName}"

[Files]
Source: "{#ReleaseDir}\VideoMaker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\app_files\*"; DestDir: "{app}\app_files"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseDir}\app_files\assets\effects\*"; DestDir: "{userappdata}\AccessibleVideoMaker\effects"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist

Source: "{#ReleaseDir}\bundled_drivers\*"; DestDir: "{app}\bundled_drivers"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{commondesktop}\{cm:ShortcutName}"; Filename: "{app}\VideoMaker.exe"; WorkingDir: "{app}"; IconFilename: "{app}\VideoMaker.exe"; Tasks: desktopicon
Name: "{commonprograms}\{cm:ShortcutName}"; Filename: "{app}\VideoMaker.exe"; WorkingDir: "{app}"; IconFilename: "{app}\VideoMaker.exe"; Tasks: startmenuicon

[InstallDelete]
Type: filesandordirs; Name: "{app}\app_files"
Type: files; Name: "{commondesktop}\صانع الفيديو.lnk"
Type: files; Name: "{commondesktop}\Video Maker.lnk"
Type: files; Name: "{commondesktop}\Createur video.lnk"
Type: files; Name: "{commondesktop}\Créateur vidéo.lnk"
Type: files; Name: "{commonprograms}\صانع الفيديو.lnk"
Type: files; Name: "{commonprograms}\Video Maker.lnk"
Type: files; Name: "{commonprograms}\Createur video.lnk"
Type: files; Name: "{commonprograms}\Créateur vidéo.lnk"

[Code]
var
  LicenseCheckBox: TNewCheckBox;
  LaunchCheckBox: TNewCheckBox;

function PreferencesPath(): string;
begin
  Result := ExpandConstant('{userappdata}\AccessibleVideoMaker\preferences.json');
end;

procedure LicenseCheckBoxClick(Sender: TObject);
begin
  WizardForm.LicenseAcceptedRadio.Checked := LicenseCheckBox.Checked;
  WizardForm.LicenseNotAcceptedRadio.Checked := not LicenseCheckBox.Checked;
end;

procedure InitializeWizard();
begin
  WizardForm.LicenseAcceptedRadio.Visible := False;
  WizardForm.LicenseNotAcceptedRadio.Visible := False;
  LicenseCheckBox := TNewCheckBox.Create(WizardForm);
  LicenseCheckBox.Parent := WizardForm.LicensePage;
  LicenseCheckBox.Caption := ExpandConstant('{cm:AcceptUseAgreement}');
  LicenseCheckBox.Left := WizardForm.LicenseAcceptedRadio.Left;
  LicenseCheckBox.Top := WizardForm.LicenseAcceptedRadio.Top;
  LicenseCheckBox.Width := WizardForm.LicenseMemo.Width;
  LicenseCheckBox.Height := WizardForm.LicenseAcceptedRadio.Height * 2;
  LicenseCheckBox.Checked := False;
  LicenseCheckBox.OnClick := @LicenseCheckBoxClick;
  WizardForm.LicenseNotAcceptedRadio.Checked := True;
  LaunchCheckBox := TNewCheckBox.Create(WizardForm);
  LaunchCheckBox.Parent := WizardForm.FinishedPage;
  LaunchCheckBox.Caption := ExpandConstant('{cm:LaunchApp}');
  LaunchCheckBox.Left := WizardForm.FinishedLabel.Left;
  LaunchCheckBox.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(18);
  LaunchCheckBox.Width := WizardForm.FinishedLabel.Width;
  LaunchCheckBox.Height := ScaleY(32);
  LaunchCheckBox.Checked := True;
end;

function PosFrom(const SubStr, Str: string; FromIndex: Integer): Integer;
var
  Rest: string;
  P: Integer;
begin
  Result := 0;
  if (FromIndex <= 0) or (FromIndex > Length(Str)) then Exit;
  Rest := Copy(Str, FromIndex, Length(Str) - FromIndex + 1);
  P := Pos(SubStr, Rest);
  if P > 0 then
    Result := FromIndex + P - 1;
end;

procedure SaveInstallerLanguage();
var
  FilePath: string;
  Content: AnsiString;
  LangCode: string;
  PosLang: Integer;
  PosQuote1: Integer;
  PosCloseBrace: Integer;
  NewContent: AnsiString;
  Data: string;
begin
  FilePath := PreferencesPath();
  ForceDirectories(ExpandConstant('{userappdata}\AccessibleVideoMaker'));
  LangCode := ExpandConstant('{cm:LanguageCode}');

  if FileExists(FilePath) and LoadStringFromFile(FilePath, Content) and (Trim(Content) <> '') then
  begin
    PosLang := Pos('"language"', Content);
    if PosLang > 0 then
    begin
      // Preserve existing user language and preferences during updates
      Exit;
    end
    else
    begin
      PosCloseBrace := 0;
      for PosQuote1 := Length(Content) downto 1 do
      begin
        if Content[PosQuote1] = '}' then
        begin
          PosCloseBrace := PosQuote1;
          Break;
        end;
      end;
      if PosCloseBrace > 0 then
      begin
        NewContent := Trim(Copy(Content, 1, PosCloseBrace - 1));
        if (NewContent <> '') and (NewContent[Length(NewContent)] <> '{') and (NewContent[Length(NewContent)] <> ',') then
          NewContent := NewContent + ',' + #13#10 + '  "language": "' + LangCode + '"' + #13#10 + '}'
        else
          NewContent := NewContent + #13#10 + '  "language": "' + LangCode + '"' + #13#10 + '}';
        SaveStringToFile(FilePath, NewContent, False);
        Exit;
      end;
    end;
  end;

  // Fresh install: initialize preferences with chosen installer language
  Data := '{' + #34 + 'language' + #34 + ': ' + #34 + LangCode + #34 + '}';
  SaveStringToFile(FilePath, Data, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveInstallerLanguage();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if (CurPageID = wpFinished) and LaunchCheckBox.Checked then
  begin
    if not ShellExec('', ExpandConstant('{app}\VideoMaker.exe'), '', ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, ErrorCode) then
      MsgBox(ExpandConstant('{cm:LaunchBlocked}'), mbInformation, MB_OK);
  end;
end;

























































































































































































































































































[Run]
; Save Original Audio Device
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\bundled_drivers\RestoreAudio.ps1"" -Action Save"; StatusMsg: "Saving Audio Configuration..."; Flags: waituntilterminated runhidden
; Install VB-Audio Virtual Cable Silently
Filename: "{app}\bundled_drivers\vbcable\VBCABLE_Setup_x64.exe"; Parameters: "-i -h"; StatusMsg: "Installing Virtual Audio Drivers..."; Flags: waituntilterminated runhidden
; Restore Original Audio Device
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\bundled_drivers\RestoreAudio.ps1"" -Action Restore"; StatusMsg: "Restoring Audio Configuration..."; Flags: waituntilterminated runhidden
; Register OBS Virtual Camera
Filename: "regsvr32.exe"; Parameters: "/s ""{app}\bundled_drivers\obs_vcam\bin\64bit\obs-virtualcam-module64.dll"""; StatusMsg: "Registering Virtual Camera..."; Flags: runhidden
