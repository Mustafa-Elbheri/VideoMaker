from video_maker.error_reporting import ErrorReportDialog, build_error_report, store_error_report
from video_maker.localization import tr


class UpdateErrorDialog(ErrorReportDialog):
    """Update-specific title backed by the application-wide error reporter."""

    def __init__(self, parent, message, details, speech_callback=None):
        report = build_error_report(
            message,
            tr("تحديث البرنامج"),
            context="update",
            technical_details=details,
        )
        report = store_error_report(report)
        super().__init__(
            parent,
            message,
            report,
            speech_callback=speech_callback,
            title=tr("تحديث البرنامج"),
            close_accessible_name=tr("إغلاق نافذة خطأ التحديث"),
        )
