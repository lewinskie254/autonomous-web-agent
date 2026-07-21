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

Only elements that are visible and not disabled are included, to keep the
payload small and to stop the agent from targeting hidden/dead elements.
"""
from bs4 import BeautifulSoup

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
    resulting HTML with BeautifulSoup into a JSON-serializable list of
    interactive elements for the LLM to reason over.
    """
    page.evaluate(_INJECT_SCRIPT)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    elements = []
    for el in soup.select("[data-agent-ref]"):
        ref = int(el.get("data-agent-ref"))
        text = " ".join(el.get_text(strip=True).split())[:120]
        entry = {
            "ref": ref,
            "tag": el.name,
        }
        if text:
            entry["text"] = text

        # Only include attributes that actually help the model decide what
        # the element does or how to fill it in.
        for attr in ("type", "name", "placeholder", "aria-label", "value", "role", "title"):
            if el.has_attr(attr):
                entry[attr] = el.get(attr)

        if el.name == "a" and el.has_attr("href"):
            href = el.get("href")
            # Keep it short; full navigation still happens via click, this is
            # just a hint for the model.
            entry["href"] = href[:200]

        elements.append(entry)

    return elements
