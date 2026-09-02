# -*- coding: utf-8 -*-
"""إدارة الإلغاء الموحدة للعمليات الطويلة.

الإلغاء قرار طبيعي من المستخدم، وليس خطأً تشغيليًا.  يوفر هذا الملف استثناءً
موحدًا وأدوات تعرف آمنة حتى لا تتحول عملية الإلغاء إلى تقرير خطأ أو تجميد
للواجهة بسبب اختلاف نوع الاستثناء بين FFmpeg وباقي العمال.
"""
from __future__ import annotations

from typing import Callable, Optional


class OperationCancelled(OSError):
    """توقف طبيعي بطلب المستخدم، ويحافظ على توافقه مع معالجات OSError القديمة."""

    def __init__(self, message: str = "Operation cancelled by user"):
        super().__init__(message)


_CANCEL_MARKERS = (
    "operation cancelled by user",
    "operation canceled by user",
    "cancelled by user",
    "canceled by user",
    "تم إلغاء",
    "جاري إلغاء",
)


def cancellation_requested(cancelled_callback: Optional[Callable[[], bool]] = None) -> bool:
    """قراءة طلب الإلغاء دون السماح لخطأ داخل callback بكسر العملية."""
    try:
        return bool(cancelled_callback and cancelled_callback())
    except Exception:
        return False


def raise_if_cancelled(cancelled_callback: Optional[Callable[[], bool]] = None) -> None:
    """إيقاف العملية بالاستثناء الموحد عند طلب المستخدم الإلغاء."""
    if cancellation_requested(cancelled_callback):
        raise OperationCancelled()


def is_operation_cancelled(
    error: BaseException | None = None,
    cancelled_callback: Optional[Callable[[], bool]] = None,
) -> bool:
    """التعرف على الإلغاء القديم والجديد بلا اعتماد هش على نص واحد فقط."""
    if cancellation_requested(cancelled_callback):
        return True
    if isinstance(error, OperationCancelled):
        return True
    if error is None:
        return False
    text = str(error or "").strip().lower()
    return any(marker.lower() in text for marker in _CANCEL_MARKERS)


__all__ = [
    "OperationCancelled",
    "cancellation_requested",
    "raise_if_cancelled",
    "is_operation_cancelled",
]
