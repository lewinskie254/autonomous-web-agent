# WebGent 🕸️🤖

**An autonomous web browsing agent that navigates real websites using nothing but the DOM — no fixed selectors, no site-specific scraping code, no vision required (with an optional hybrid mode that adds screenshots when it gets stuck).**

Give it a task like *"find the cheapest watch under $500"* and a starting URL, and it drives a real Chromium browser: reading the page's interactive elements, deciding what to click/type/scroll next, and executing that action — all reasoned about step by step by Gemini.

---

## What makes this different

Most web-scraping bots are hard-coded for one site: they know exactly which CSS selector to click because a human wrote it for that specific page. This agent doesn't. It:

1. Loads any page and extracts every visible, clickable/typeable element into a compact JSON list — tag, text, labels, no fragile selectors.
2. Hands that list, the task, and recent history to Gemini and asks it to choose exactly one next action.
3. Executes that action in the real browser, then repeats — reading the new page state fresh each time.

Because it never depends on a site's own (often meaningless) class names or IDs, it can be pointed at a site it's never seen before and figure out the navigation on its own.

## Two agent modes

| | **Baseline** | **Hybrid** |
|---|---|---|
| Perception | DOM only | DOM first, screenshot only when confidence is low |
| Speed | Faster | Slower (extra vision call when triggered) |
| Cost | Lower | Higher (only pays the vision-token cost when it actually needs it) |
| Best for | Well-structured pages | Visually complex / ambiguous layouts, icon-only buttons, elements that look different than their DOM order suggests |

Both modes share the exact same prompt, response schema, DOM extractor, executor, and safety layer — the *only* difference is whether a screenshot gets added to the decision when Gemini isn't confident. This makes it easy to run the same task through both and compare which one actually performs better on a given site.

## Built-in safety

The agent will **never complete a real purchase**. Every click is checked against the target element's own label before it's executed:

- Clicks that look like they'd finalize a transaction ("Buy Now," "Place Order," "Checkout," "Pay Now," "Confirm Purchase," etc.) are **refused outright** — the agent won't click them no matter what it decided.
- "Add to Cart" / "Add to Bag" clicks are allowed (they're non-committal), but the agent **halts the run immediately afterward**, so it can never chain into an actual checkout flow.

## Architecture

```
run_task.py                  CLI — baseline agent
run_hybrid_task.py            CLI — hybrid agent
agent.py                      Baseline control loop: perceive → decide → act → log
hybrid_agent.py                 Hybrid control loop: adds confidence-gated screenshot escalation
dom_extractor.py                 Playwright + BeautifulSoup → JSON of interactive DOM elements
gemini_client.py                  Talks to the Gemini API directly over REST (no SDK dependency)
actions.py                         Action vocabulary + system prompt shared by both agents
executor.py                         Turns a decided action into a real Playwright interaction
safety.py                            Purchase-blocking guardrail
db.py                                 PostgreSQL logging: every task, step, and error
metrics.py                            Computes success rate / speed / errors, baseline vs hybrid
config.py                             Environment-driven configuration
schema.sql                            Database schema
```

## How a step works

```
┌─────────────┐     ┌────────────────┐     ┌───────────────┐     ┌──────────────┐
│  Load page  │ --> │  Extract DOM   │ --> │  Ask Gemini    │ --> │  Execute the │
│ (Playwright)│     │ as JSON (bs4)  │     │  for an action │     │  action      │
└─────────────┘     └────────────────┘     └───────────────┘     └──────────────┘
                                                    │                     │
                                          (hybrid: if low confidence,     │
                                           screenshot + re-ask)           ▼
                                                                   Log to Postgres,
                                                                   repeat until "done"
```

## Tech stack

- **Python** — orchestration
- **Playwright** — real browser automation (Chromium)
- **BeautifulSoup** — DOM parsing into structured JSON
- **Gemini API** — decision-making, called directly via `requests` (no SDK)
- **PostgreSQL** — full experiment/run logging (tasks, steps, errors, timings)

## Quickstart

```bash
git clone <this-repo>
cd WebGent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

createdb dom_agent
psql -d dom_agent -f schema.sql

cp env.example .env   # add your GEMINI_API_KEY and Postgres credentials
```

Run the baseline agent:
```bash
python run_task.py \
  --task "find the cheapest watch under $500" \
  --url "https://example-shop.com" \
  --website example_store
```

Run the hybrid agent on the same task to compare:
```bash
python run_hybrid_task.py \
  --task "find the cheapest watch under $500" \
  --url "https://example-shop.com" \
  --website example_store
```

Compare results:
```bash
python metrics.py
```
Outputs success rate, average completion time, average steps, and error rate for each agent side by side — plus, for the hybrid agent, exactly how often it needed a screenshot and how much slower those steps were.

## Configuration

All tunable via `.env`:

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_MODEL` | Which Gemini model to use | `gemini-2.0-flash` |
| `MAX_STEPS` | Hard cap on actions per task | `25` |
| `NAV_TIMEOUT_MS` | Browser navigation timeout | `15000` |
| `HEADLESS` | Run Chromium headless or visibly | `true` |
| `GEMINI_MIN_INTERVAL_SECONDS` | Throttle between Gemini calls (avoids rate limits) | `4.5` |
| `CONFIDENCE_THRESHOLD` | Hybrid agent's cutoff for triggering a screenshot | `0.6` |

## Known limitations

- Single-tab only — doesn't yet handle flows that open a new browser tab/popup.
- No CAPTCHA or anti-bot bypass — sites with aggressive bot protection may block it outright, and that's treated as an expected failure mode rather than something to defeat.
- DOM element references are re-assigned every step, so they're not stable across steps by design — always resolved fresh, never cached.
- The purchase-blocking guardrail is keyword-based against visible element text, not a semantic guarantee — extend `safety.py` if you hit a site with unusual button wording.

## Roadmap

- [ ] Multi-tab / popup handling
- [ ] Configurable action vocabulary (drag/drop, hover, keyboard shortcuts)
- [ ] Pluggable model backend (beyond Gemini)
- [ ] Web dashboard for watching runs live instead of console logs

## License

MIT (or whichever you prefer — update this section before publishing).
