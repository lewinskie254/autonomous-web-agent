"""
DOM-only perception module.

Strategy (matches Chapter 3's baseline agent description):
1. Playwright loads the live page (so JS-rendered content is included).
2. We inject a `data-agent-ref` attribute onto every interactive element in
   the *live* DOM, so we have a stable handle we can click/type into later,
   regardless of how meaningless the site's own ids/classes are.
3. We pull page.content() (the post-injection HTML) and hand it to
   BeautifulSoup to parse into a compact JSON list of elements: tag, visible
   text, role/type, and any semantically useful attributes (placeholder,
   aria-label, name, href). This JSON is what gets sent to Gemini.

Only elements that are visible, enabled, and carry actual information (text,
label, or a meaningful attribute) are included. Modern sites often tag large
numbers of purely structural elements with `tabindex` for focus management —
those carry no signal for the model and just burn tokens, so anything with
no usable content is dropped. The remaining list is capped at
MAX_DOM_ELEMENTS, prioritizing elements that have text/label/value over bare
tags, to keep the per-step payload (and therefore token cost) bounded on
large pages.
"""
from bs4 import BeautifulSoup

MAX_DOM_ELEMENTS = 60
TEXT_MAX_CHARS = 60
HREF_MAX_CHARS = 100

INTERACTIVE_SELECTOR = (
    "a[href], button, input, select, textarea, "
    "[role='button'], [role='link'], [role='textbox'], "
    "[onclick], [tabindex]"
)

_INJECT_SCRIPT = f"""
() => {{
    const els = Array.from(document.querySelectorAll("{INTERACTIVE_SELECTOR}"));
    let ref = 0;
    for (const el of els) {{
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const visible = style.display !== 'none'
            && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
        if (!visible) continue;
        if (el.hasAttribute('disabled')) continue;
        el.setAttribute('data-agent-ref', String(ref));
        ref += 1;
    }}
    return ref;
}}
"""


def extract_dom(page) -> list:
    """
    Injects data-agent-ref attributes into the live page, then parses the
    resulting HTML with BeautifulSoup into a compact, capped JSON list of
    interactive elements for the LLM to reason over.
    """
    page.evaluate(_INJECT_SCRIPT)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    elements = []
    for el in soup.select("[data-agent-ref]"):
        ref = int(el.get("data-agent-ref"))
        text = " ".join(el.get_text(strip=True).split())[:TEXT_MAX_CHARS]
        entry = {"ref": ref, "tag": el.name}
        if text:
            entry["text"] = text

        # Only include attributes that actually help the model decide what
        # the element does or how to fill it in.
        for attr in ("type", "name", "placeholder", "aria-label", "value"):
            if el.has_attr(attr) and el.get(attr):
                entry[attr] = el.get(attr)[:TEXT_MAX_CHARS] if isinstance(el.get(attr), str) else el.get(attr)

        # "role" only adds information when it's not already implied by the
        # tag itself (e.g. role="button" on a <div>). Skip it on elements
        # whose tag already says the same thing (button, a, input).
        if el.has_attr("role") and el.name not in ("button", "a", "input", "select", "textarea"):
            entry["role"] = el.get("role")

        if el.name == "a" and el.has_attr("href"):
            entry["href"] = el.get("href")[:HREF_MAX_CHARS]

        # Drop elements that carry no usable signal at all (common with
        # tabindex-only structural wrappers) -- they're pure token cost with
        # nothing for the model to act on.
        has_signal = any(k in entry for k in ("text", "aria-label", "placeholder", "value", "href", "name"))
        if not has_signal and entry["tag"] not in ("input", "select", "textarea"):
            continue

        elements.append(entry)

    # Cap payload size: prioritize elements with real content (text/label/
    # value/placeholder) over bare/attribute-less ones, then take the first
    # MAX_DOM_ELEMENTS in DOM order within each priority group so refs stay
    # meaningful relative to page layout.
    def priority(e):
        return 0 if any(k in e for k in ("text", "aria-label", "placeholder", "value")) else 1

    if len(elements) > MAX_DOM_ELEMENTS:
        elements = sorted(elements, key=priority)[:MAX_DOM_ELEMENTS]
        elements.sort(key=lambda e: e["ref"])

    return elements
