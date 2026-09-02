import wx

from video_maker.app_state import (
    get_language,
    is_usage_consent_accepted,
    set_usage_consent_accepted,
)


TEXTS = {
    "ar": {
        "title": "تنبيه مهم جدا",
        "message": (
            "مصمم هذا البرنامج يرى حرمة استخدام هذا البرنامج في أي شيء يخالف الشريعة "
            "بما في ذلك المعازف وأي شيء لا يرضي الله وفق منهج أهل السنة والجماعة.\n\n"
            "هل توافق على استخدامه؟"
        ),
        "agree": "الموافقة على استخدامه وفق منهج أهل السنة والجماعة",
        "decline": "عدم الموافقة",
    },
    "en": {
        "title": "Very Important Notice",
        "message": (
            "The designer of this program considers it forbidden to use this program "
            "for anything that violates Sharia, including musical instruments, or "
            "anything that does not please Allah, according to the methodology of "
            "Ahl al-Sunnah wal-Jama'ah.\n\n"
            "Do you agree to use it?"
        ),
        "agree": "Agree to use it according to the methodology of Ahl al-Sunnah wal-Jama'ah",
        "decline": "Do not agree",
    },
    "fr": {
        "title": "Avis très important",
        "message": (
            "Le concepteur de ce programme considère qu'il est interdit d'utiliser "
            "ce programme pour quoi que ce soit qui enfreint la charia, y compris "
            "les instruments de musique, ou quoi que ce soit qui ne satisfait pas "
            "Allah, selon la méthodologie d'Ahl al-Sunnah wal-Jama'ah.\n\n"
            "Acceptez-vous de l'utiliser ?"
        ),
        "agree": "Accepter de l'utiliser selon la méthodologie d'Ahl al-Sunnah wal-Jama'ah",
        "decline": "Ne pas accepter",
    },
}


def _language_texts():
    return TEXTS.get(get_language(), TEXTS["ar"])


def _set_accessible_text(control, text):
    control.SetName(text)
    if hasattr(control, "SetAccessibleName"):
        try:
            control.SetAccessibleName(text)
        except Exception:
            pass
    if hasattr(control, "SetHelpText"):
        try:
            control.SetHelpText(text)
        except Exception:
            pass


class UsageConsentDialog(wx.Dialog):
    def __init__(self, parent=None):
        self.texts = _language_texts()
        super().__init__(
            parent,
            title=self.texts["title"],
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._build()

    def _build(self):
        language = get_language()
        direction = wx.Layout_RightToLeft if language == "ar" else wx.Layout_LeftToRight
        try:
            self.SetLayoutDirection(direction)
        except Exception:
            pass

        panel = wx.Panel(self)
        try:
            panel.SetLayoutDirection(direction)
        except Exception:
            pass

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        message = wx.TextCtrl(
            panel,
            value=self.texts["message"],
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
        )
        message.SetMinSize((560, 150))
        _set_accessible_text(message, self.texts["message"])

        buttons = wx.StdDialogButtonSizer()
        agree_button = wx.Button(panel, wx.ID_OK, self.texts["agree"])
        decline_button = wx.Button(panel, wx.ID_CANCEL, self.texts["decline"])
        _set_accessible_text(agree_button, self.texts["agree"])
        _set_accessible_text(decline_button, self.texts["decline"])
        buttons.AddButton(agree_button)
        buttons.AddButton(decline_button)
        buttons.Realize()

        main_sizer.Add(message, 1, wx.EXPAND | wx.ALL, 12)
        main_sizer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(main_sizer)

        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer_sizer)
        self.SetMinSize((620, 260))
        self.SetSize((700, 300))
        self.SetAffirmativeId(wx.ID_OK)
        self.SetEscapeId(wx.ID_CANCEL)
        self.CentreOnScreen()
        wx.CallAfter(message.SetFocus)


def show_usage_consent_if_needed(parent=None):
    if is_usage_consent_accepted():
        return True
    dialog = UsageConsentDialog(parent)
    try:
        accepted = dialog.ShowModal() == wx.ID_OK
    finally:
        dialog.Destroy()
    if accepted:
        set_usage_consent_accepted(True)
    return accepted
