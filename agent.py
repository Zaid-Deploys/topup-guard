#!/usr/bin/env python3
"""
Agent Terminal Chat Loop for Bitrefill Purchases.

Features:
1. Loads GROQ_API_KEY or OPENROUTER_API_KEY from .env / environment (never prints keys).
2. Prompts LLM to extract purchase intent as strict JSON:
   {"product": "...", "value": "...", "recipient": "..."}
3. Validates strictly in plain Python:
   - Product in allowlist (reliance-jio-india-bundles, airtel-india, test-gift-card-code).
   - Value is numeric, positive, and matches an allowed package value.
   - Recipient matches +91 followed by 10 digits, or 'self'.
   - Rejects invalid JSON, unknown products, negative or non-numeric values.
4. Shows parsed request and asks user for confirmation (y/n).
5. Invokes existing guardrail check and purchase flow from buy.py.
   (The LLM never sees the Bitrefill key, never triggers payment, never decides approvals).
6. Displays the invoice ID and a masked receipt upon delivery.
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple
import requests

import buy
import guardrails

# ANSI Color codes for terminal/video display
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[1;32m"
    RED = "\033[1;31m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[1;36m"
    BRIGHT_GREEN = "\033[1;92m"


# Colored status words for video-friendly output
STATUS_OK = f"{Color.GREEN}OK{Color.RESET}"
STATUS_BLOCKED = f"{Color.RED}BLOCKED{Color.RESET}"
STATUS_APPROVAL_NEEDED = f"{Color.YELLOW}APPROVAL NEEDED{Color.RESET}"
STATUS_DELIVERED = f"{Color.BRIGHT_GREEN}DELIVERED{Color.RESET}"


def mask_text(text: Any) -> str:
    """Mask any phone numbers in string."""
    if not text:
        return ""
    s = str(text)
    # Mask Indian mobile numbers with +91: +919876543210 -> +91******3210
    s = re.sub(r'(\+91)\d{6}(\d{4})', r'\1******\2', s)
    # Mask standalone 10-digit Indian mobile numbers (starts with 6-9)
    s = re.sub(r'(?<!\d)([6-9])\d{5}(\d{4})(?!\d)', r'\1*****\2', s)
    return s


def print_spending_banner() -> None:
    """Display spending banner showing today's spend against limits."""
    today_spent = guardrails.get_daily_spending()
    max_day = guardrails.get_max_per_day()
    max_purchase = guardrails.get_max_per_purchase()
    remaining = max(0.0, max_day - today_spent)

    print(f"\n{Color.CYAN}┌────────────────────────────────────────────────────────────────────────────────────────┐{Color.RESET}")
    print(
        f"{Color.CYAN}│{Color.RESET} {Color.BOLD}DAILY SPEND:{Color.RESET} ₹{today_spent:.2f} / ₹{max_day:.2f} INR  "
        f"│  {Color.BOLD}REMAINING:{Color.RESET} ₹{remaining:.2f}  "
        f"│  {Color.BOLD}TX CAP:{Color.RESET} ₹{max_purchase:.2f} INR {Color.CYAN}│{Color.RESET}"
    )
    print(f"{Color.CYAN}└────────────────────────────────────────────────────────────────────────────────────────┘{Color.RESET}\n")

ALLOWED_PRODUCTS: List[str] = [
    "reliance-jio-india-bundles",
    "airtel-india",
    "test-gift-card-code",
]

STATIC_PACKAGE_VALUES: Dict[str, Set[str]] = {
    "airtel-india": {"500", "1000"},
    "test-gift-card-code": {"10", "20", "30", "50", "100"},
    "reliance-jio-india-bundles": {
        "10", "11", "19", "20", "29", "49", "50", "69", "100", "101", "111",
        "139", "151", "175", "195", "198", "200", "219", "239", "289", "299",
        "319", "329", "349", "359", "399", "445", "449", "450", "459", "500",
        "501", "579", "629", "799", "859", "889", "899", "949", "999", "1000",
        "1028", "1029", "1049", "1199", "1799", "2025", "3599", "3999",
    },
}

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"


def get_llm_credentials() -> Tuple[str, str]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(script_dir, ".env"),
    ]

    loaded_env: Dict[str, str] = {}
    for path in candidates:
        loaded = buy.load_env_file(path)
        loaded_env.update(loaded)

    groq_key = os.getenv("GROQ_API_KEY") or loaded_env.get("GROQ_API_KEY")
    if groq_key:
        return "groq", groq_key.strip()

    openrouter_key = os.getenv("OPENROUTER_API_KEY") or loaded_env.get("OPENROUTER_API_KEY")
    if openrouter_key:
        return "openrouter", openrouter_key.strip()

    raise RuntimeError(
        "Neither GROQ_API_KEY nor OPENROUTER_API_KEY found. "
        "Please configure one in your .env file or environment."
    )


def get_allowed_package_values(product_id: str, client: Optional[buy.BitrefillClient] = None) -> Set[str]:
    allowed: Set[str] = set(STATIC_PACKAGE_VALUES.get(product_id, set()))

    if client:
        try:
            prod_info = client.get_product(product_id)
            for pkg in prod_info.get("packages", []):
                val = pkg.get("value")
                amt = pkg.get("amount")
                if val is not None:
                    allowed.add(str(val))
                if amt is not None:
                    allowed.add(str(amt))
            rng = prod_info.get("range")
            if rng and "min" in rng and "max" in rng:
                step = rng.get("step", 1) or 1
                curr = rng["min"]
                while curr <= rng["max"]:
                    allowed.add(str(curr))
                    curr += step
        except Exception:
            pass

    return allowed


def normalize_numeric_string(val: Any) -> Optional[str]:
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        return f"{f:g}"
    except (ValueError, TypeError):
        return None


def validate_purchase_request(
    parsed: Any,
    client: Optional[buy.BitrefillClient] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, str]]]:
    if not isinstance(parsed, dict):
        return False, "Output must be a valid JSON object.", None

    for key in ("product", "value", "recipient"):
        if key not in parsed:
            return False, f"Missing required field '{key}' in JSON.", None

    product = str(parsed.get("product", "")).strip().lower()
    if product not in ALLOWED_PRODUCTS:
        allowed_str = ", ".join(ALLOWED_PRODUCTS)
        return (
            False,
            f"Unknown product '{product}'. Allowed products: {allowed_str}",
            None,
        )

    raw_val = parsed.get("value")
    if raw_val is None:
        return False, "Value cannot be null or empty.", None

    raw_val_str = str(raw_val).strip()
    try:
        val_float = float(raw_val_str)
    except (ValueError, TypeError):
        return False, f"Value must be numeric, got: '{raw_val_str}'.", None

    if val_float <= 0:
        return False, f"Value must be positive, got: {raw_val_str}.", None

    normalized_val = normalize_numeric_string(raw_val_str)
    if not normalized_val:
        return False, f"Invalid numeric value format: '{raw_val_str}'.", None

    allowed_values = get_allowed_package_values(product, client)
    val_matches = (
        normalized_val in allowed_values
        or raw_val_str in allowed_values
        or (val_float.is_integer() and str(int(val_float)) in allowed_values)
    )

    if not val_matches:
        allowed_sorted = sorted(
            list(allowed_values),
            key=lambda x: float(x) if buy.is_plain_number(x) else float("inf")
        )
        allowed_sample = ", ".join(allowed_sorted[:15])
        suffix = " ..." if len(allowed_sorted) > 15 else ""
        return (
            False,
            f"Value '{raw_val_str}' is not an allowed package value for '{product}'. "
            f"Allowed values: {allowed_sample}{suffix}",
            None,
        )

    recipient = str(parsed.get("recipient", "")).strip()
    if recipient.lower() == "self":
        validated_recipient = "self"
    elif re.match(r"^\+91[0-9]{10}$", recipient):
        validated_recipient = recipient
    else:
        return (
            False,
            f"Invalid recipient '{recipient}'. Must be '+91' followed by 10 digits, or 'self'.",
            None,
        )

    return True, None, {
        "product": product,
        "value": normalized_val,
        "recipient": validated_recipient,
    }


def call_llm(
    provider: str,
    api_key: str,
    user_prompt: str,
    timeout: float = 30.0,
) -> str:
    system_instruction = (
        "You are an assistant that extracts purchase intent from user messages.\n"
        "You must respond ONLY with a single valid JSON object containing exactly three keys:\n"
        '  - "product": one of ["reliance-jio-india-bundles", "airtel-india", "test-gift-card-code"]\n'
        '  - "value": package amount or value as a numeric string (e.g. "10", "20", "50", "500", "1000")\n'
        '  - "recipient": phone number matching +91 followed by 10 digits (e.g. "+919876543210") or "self"\n'
        "Do NOT return any commentary, markdown explanation, or text outside the JSON object."
    )

    if provider == "groq":
        url = GROQ_ENDPOINT
        model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    else:
        url = OPENROUTER_ENDPOINT
        model = os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"LLM API Error [{resp.status_code}]: {resp.text}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("LLM returned empty choices list.")

    return choices[0].get("message", {}).get("content", "").strip()


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as err:
        raise ValueError(f"Failed to decode JSON from LLM: {err}")

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")

    return parsed


def mask_recipient(recipient: str) -> str:
    if not recipient or recipient.lower() == "self":
        return "self"
    if recipient.startswith("+91") and len(recipient) == 13:
        return f"+91******{recipient[-4:]}"
    return buy.mask_sensitive(recipient)


def print_masked_receipt(purchase_result: Dict[str, Any]) -> None:
    invoice_id = purchase_result.get("invoice_id", "N/A")
    status = purchase_result.get("status", "complete")
    product_id = purchase_result.get("product_id", "")
    amount_inr = purchase_result.get("amount_inr")
    recipient = purchase_result.get("recipient")
    orders = purchase_result.get("orders", [])

    print("\n" + "=" * 54)
    print("                 PURCHASE RECEIPT                 ")
    print("=" * 54)
    print(f"Invoice ID:       {invoice_id}")
    print(f"Status:           {str(status).upper()}")
    print(f"Product:          {product_id}")
    if amount_inr is not None:
        print(f"Amount (INR):     ₹{amount_inr:.2f}")
    print(f"Recipient:        {mask_recipient(str(recipient))}")

    if orders:
        for idx, order in enumerate(orders, 1):
            order_id = order.get("id")
            print(f"\nOrder #{idx} ID:     {order_id}")
            print(f"Order Status:     {order.get('status')}")
            redemption_info = order.get("redemption_info")
            if isinstance(redemption_info, dict):
                code = redemption_info.get("code")
                pin = redemption_info.get("pin")
                link = redemption_info.get("link")
                if code:
                    print(f"Redemption Code:  {buy.mask_sensitive(code)}")
                if pin:
                    print(f"Redemption PIN:   {buy.mask_sensitive(pin)}")
                if link:
                    print(f"Redemption Link:  {link}")
            elif isinstance(redemption_info, str) and redemption_info:
                print(f"Redemption Info:  {buy.mask_sensitive(redemption_info)}")
            else:
                print("Redemption Info:  Delivered directly to recipient.")
    else:
        print("Delivery:         Completed successfully.")
    print("=" * 54 + "\n")


def ask_user_confirmation(parsed_req: Dict[str, str]) -> bool:
    """Prompt user to confirm the parsed request before proceeding."""
    product = parsed_req["product"]
    value = parsed_req["value"]
    recipient = mask_text(parsed_req["recipient"])

    print("\n" + "-" * 42)
    print("Parsed Purchase Request:")
    print(f"  Product:   {product}")
    print(f"  Value:     {value}")
    print(f"  Recipient: {recipient}")
    print("-" * 42)

    while True:
        try:
            choice = input("Confirm purchase? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nPurchase cancelled.")
            return False

        if choice in ("y", "yes"):
            return True
        elif choice in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def run_agent_loop(dry_run: bool = False) -> None:
    """Main interactive terminal loop for agent."""
    print_spending_banner()

    # 1. Load LLM credentials
    try:
        provider, api_key = get_llm_credentials()
        print(f"LLM Provider: {provider.upper()} (API key loaded successfully)")
    except Exception as exc:
        print(f"\nConfiguration Error: {exc}", file=sys.stderr)
        return

    # 2. Check Bitrefill client connectivity
    bitrefill_client: Optional[buy.BitrefillClient] = None
    try:
        bf_key = buy.get_api_key()
        bitrefill_client = buy.BitrefillClient(api_key=bf_key)
    except Exception as exc:
        print(f"Notice: Bitrefill client initialized without API key ({exc}). Static catalog will be used.")

    print("\nAvailable products:")
    for p in ALLOWED_PRODUCTS:
        print(f"  - {p}")
    print("\nType your purchase request (e.g., 'Buy test card 10 for self' or 'Recharge Jio 10 on +91XXXXXXXXXX').")
    print("Type 'exit' or 'quit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting. Goodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("Goodbye!")
            break

        # Step 1: Parse Intent with LLM
        try:
            raw_llm_output = call_llm(provider, api_key, user_input)
            parsed_json = parse_llm_json(raw_llm_output)
        except Exception as exc:
            print(f"[1/4] Parse Intent:     {STATUS_BLOCKED}  (Error: {mask_text(str(exc))})")
            continue

        raw_prod = parsed_json.get("product", "")
        raw_val = parsed_json.get("value", "")
        raw_recip = mask_text(parsed_json.get("recipient", ""))
        print(f"[1/4] Parse Intent:     {STATUS_OK}  (Product: {raw_prod} | Value: {raw_val} | Recipient: {raw_recip})")

        # Step 2: Plain Python Validation
        valid, err_msg, validated_req = validate_purchase_request(parsed_json, bitrefill_client)
        if not valid or validated_req is None:
            print(f"[2/4] Validation:       {STATUS_BLOCKED}  ({mask_text(err_msg)})")
            continue

        print(f"[2/4] Validation:       {STATUS_OK}  (Allowlist, package value, recipient verified)")

        # Ask user confirmation
        confirmed = ask_user_confirmation(validated_req)
        if not confirmed:
            print("[Cancelled] Purchase cancelled by user.\n")
            continue

        # Prepare parameters for buy.py purchase flow
        product_id = validated_req["product"]
        package_value = validated_req["value"]
        recipient_phone = None if validated_req["recipient"] == "self" else validated_req["recipient"]

        # Step 3 (Guardrails) & Step 4 (Delivery) executed via buy.run_purchase_flow
        try:
            result = buy.run_purchase_flow(
                product_id=product_id,
                package_value=package_value,
                recipient_phone=recipient_phone,
                dry_run=dry_run,
                verbose=False,
            )
            if result and isinstance(result, dict):
                print_masked_receipt(result)
                print_spending_banner()
            else:
                print_spending_banner()
        except Exception:
            # buy.py already printed colored guardrail status on failure
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bitrefill Terminal Purchasing Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run purchase in dry-run mode (invoice created, not paid)")
    args = parser.parse_args()

    run_agent_loop(dry_run=args.dry_run)
