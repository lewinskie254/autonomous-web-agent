"""
Safety guardrails so the agent can never complete a real transaction.

Two tiers, checked against the *target element's own text/label* (not the
task description, since that would be trivial to talk around):

1. BLOCK_KEYWORDS - things that actually spend money or finalize an order
   (checkout, place order, pay now, confirm purchase, etc). These clicks are
   refused outright; the agent never executes them, regardless of what
   Gemini decided.

2. STOP_AFTER_KEYWORDS - "add to cart" / "add to bag" style actions. These
   are non-committal (no money moves), so we let the click happen, but the
   agent loop halts immediately afterward and reports done, so it can never
   chain into an actual checkout flow in the same run.

This matches your dissertation's ethical scope ("checkout will not be
evaluated") while still allowing Task #4 ("add a product to a cart") to
succeed.
"""

BLOCK_KEYWORDS = [
    "buy now", "place order", "proceed to checkout", "checkout",
    "pay now", "complete purchase", "confirm order", "confirm purchase",
    "submit order", "complete order", "pay with", "confirm payment",
    "place your order",
]

STOP_AFTER_KEYWORDS = [
    "add to cart", "add to bag", "add to basket", "add to trolley",
]


def _element_text_blob(dom_elements: list, ref) -> str:
    for el in dom_elements:
        if el.get("ref") == ref:
            parts = [str(el.get(k, "")) for k in ("text", "aria-label", "value", "placeholder", "name")]
            return " ".join(parts).lower()
    return ""


def check_click_safety(dom_elements: list, ref):
    """
    Returns (verdict, matched_text) where verdict is one of:
      "block"      - refuse to execute this click at all
      "stop_after" - execute it, then end the run right after
      None         - no safety concern, proceed normally
    """
    blob = _element_text_blob(dom_elements, ref)
    if not blob.strip():
        return None, blob

    for kw in BLOCK_KEYWORDS:
        if kw in blob:
            return "block", blob

    for kw in STOP_AFTER_KEYWORDS:
        if kw in blob:
            return "stop_after", blob

    return None, blob
