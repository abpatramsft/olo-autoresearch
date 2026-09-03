"""Tiny policy router with two intentional benchmark gaps."""


def route(request: str) -> str:
    text = request.lower()

    if "status" in text or "where is" in text:
        return "status"

    if "refund" in text:
        return "refund"

    if "cancel" in text:
        return "cancel"

    return "escalate"
