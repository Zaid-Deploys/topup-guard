# Bitrefill Integration Feedback — Free Society Hack Mumbai

Honest, specific feedback from building **Top-up Guard** on 4 Nov 2026 for the Bitrefill Agentic Commerce track.

## Developer Experience & API Feedback

### 1. REST Hid Small Airtel Packages
- **What happened:** Running `bitrefill get-product-details` via CLI for `airtel-india` returned 5 package tiers (₹10, ₹120, ₹500, ₹1000, ₹5000), but `GET /v2/products/airtel-india` over REST v2 returned only ₹500 and ₹1000.
- **What I expected:** Consistent package catalogs across CLI, MCP, and REST API surfaces.
- **Suggested fix:** Align catalog filtering between REST and CLI, or document intentional differences in catalog availability.

### 2. 403 Forbidden on Jio Before Account Credits
- **What happened:** `GET /v2/products/jio-india` returned HTTP 403 with `"This product is not available for your account"`, and searching for `"jio-india"` returned `reliance-jio-india-bundles`. Once credits were added, 50 Jio packages became visible.
- **What I expected:** Browseable catalog information regardless of account balance, or a descriptive reason for restriction.
- **Suggested fix:** Return clearer error messages (e.g., `"Requires funded account to query operator"`) and document catalog gating in developer documentation.

### 3. Non-Machine-Readable Package Values
- **What happened:** Jio package values in the API response are human-formatted strings with en dashes and currency symbols (e.g., `"₹14.95 Talktime Top-Up"`), lacking a clean numerical amount.
- **What I expected:** Machine-readable numerical values required for autonomous agents and guardrails to evaluate limits.
- **Suggested fix:** Include an explicit numeric `amount` or `value_inr` field alongside the display text in each package object.

### 4. Invoices Defaulted to Sats When Credits Were USD
- **What happened:** Package and invoice prices defaulted to BTC satoshis. Paying via balance failed with `"Balance too low"` because primary balance was BTC (0), even though the account held USD credits. It succeeded once USD was set as primary balance.
- **What I expected:** Ability to specify payment currency or automatic fallback to funded balance currency.
- **Suggested fix:** Add an explicit currency parameter on invoice creation and pay endpoints, and return descriptive error messages specifying which currency had insufficient balance.

### 5. Jio Talktime Paid Then Refunded Silently
- **What happened:** Two talktime orders (₹7.47 and ₹14.95) transitioned `processing` -> `all_error` / `permanent_failure` -> `refunded`. The checkout page stated only that items failed to deliver, without technical error codes. A data pack for the exact same phone number succeeded immediately.
- **What I expected:** Specific failure diagnostic reasons (e.g., operator rejection, invalid denomination) in API response.
- **Suggested fix:** Expose operator failure error codes or descriptions in invoice and order JSON responses.

### 6. Test Products Debit Real Account Balance
- **What happened:** Documentation indicates test products for development, but purchasing `test-gift-card-code` debited ~$10 USD of real account balance each time.
- **What I expected:** Zero-cost test mock products or a dedicated sandbox environment.
- **Suggested fix:** Add a prominent warning in docs that test products incur real charges, or provide a zero-cost test SKU for developer sandbox testing.

### 7. CLI MCP Endpoint Blocked on Venue Network
- **What happened:** `bitrefill get-product-details` failed on venue Wi-Fi with `"Cannot reach https://api.bitrefill.com/mcp"`, whereas direct REST API v2 calls from Python worked reliably.
- **What I expected:** CLI fallback to standard REST endpoints when MCP endpoint is unreachable.
- **Suggested fix:** Provide fallback transports in the CLI or a `--rest` flag to bypass MCP websocket/streaming dependencies.

### 8. Invoice Polling Terminal States Undocumented
- **What happened:** Invoices with `all_error` and `refunded` are terminal states, but documentation did not specify which states terminate polling.
- **What I expected:** Clear state transition machine documentation for invoice lifecycle polling.
- **Suggested fix:** Publish a status diagram and terminal state matrix (`delivered`, `refunded`, `expired`, `failed`).

## What Worked Really Well

- **Speed:** Once API credentials and balance currency were configured, the search -> invoice -> pay -> delivered cycle took ~20 seconds end-to-end.
- **AI Agent Docs:** The REST documentation in `llms.txt` was exceptionally easy for AI coding agents to parse and integrate.
- **Web Verification:** The account dashboard invoice and order detail pages made auditing deliveries straightforward.

## What I Would Build Next

1. **Telegram Approval Bot:** An async webhook flow sending spending approval buttons directly to a mobile device for out-of-band supervisor sign-off.
2. **Scheduled Autopilot Triggers:** Automated cron triggers for monthly recurring top-ups or balance-threshold top-ups running within hard guardrail caps.
