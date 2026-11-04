#!/usr/bin/env python3
"""
Bitrefill Purchase Script

Flow:
1. Load BITREFILL_API_KEY from .env / environment (no hardcoded secrets).
2. Search for product 'airtel-india' in country 'IN'.
3. Retrieve product details and find package with value '10'.
4. Create an invoice with payment_method='balance' (auto_pay=False).
5. Pay the invoice from account balance via POST /invoices/{id}/pay.
6. Poll the invoice until it reaches 'complete' / 'delivered' status.
7. Print the invoice ID and mask any redemption code.
"""

import argparse
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
import requests
import guardrails

BASE_URL = "https://api.bitrefill.com/v2"
PRODUCT_ID = "airtel-india"
TARGET_PACKAGE_VALUE = "10"
TARGET_COUNTRY = "IN"
POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 120


def load_env_file(filepath: str = ".env") -> Dict[str, str]:
    """Parse key-value pairs from a .env file without external dependencies."""
    env_vars: Dict[str, str] = {}
    if not os.path.isfile(filepath):
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                env_vars[key] = val
    return env_vars


def get_api_key() -> str:
    """Retrieve BITREFILL_API_KEY from environment or .env file."""
    # Check current process environment first
    api_key = os.getenv("BITREFILL_API_KEY")
    if api_key:
        return api_key

    # Try local .env in current working directory or script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(script_dir, ".env"),
    ]

    for path in candidates:
        loaded = load_env_file(path)
        if "BITREFILL_API_KEY" in loaded and loaded["BITREFILL_API_KEY"]:
            return loaded["BITREFILL_API_KEY"]

    raise RuntimeError(
        "BITREFILL_API_KEY not found. Please define it in a .env file or environment variable."
    )


def mask_sensitive(value: Optional[str]) -> str:
    """Mask a secret code or PIN while keeping the format discernible."""
    if not value:
        return ""
    val_str = str(value)
    if len(val_str) <= 4:
        return "*" * len(val_str)
    # Mask all characters except the first 2 and last 2
    return f"{val_str[:2]}{'*' * (len(val_str) - 4)}{val_str[-2:]}"


def mask_text(text: Any) -> str:
    """Mask any phone numbers in string."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r'(\+91)\d{6}(\d{4})', r'\1******\2', s)
    s = re.sub(r'(?<!\d)([6-9])\d{5}(\d{4})(?!\d)', r'\1*****\2', s)
    return s


COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[1;32m"
COLOR_RED = "\033[1;31m"
COLOR_YELLOW = "\033[1;33m"
COLOR_BRIGHT_GREEN = "\033[1;92m"

STATUS_OK = f"{COLOR_GREEN}OK{COLOR_RESET}"
STATUS_BLOCKED = f"{COLOR_RED}BLOCKED{COLOR_RESET}"
STATUS_APPROVAL_NEEDED = f"{COLOR_YELLOW}APPROVAL NEEDED{COLOR_RESET}"
STATUS_DELIVERED = f"{COLOR_BRIGHT_GREEN}DELIVERED{COLOR_RESET}"


def is_plain_number(val: Any) -> bool:
    """Check if value is a plain numeric string or number."""
    if val is None:
        return False
    s = str(val).strip()
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def parse_rupee_amount(text: Optional[str]) -> Optional[float]:
    """Parse rupee amount from strings like '₹7.47 Talktime Top-Up'."""
    if not text:
        return None
    match = re.search(r'[₹|Rs\.?|INR]\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)', str(text), re.IGNORECASE)
    if match:
        raw_num = match.group(1).replace(",", "")
        try:
            val = float(raw_num)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return None


_CACHED_SATS_PER_INR_RATE: Optional[float] = None


def get_sats_per_inr_rate(product_details: Optional[Dict[str, Any]] = None) -> float:
    """Derive sats-per-INR exchange rate from available packages in product (fetched once)."""
    global _CACHED_SATS_PER_INR_RATE
    if _CACHED_SATS_PER_INR_RATE is not None:
        return _CACHED_SATS_PER_INR_RATE

    if product_details:
        packages = product_details.get("packages", [])
        rates: List[float] = []
        for p in packages:
            amt = p.get("amount")
            prc = p.get("price")
            if amt is None:
                amt = parse_rupee_amount(str(p.get("value", ""))) or parse_rupee_amount(str(p.get("name", "")))
            try:
                amt_f = float(amt)
                prc_f = float(prc)
                if amt_f > 0 and prc_f > 0:
                    rates.append(prc_f / amt_f)
            except (ValueError, TypeError):
                continue
        if rates:
            _CACHED_SATS_PER_INR_RATE = sum(rates) / len(rates)
            return _CACHED_SATS_PER_INR_RATE

    _CACHED_SATS_PER_INR_RATE = 14.03
    return _CACHED_SATS_PER_INR_RATE


def convert_sats_to_inr(
    price_sats: float,
    product_details: Optional[Dict[str, Any]] = None,
) -> float:
    """Convert sats to INR using a rate fetched once."""
    rate = get_sats_per_inr_rate(product_details)
    return round(float(price_sats) / rate, 2)


class BitrefillClient:
    """Client for Bitrefill REST API v2."""

    def __init__(self, api_key: str, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("timeout", 30)
        response = self.session.request(method, url, **kwargs)

        try:
            data = response.json()
        except Exception:
            response.raise_for_status()
            raise RuntimeError(f"Unexpected non-JSON response from {url}: {response.text}")

        if not response.ok:
            error_msg = data.get("message") or data.get("error") or response.text
            raise RuntimeError(f"API Error [{response.status_code}] on {method} {endpoint}: {error_msg}")

        return data

    def search_products(self, query: str) -> List[Dict[str, Any]]:
        """Search products by query."""
        res = self._request("GET", "/products/search", params={"q": query})
        return res.get("data", [])

    def get_product(self, product_id: str) -> Dict[str, Any]:
        """Retrieve single product details including packages and range."""
        res = self._request("GET", f"/products/{product_id}")
        return res.get("data", res)

    def create_invoice(self, products_payload: List[Dict[str, Any]], payment_method: str = "balance", auto_pay: bool = False) -> Dict[str, Any]:
        """Create a new purchase invoice."""
        payload = {
            "products": products_payload,
            "payment_method": payment_method,
            "auto_pay": auto_pay,
        }
        res = self._request("POST", "/invoices", json=payload)
        return res.get("data", res)

    def pay_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Pay an unpaid invoice using account balance."""
        res = self._request("POST", f"/invoices/{invoice_id}/pay")
        return res.get("data", res)

    def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Retrieve an invoice by ID."""
        res = self._request("GET", f"/invoices/{invoice_id}")
        return res.get("data", res)

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Retrieve an order by ID."""
        res = self._request("GET", f"/orders/{order_id}")
        return res.get("data", res)


def run_purchase_flow(
    product_id: str = PRODUCT_ID,
    package_value: str = TARGET_PACKAGE_VALUE,
    country: str = TARGET_COUNTRY,
    recipient_phone: Optional[str] = None,
    dry_run: bool = False,
    timeout: float = POLL_TIMEOUT_SECONDS,
    verbose: bool = True,
):
    vprint = print if verbose else lambda *args, **kwargs: None
    api_key = get_api_key()
    client = BitrefillClient(api_key=api_key)

    vprint("=== Step 1: Searching Product ===")
    vprint(f"Searching for '{product_id}' in country '{country}'...")
    search_results = client.search_products(query=product_id)

    matched_product = None
    for p in search_results:
        p_id = p.get("id")
        p_country = p.get("country_code") or p.get("country")
        if p_id == product_id or (p_id and product_id in p_id and (not country or p_country == country)):
            matched_product = p
            break

    if matched_product:
        vprint(f"Found product in search: {matched_product.get('name')} (ID: {matched_product.get('id')})")
    else:
        vprint(f"Product '{product_id}' not explicitly found in search query results, querying direct product endpoint...")

    vprint("\n=== Step 2: Getting Package Values ===")
    product_details = client.get_product(product_id)
    packages: List[Dict[str, Any]] = product_details.get("packages", [])
    range_info = product_details.get("range")

    vprint(f"Product Name: {product_details.get('name')}")
    vprint(f"Product Currency: {product_details.get('currency')}")

    selected_package_id = None
    selected_value = None
    selected_package: Optional[Dict[str, Any]] = None

    if packages:
        vprint(f"Available Packages ({len(packages)} total):")
        for pkg in packages:
            pkg_id = pkg.get("package_id") or pkg.get("id")
            val = str(pkg.get("value"))
            price = pkg.get("price")
            vprint(f"  - Package ID: {pkg_id} | Value: {val} | Price: {price}")

            # Match the package value exactly or by amount denomination
            pkg_amt = str(pkg.get("amount")) if pkg.get("amount") is not None else ""
            if (val == str(package_value) or pkg_amt == str(package_value)) and selected_package_id is None:
                selected_package_id = pkg_id
                selected_value = pkg.get("value")
                selected_package = pkg

    if not selected_package_id and range_info:
        vprint(f"Product offers a value range: min={range_info.get('min')}, max={range_info.get('max')}, step={range_info.get('step')}")
        val_float = float(package_value)
        if range_info.get("min", 0) <= val_float <= range_info.get("max", float("inf")):
            selected_value = val_float
            vprint(f"Selected range value: {selected_value}")

    if not selected_package_id and selected_value is None:
        raise RuntimeError(
            f"Could not find a package matching value '{package_value}' for product '{product_id}'."
        )

    vprint(f"\nTarget package selected: ID={selected_package_id}, Value={selected_value}")

    phone = recipient_phone or os.getenv("RECIPIENT_PHONE")

    # Determine amount in INR for guardrail verification
    amount_inr: Optional[float] = None

    if selected_package and is_plain_number(selected_package.get("amount")):
        amount_inr = float(selected_package["amount"])
    elif is_plain_number(package_value):
        amount_inr = float(package_value)
    else:
        val_to_check = selected_value if selected_value is not None else package_value
        if is_plain_number(val_to_check):
            amount_inr = float(val_to_check)
        else:
            # Parse rupee amount from names like "₹7.47 Talktime Top-Up"
            rupee_val = parse_rupee_amount(str(val_to_check))
            if rupee_val is None and selected_package:
                rupee_val = parse_rupee_amount(str(selected_package.get("name", ""))) or parse_rupee_amount(str(selected_package.get("id", "")))
            if rupee_val is not None:
                amount_inr = rupee_val
            elif selected_package and selected_package.get("price") is not None:
                # For packages whose name has no ₹ amount, get INR amount from catalog price
                try:
                    catalog_price_sats = float(selected_package["price"])
                    if catalog_price_sats > 0:
                        amount_inr = convert_sats_to_inr(catalog_price_sats, product_details)
                except (ValueError, TypeError):
                    pass

    if amount_inr is None or amount_inr <= 0:
        raise RuntimeError(f"Could not determine INR amount for package '{val_to_check}'.")

    # Guardrail check must run BEFORE invoice is created
    vprint("\n=== Checking Guardrails ===")
    check_res = guardrails.check_purchase(
        product=product_id,
        amount_inr=amount_inr,
        recipient=phone,
    )
    vprint(f"Guardrails Status: {check_res.status}")
    vprint(f"Details: {check_res.reason}")

    if check_res.status == "BLOCKED":
        masked_reason = mask_text(check_res.reason)
        if not verbose:
            print(f"[3/4] Guardrails:       {STATUS_BLOCKED}  ({masked_reason})")
        raise RuntimeError(f"Purchase blocked by guardrails: {masked_reason}")

    if check_res.status == "NEEDS_APPROVAL":
        masked_reason = mask_text(check_res.reason)
        if not verbose:
            print(f"[3/4] Guardrails:       {STATUS_APPROVAL_NEEDED}  ({masked_reason})")
        approved = guardrails.ask_approval(masked_reason)
        if not approved:
            print("[Declined] Purchase declined by user.")
            return None
        vprint("Purchase approved by user. Proceeding...")
    else:
        if not verbose:
            today_spent = guardrails.get_daily_spending()
            max_day = guardrails.get_max_per_day()
            print(f"[3/4] Guardrails:       {STATUS_OK}  (Today's spend: ₹{today_spent:.2f} / ₹{max_day:.2f} INR)")

    vprint("\n=== Step 3: Creating Invoice ===")
    order_item: Dict[str, Any] = {
        "product_id": product_id,
        "quantity": 1,
    }
    if selected_package_id:
        order_item["package_id"] = selected_package_id
    elif selected_value is not None:
        order_item["value"] = selected_value

    if phone:
        order_item["phone_number"] = phone
        vprint(f"Attached recipient phone: {mask_sensitive(phone)}")

    invoice = client.create_invoice(
        products_payload=[order_item],
        payment_method="balance",
        auto_pay=False,
    )
    invoice_id = invoice.get("id")
    payment_info = invoice.get("payment", {}) if isinstance(invoice.get("payment"), dict) else {}
    price = payment_info.get("price", invoice.get("price"))
    currency = payment_info.get("currency", invoice.get("currency", ""))
    if currency and currency.upper() in ("BTC", "XBT"):
        currency = "sats"

    vprint(f"Invoice successfully created!")
    vprint(f"Invoice ID: {invoice_id}")
    if price is not None:
        price_str = f"{price} {currency}".strip() if currency else str(price)
        vprint(f"Invoice Price: {price_str}")
    else:
        vprint("Invoice Price: N/A")
    vprint(f"Invoice Status: {invoice.get('status')}")

    if dry_run:
        if not verbose:
            print(f"[4/4] Delivery:         {STATUS_OK}  (Dry-Run: Invoice {invoice_id} created)")
        else:
            print("\n[Dry Run] Invoice created successfully. Stopping without paying.")
        return {
            "invoice_id": invoice_id,
            "status": invoice.get("status"),
            "product_id": product_id,
            "package_value": package_value,
            "amount_inr": amount_inr,
            "recipient": phone,
            "dry_run": True,
            "invoice": invoice,
        }

    vprint("\n=== Step 4: Paying Invoice from Account Balance ===")
    pay_res = client.pay_invoice(invoice_id)
    vprint(f"Payment request submitted for invoice: {invoice_id}")
    vprint(f"Updated Invoice Status: {pay_res.get('status')}")

    vprint("\n=== Step 5: Polling Invoice until Delivered ===")
    start_time = time.time()
    delivered = False
    latest_invoice = pay_res

    while time.time() - start_time < timeout:
        time.sleep(POLL_INTERVAL_SECONDS)
        latest_invoice = client.get_invoice(invoice_id)
        current_status = latest_invoice.get("status")
        orders = latest_invoice.get("orders", [])

        orders_status_summary = [o.get("status") for o in orders]
        elapsed = int(time.time() - start_time)
        vprint(f"Polling status ({elapsed}s/{int(timeout)}s): invoice='{current_status}', orders={orders_status_summary}")

        if current_status in ("all_error", "blocked", "denied", "payment_error"):
            raise RuntimeError(f"Invoice {invoice_id} failed with status: {current_status}")

        for o in orders:
            o_status = o.get("status")
            if o_status in ("permanent_failure", "refunded", "failed"):
                o_id = o.get("id")
                raise RuntimeError(f"Order {o_id} failed with status: {o_status}")

        if current_status in ("complete", "delivered") or (orders and all(o.get("status") == "delivered" for o in orders)):
            delivered = True
            break

    if not delivered:
        raise TimeoutError(f"Invoice {invoice_id} did not reach delivered status within {timeout}s.")

    vprint("\n=== Step 6: Order Summary & Delivery Details ===")
    vprint(f"Invoice ID: {invoice_id}")
    vprint(f"Final Invoice Status: {latest_invoice.get('status')}")

    orders = latest_invoice.get("orders", [])
    for order_summary in orders:
        order_id = order_summary.get("id")
        try:
            order_data = client.get_order(order_id)
        except Exception as e:
            vprint(f"Notice: Could not fetch detailed order {order_id} ({e}), using invoice summary.")
            order_data = order_summary

        vprint(f"\nOrder ID: {order_id}")
        vprint(f"Order Status: {order_data.get('status')}")

        redemption_info = order_data.get("redemption_info")
        if isinstance(redemption_info, dict):
            code = redemption_info.get("code")
            pin = redemption_info.get("pin")
            link = redemption_info.get("link")
            instructions = redemption_info.get("instructions")

            if code:
                vprint(f"Redemption Code: {mask_sensitive(code)}")
            if pin:
                vprint(f"Redemption PIN: {mask_sensitive(pin)}")
            if link:
                vprint(f"Redemption Link: {link}")
            if instructions:
                vprint(f"Instructions: {instructions}")
        elif isinstance(redemption_info, str) and redemption_info:
            vprint(f"Redemption Info: {mask_sensitive(redemption_info)}")
        else:
            vprint("Redemption Info: Delivered directly (no voucher code required).")

    # Record purchase in ledger after delivery
    ledger_entry = guardrails.record_purchase(
        product=product_id,
        amount_inr=amount_inr,
        recipient=phone,
        invoice_id=invoice_id,
    )
    vprint(f"\n[Guardrails] Purchase recorded in ledger: {ledger_entry['purchase_key']}")

    if not verbose:
        print(f"[4/4] Delivery:         {STATUS_DELIVERED}  (Invoice ID: {invoice_id})")

    return {
        "invoice_id": invoice_id,
        "status": latest_invoice.get("status"),
        "product_id": product_id,
        "package_value": package_value,
        "amount_inr": amount_inr,
        "recipient": phone,
        "invoice": latest_invoice,
        "orders": orders,
        "ledger_entry": ledger_entry,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bitrefill Purchase Script")
    parser.add_argument("--product", type=str, default=PRODUCT_ID, help="Product ID to purchase (default: 'airtel-india')")
    parser.add_argument("--value", type=str, default="10", help="Package value to buy (default: '10')")
    parser.add_argument("--dry-run", action="store_true", help="Create invoice, print ID and price, and stop without paying")
    parser.add_argument("--timeout", type=float, default=POLL_TIMEOUT_SECONDS, help="Polling timeout in seconds (default: 120)")
    parser.add_argument("recipient_phone", nargs="?", default=None, help="Optional recipient phone number for mobile top-ups")

    args = parser.parse_args()

    try:
        run_purchase_flow(
            product_id=args.product,
            package_value=args.value,
            country=TARGET_COUNTRY,
            recipient_phone=args.recipient_phone,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
