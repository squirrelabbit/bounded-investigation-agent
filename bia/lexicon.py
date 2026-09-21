"""Server-owned vocabulary. The DecisionProvider never contributes a search term."""
from __future__ import annotations

from typing import Dict, Tuple

PRODUCTS: Tuple[str, ...] = ("P-Alpha", "P-Beta", "P-Gamma", "P-Delta")

COMPLAINT_TYPES: Tuple[str, ...] = (
    "delivery_delay",
    "billing_error",
    "app_crash",
    "damaged_item",
    "support_wait",
)

# Each complaint_type must be supported by the ticket text itself; a ticket whose
# text shares no term with its own label is not admitted as evidence for it.
SUPPORT_TERMS: Dict[str, Tuple[str, ...]] = {
    "delivery_delay": ("delayed", "late", "has not arrived", "shipping", "dispatch"),
    "billing_error": ("charged", "billing", "invoice", "double charge", "wrong amount"),
    "app_crash": ("crash", "crashes", "freezes", "force close", "error screen"),
    "damaged_item": ("damaged", "broken", "cracked", "dented", "torn"),
    "support_wait": ("waiting", "no reply", "on hold", "response time", "unanswered"),
}

TYPE_LABELS_KO: Dict[str, str] = {
    "delivery_delay": "배송 지연",
    "billing_error": "청구 오류",
    "app_crash": "앱 비정상 종료",
    "damaged_item": "파손 배송",
    "support_wait": "상담 대기",
}


def supports_label(text: str, complaint_type: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in SUPPORT_TERMS.get(complaint_type, ()))


def label_ko(complaint_type: str) -> str:
    return TYPE_LABELS_KO.get(complaint_type, complaint_type)
