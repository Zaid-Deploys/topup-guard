#!/usr/bin/env python3
"""
Guardrails module for agentic purchases.

Rules:
1. Max ₹200 per purchase (configurable via MAX_PER_PURCHASE_INR, default 200).
2. Max ₹500 per day (configurable via MAX_PER_DAY_INR, default 500).
3. Duplicate check using purchase key (product + value + recipient + date).
4. Track daily spending in local ledger.json.
5. check_purchase(product, amount_inr, recipient) returns 'OK', 'NEEDS_APPROVAL', or 'BLOCKED' with a reason.
6. If NEEDS_APPROVAL, interactive prompt asks 'approve? y/n'.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

DEFAULT_MAX_PER_PURCHASE_INR: float = 200.0
DEFAULT_MAX_PER_DAY_INR: float = 500.0
LEDGER_FILENAME: str = "ledger.json"


def get_max_per_purchase() -> float:
    """Retrieve maximum purchase amount in INR from environment or default."""
    val = os.getenv("MAX_PER_PURCHASE_INR")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return DEFAULT_MAX_PER_PURCHASE_INR


def get_max_per_day() -> float:
    """Retrieve maximum daily spending amount in INR from environment or default."""
    val = os.getenv("MAX_PER_DAY_INR")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return DEFAULT_MAX_PER_DAY_INR


# Expose module-level constants for inspection / default reference
MAX_PER_PURCHASE_INR: float = get_max_per_purchase()
MAX_DAILY_INR: float = get_max_per_day()


class CheckResult(NamedTuple):
    status: str
    reason: str


def get_ledger_path(custom_path: Optional[str] = None) -> Path:
    """Resolve the path to ledger.json."""
    if custom_path:
        return Path(custom_path)
    env_path = os.getenv("LEDGER_PATH")
    if env_path:
        return Path(env_path)
    # Default to current working directory or script directory
    cwd_ledger = Path(os.getcwd()) / LEDGER_FILENAME
    if cwd_ledger.parent.exists():
        return cwd_ledger
    script_ledger = Path(__file__).parent.resolve() / LEDGER_FILENAME
    return script_ledger


def load_ledger(filepath: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load transaction records from ledger.json."""
    path = get_ledger_path(filepath)
    if not path.is_file():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("purchases", [])
            return []
    except (json.JSONDecodeError, OSError):
        return []


def save_ledger(records: List[Dict[str, Any]], filepath: Optional[str] = None) -> None:
    """Save transaction records to ledger.json safely."""
    path = get_ledger_path(filepath)
    tmp_path = path.with_suffix(".tmp")
    data = {"purchases": records}
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)


def format_package_value(val: Any) -> str:
    """Normalize package value into consistent string representation."""
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except (ValueError, TypeError):
        return str(val).strip()


def generate_purchase_key(
    product: str,
    value: Any,
    recipient: Optional[str] = None,
    date_str: Optional[str] = None,
) -> str:
    """
    Generate unique purchase key based on product + value + recipient + date.
    Format: product:value:recipient:date
    """
    p = str(product).strip().lower()
    val = format_package_value(value)
    r = str(recipient).strip() if recipient else "self"
    d = date_str or datetime.now().strftime("%Y-%m-%d")
    return f"{p}:{val}:{r}:{d}"


def get_daily_spending(
    date_str: Optional[str] = None,
    filepath: Optional[str] = None,
) -> float:
    """Calculate total INR spent on a specific date."""
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")
    records = load_ledger(filepath)
    total = 0.0
    for entry in records:
        if entry.get("date") == target_date:
            try:
                total += float(entry.get("amount_inr", 0.0))
            except (ValueError, TypeError):
                continue
    return total


def check_purchase(
    product: str,
    amount_inr: float,
    recipient: Optional[str] = None,
    filepath: Optional[str] = None,
) -> CheckResult:
    """
    Check if a purchase complies with guardrails.

    Rules:
    - Duplicate check: Same product + value + recipient + date -> BLOCKED
    - Max per day: Total daily spend + amount_inr > MAX_PER_DAY_INR -> BLOCKED
    - Max per purchase: amount_inr > MAX_PER_PURCHASE_INR -> NEEDS_APPROVAL
    - Otherwise -> OK
    """
    currency = "INR"

    try:
        amount = float(amount_inr)
    except (ValueError, TypeError):
        print(f"[Guardrails] Invalid amount: {amount_inr} (Currency treated as: {currency})")
        return CheckResult("BLOCKED", f"Invalid purchase amount: {amount_inr} {currency}")

    # Print the amount and currency it is treating it as in the guardrail output
    print(f"[Guardrails] Amount: {amount:.2f} {currency} (Currency treated as: {currency})")

    if amount <= 0:
        return CheckResult("BLOCKED", f"Purchase amount must be positive, got: {amount:.2f} {currency}")

    max_per_purchase = get_max_per_purchase()
    max_daily = get_max_per_day()

    today_str = datetime.now().strftime("%Y-%m-%d")
    purchase_key = generate_purchase_key(product, amount, recipient, today_str)
    records = load_ledger(filepath)

    # 1. Duplicate check (product + value + recipient + date)
    for entry in records:
        entry_key = entry.get("purchase_key")
        if not entry_key:
            entry_key = generate_purchase_key(
                entry.get("product", ""),
                entry.get("amount_inr", ""),
                entry.get("recipient"),
                entry.get("date"),
            )
        elif len(entry_key.split(":")) == 3:
            parts = entry_key.split(":")
            entry_key = f"{parts[0]}:{format_package_value(entry.get('amount_inr', ''))}:{parts[1]}:{parts[2]}"
        if entry_key == purchase_key:
            return CheckResult(
                "BLOCKED",
                f"Duplicate purchase detected: Key '{purchase_key}' was already completed on {today_str}.",
            )

    # 2. Daily limit check (configurable MAX_PER_DAY_INR, default 500)
    daily_spent = sum(
        float(e.get("amount_inr", 0.0))
        for e in records
        if e.get("date") == today_str
    )
    if daily_spent + amount > max_daily:
        return CheckResult(
            "BLOCKED",
            f"Daily spending limit exceeded: Current spent {daily_spent:.2f} {currency} + requested {amount:.2f} {currency} = {daily_spent + amount:.2f} {currency} (Max {max_daily:.2f} {currency}/day).",
        )

    # 3. Single purchase limit check (configurable MAX_PER_PURCHASE_INR, default 200)
    if amount > max_per_purchase:
        return CheckResult(
            "NEEDS_APPROVAL",
            f"Purchase amount of {amount:.2f} {currency} exceeds per-purchase limit of {max_per_purchase:.2f} {currency} (Daily spent: {daily_spent:.2f} {currency}/{max_daily:.2f} {currency}).",
        )

    # 4. OK
    return CheckResult(
        "OK",
        f"Purchase of {amount:.2f} {currency} approved (Daily spent: {daily_spent:.2f} {currency}/{max_daily:.2f} {currency}).",
    )


def ask_approval(reason: str) -> bool:
    """Prompt user in terminal when approval is required."""
    print(f"\n[APPROVAL REQUIRED] {reason}")
    while True:
        try:
            choice = input("approve? y/n: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nOperation aborted by user.")
            return False

        if choice in ("y", "yes"):
            return True
        elif choice in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def record_purchase(
    product: str,
    amount_inr: float,
    recipient: Optional[str] = None,
    invoice_id: Optional[str] = None,
    filepath: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a delivered purchase in ledger.json."""
    records = load_ledger(filepath)
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    purchase_key = generate_purchase_key(product, amount_inr, recipient, today_str)

    entry = {
        "timestamp": now.isoformat(),
        "date": today_str,
        "product": str(product).strip(),
        "amount_inr": round(float(amount_inr), 2),
        "recipient": str(recipient).strip() if recipient else "",
        "purchase_key": purchase_key,
        "invoice_id": invoice_id,
        "status": "delivered",
    }
    records.append(entry)
    save_ledger(records, filepath)
    return entry


if __name__ == "__main__":
    # Self-test CLI for guardrails
    if len(sys.argv) < 3:
        print("Usage: python3 guardrails.py <product> <amount_inr> [recipient]")
        sys.exit(1)

    prod = sys.argv[1]
    amt = float(sys.argv[2])
    recip = sys.argv[3] if len(sys.argv) > 3 else None

    status, reason = check_purchase(prod, amt, recip)
    print(f"Status: {status}\nReason: {reason}")
    if status == "NEEDS_APPROVAL":
        approved = ask_approval(reason)
        print(f"Approved: {approved}")
