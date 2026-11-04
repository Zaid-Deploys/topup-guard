# Top-up Guard

A Python purchasing script (`buy.py`) that buys real mobile top-ups and gift cards through the Bitrefill REST API v2 with hard spending guardrails. The core idea is that an agent that spends real money needs limits enforced deterministically in code, not in a model prompt. Built as a submission for the Agentic Commerce (Bitrefill) track at Free Society Hack, Mumbai (4 Nov 2026), fitting Lane 1 (rails and guardrails).

## The Main Loop (buy.py)

```
CLI Request (product, value, recipient)
    -> Hard Guardrail Check (per-purchase cap, daily cap, duplicate check)
    -> Bitrefill REST API v2 (Search -> Details -> Invoice -> Balance Pay -> Poll Delivery)
    -> Masked Receipt Display & Append to Local Ledger (ledger.json)
```

## Guardrails

- **Per-Purchase Cap:** Default ₹200 INR (configurable via `MAX_PER_PURCHASE_INR`). Purchases exceeding this trigger an interactive terminal prompt (`approve? y/n`).
- **Daily Spending Cap:** Default ₹500 INR (configurable via `MAX_PER_DAY_INR`). Purchases exceeding this are hard-blocked (`BLOCKED`).
- **Duplicate Protection:** Blocks identical purchases made on the same calendar day using key: `product:value:recipient:date`.
- **Recipient Phone:** For products with `recipient_type == "phone_number"`, defaults to `RECIPIENT_PHONE` from environment / `.env`. A command-line argument overrides it. If missing when required, execution halts with a clear error before invoice creation.
- **Who Decides:** Limits are strictly enforced in Python code; approvals above the single-purchase cap require human terminal input (`y/n`). The maximum an agent can spend is the daily cap (₹500 default).

## Quickstart (Under 15 Minutes)

### Prerequisites
- Python 3.10+ and Git
- Funded Bitrefill account with API key
- (Optional) Gemini, Groq, or OpenRouter API key for the experimental chat front end

### Setup
```bash
git clone https://github.com/Zaid-Deploys/topup-guard.git
cd topup-guard
python3 -m venv .venv && source .venv/bin/activate
pip install requests pytest
cp .env.example .env
# Edit .env and set BITREFILL_API_KEY and RECIPIENT_PHONE
```

### Main Demo Commands
- **Dry-run purchase:** Create invoice and verify pricing without paying
  ```bash
  python3 buy.py --product airtel-india --value 500 --dry-run
  ```
- **Live purchase:** Create invoice, pay from balance, poll until delivered
  ```bash
  python3 buy.py --product reliance-jio-india-bundles --value 19 +91XXXXXXXXXX
  ```
  *Flags:* `--product` (catalog slug), `--value` (package value), `--dry-run` (simulate without paying), `--timeout` (max polling seconds, default 120).

- **Run test suite:**
  ```bash
  pytest -v
  ```

## Experimental: LLM Chat Front End (agent.py)

`agent.py` provides an experimental natural-language terminal chat loop on top of `buy.py`:
- **Intent Extraction:** Prompts Gemini or Groq to parse user input strictly into `{product, value, recipient}` as JSON.
- **Plain Python Validation:** Python strictly validates extracted fields against an allowlist (`reliance-jio-india-bundles`, `airtel-india`, `test-gift-card-code`), checks that values match allowed catalog packages, and verifies phone format (`+91XXXXXXXXXX` or `self`).
- **Human Confirmation:** Always presents parsed parameters to the user for explicit confirmation (`Confirm purchase? (y/n)`).
- **LLM Dependency & Fail-Safe:** The chat front end depends on the LLM provider being available. If the provider returns 503 or transient errors, it retries up to 3 times (1s, 2s, 4s backoff) and falls back to alternate providers. If all providers fail, it fails safe with `LLM unavailable, nothing was bought` and waits for next input without buying anything.
- **Run the agent:**
  ```bash
  python3 agent.py           # Interactive loop
  python3 agent.py --dry-run # Dry-run simulation mode
  ```

## Bitrefill Touchpoints Used

Integrates directly with Bitrefill REST API v2 (`https://api.bitrefill.com/v2`):
- `GET /products/search` — Discover products by slug or query
- `GET /products/{id}` — Fetch package catalog, operator metadata, and pricing
- `POST /invoices` — Create purchase invoice for balance payment
- `POST /invoices/{id}/pay` — Execute payment against account balance
- `GET /invoices/{id}` — Poll invoice status until `delivered` or terminal state

*Note:* MCP, CLI, x402, and Agent Skills were NOT used in project code (Bitrefill CLI was used solely for catalog inspection).

## Built vs Reused

- **Built from scratch:** `agent.py`, `buy.py`, `guardrails.py`, `test_agent.py`, and `test_buy.py` were written entirely during the hackathon on 4 Nov 2026.
- **Reused:** Bitrefill REST API v2, `requests` HTTP library, and Python standard library. No code from prior projects.

## Proof of Real Purchases

- **Delivered Invoice:** `f8661502-d2e0-415f-885e-d504f7d660bb` (Reliance Jio Bundles India, 1GB data pack top-up, status: `delivered`).

## Known Gaps

- **Local Ledger:** Spend tracking is stored in a local `ledger.json` file without multi-process locking or database persistence.
- **Terminal Approval Only:** Human-in-the-loop approval requires stdin at the terminal; no remote webhook/mobile notifications.
- **Rupee Estimation:** For packages without explicit ₹ values in their title, INR amount is estimated using derived sats/INR exchange rates.
- **Limited Catalog Allowlist:** Hardcoded to three vetted products (`reliance-jio-india-bundles`, `airtel-india`, `test-gift-card-code`).
- **Polling Loop:** Simple synchronous sleep loop without webhook callbacks.
- **No Refund Handling / Scheduling:** No automatic recovery for operator-side refunds or cron-based top-up triggers; single-user design.

## Privacy & Safety

- Secrets live exclusively in `.env` (git-ignored, never committed).
- LLM receives only user text prompts and returns JSON; it never receives the Bitrefill API key.
- Phone numbers, API keys, PINs, and redemption codes are masked on screen and in logs (`+91XXXXXXXXXX`).
- Local ledger (`ledger.json`) is git-ignored.
