# Top-up Guard

A Python terminal purchasing agent that buys real mobile top-ups and gift cards through the Bitrefill REST API v2 with hard spending guardrails. The core idea is that an agent that spends real money needs limits enforced deterministically in code, not in a model prompt. Built as a submission for the Agentic Commerce (Bitrefill) track at Free Society Hack, Mumbai (4 Nov 2026), covering Lane 1 (rails and guardrails) with a natural-language terminal chat loop.

## The Loop

```
User Prompt -> LLM Intent Extraction (JSON only) -> Plain Python Validation & Allowlists
    -> Hard Guardrail Check (Caps & Duplicates) -> User Confirmation (y/n)
    -> Bitrefill REST API v2 (Search -> Details -> Invoice -> Balance Pay -> Poll Delivery)
    -> Masked Receipt Display & Append to Local Ledger
```

## Guardrails

- **Per-Purchase Cap:** Default ₹200 INR (configurable via `MAX_PER_PURCHASE_INR`). Purchases exceeding this trigger an interactive terminal approval prompt (`y/n`).
- **Daily Spending Cap:** Default ₹500 INR (configurable via `MAX_PER_DAY_INR`). Purchases exceeding this are hard-blocked (`BLOCKED`).
- **Duplicate Protection:** Blocks identical purchases made on the same calendar day using a composite key: `product:value:recipient:date`.
- **Validation & Allowlists:** Recipient must match `+91` followed by 10 digits or `self`. Products restricted to an allowlist: `reliance-jio-india-bundles`, `airtel-india`, and `test-gift-card-code`.
- **Who Decides:** Spending limits and validation are strictly enforced by Python code; approvals above the per-purchase cap are decided exclusively by the human at the terminal.
- **Maximum Agent Spend:** The hard daily cap (default ₹500 INR). The LLM never sees the Bitrefill API key, never triggers payment, and never decides approvals.

## Quickstart (Under 15 Minutes)

### Prerequisites
- Python 3.10+ and Git
- Funded Bitrefill account with API key
- (Optional) Gemini, Groq, or OpenRouter API key for the conversational agent (`agent.py`)

### Setup
```bash
git clone https://github.com/Zaid-Deploys/topup-guard.git
cd topup-guard
python3 -m venv .venv && source .venv/bin/activate
pip install requests pytest
cp .env.example .env
# Edit .env and insert your BITREFILL_API_KEY (and optional GEMINI_API_KEY / GROQ_API_KEY)
```

### Usage
- **Dry-run CLI purchase:** Create invoice and verify pricing without paying
  ```bash
  python3 buy.py --product airtel-india --value 500 --dry-run
  ```
- **Real CLI purchase:** Create invoice, pay from balance, and poll until delivered
  ```bash
  python3 buy.py --product reliance-jio-india-bundles --value 19 +91XXXXXXXXXX
  ```
  *Flags:* `--product` (catalog slug), `--value` (package amount/value), `--dry-run` (simulate without paying), `--timeout` (max polling seconds, default 120).
- **Run the conversational agent:** Video-friendly chat interface with live spending banner and color status
  ```bash
  python3 agent.py           # Real purchases with confirmation
  python3 agent.py --dry-run # Dry-run simulation mode
  ```
- **Run test suite:** Run 32 unit and integration tests
  ```bash
  pytest -v test_agent.py
  ```

## Bitrefill Touchpoints Used

Integrates directly with Bitrefill REST API v2 (`https://api.bitrefill.com/v2`):
- `GET /products/search` — Discover products by slug or query
- `GET /products/{id}` — Fetch package catalog, operator metadata, and pricing
- `POST /invoices` — Create purchase invoice for balance payment
- `POST /invoices/{id}/pay` — Execute instant payment against account balance
- `GET /invoices/{id}` — Poll invoice status until `delivered` or terminal state

*Note:* MCP, CLI, x402, and Agent Skills were NOT used in project code (Bitrefill CLI was used solely for catalog inspection).

## Built vs Reused

- **Built from scratch:** `agent.py`, `buy.py`, `guardrails.py`, and `test_agent.py` were written entirely during the hackathon on 4 Nov 2026.
- **Reused:** Bitrefill REST API v2, `requests` HTTP library, Python standard library, and LLM provider APIs (Groq/OpenRouter via HTTPS). No code from prior projects.

## Proof of Real Purchases

- **Live Delivered Invoice:** `f8661502-d2e0-415f-885e-d504f7d660bb` (Reliance Jio Bundles India, 1GB 4G/5G data pack top-up, status: `delivered`).

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
