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
):
    api_key = get_api_key()
    client = BitrefillClient(api_key=api_key)

    print("=== Step 1: Searching Product ===")
    print(f"Searching for '{product_id}' in country '{country}'...")
    search_results = client.search_products(query=product_id)

    matched_product = None
    for p in search_results:
        p_id = p.get("id")
        p_country = p.get("country_code") or p.get("country")
        if p_id == product_id or (p_id and product_id in p_id and (not country or p_country == country)):
            matched_product = p
            break

    if matched_product:
        print(f"Found product in search: {matched_product.get('name')} (ID: {matched_product.get('id')})")
    else:
        print(f"Product '{product_id}' not explicitly found in search query results, querying direct product endpoint...")

    print("\n=== Step 2: Getting Package Values ===")
    product_details = client.get_product(product_id)
    packages: List[Dict[str, Any]] = product_details.get("packages", [])
    range_info = product_details.get("range")

    print(f"Product Name: {product_details.get('name')}")
    print(f"Product Currency: {product_details.get('currency')}")

    selected_package_id = None
    selected_value = None

    if packages:
        print(f"Available Packages ({len(packages)} total):")
        for pkg in packages:
            pkg_id = pkg.get("package_id") or pkg.get("id")
            val = str(pkg.get("value"))
            price = pkg.get("price")
            print(f"  - Package ID: {pkg_id} | Value: {val} | Price: {price}")

            # Match the package value exactly
            if val == str(package_value) and selected_package_id is None:
                selected_package_id = pkg_id
                selected_value = pkg.get("value")

    if not selected_package_id and range_info:
        print(f"Product offers a value range: min={range_info.get('min')}, max={range_info.get('max')}, step={range_info.get('step')}")
        val_float = float(package_value)
        if range_info.get("min", 0) <= val_float <= range_info.get("max", float("inf")):
            selected_value = val_float
            print(f"Selected range value: {selected_value}")

    if not selected_package_id and selected_value is None:
        raise RuntimeError(
            f"Could not find a package matching value '{package_value}' for product '{product_id}'."
        )

    print(f"\nTarget package selected: ID={selected_package_id}, Value={selected_value}")

    phone = recipient_phone or os.getenv("RECIPIENT_PHONE")

    # Determine amount in INR for guardrail verification
    try:
        amount_inr = float(selected_value) if selected_value is not None else float(package_value)
    except (ValueError, TypeError):
        amount_inr = 0.0

    print("\n=== Checking Guardrails ===")
    check_res = guardrails.check_purchase(
        product=product_id,
        amount_inr=amount_inr,
        recipient=phone,
    )
    print(f"Guardrails Status: {check_res.status}")
    print(f"Details: {check_res.reason}")

    if check_res.status == "BLOCKED":
        raise RuntimeError(f"Purchase blocked by guardrails: {check_res.reason}")

    if check_res.status == "NEEDS_APPROVAL":
        approved = guardrails.ask_approval(check_res.reason)
        if not approved:
            print("\nPurchase declined by user. Stopping.")
            return
        print("Purchase approved by user. Proceeding...")

    print("\n=== Step 3: Creating Invoice ===")
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
        print(f"Attached recipient phone: {mask_sensitive(phone)}")

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

    print(f"Invoice successfully created!")
    print(f"Invoice ID: {invoice_id}")
    if price is not None:
        price_str = f"{price} {currency}".strip() if currency else str(price)
        print(f"Invoice Price: {price_str}")
    else:
        print("Invoice Price: N/A")
    print(f"Invoice Status: {invoice.get('status')}")

    if dry_run:
        print("\n[Dry Run] Invoice created successfully. Stopping without paying.")
        return

    print("\n=== Step 4: Paying Invoice from Account Balance ===")
    pay_res = client.pay_invoice(invoice_id)
    print(f"Payment request submitted for invoice: {invoice_id}")
    print(f"Updated Invoice Status: {pay_res.get('status')}")

    print("\n=== Step 5: Polling Invoice until Delivered ===")
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
        print(f"Polling status ({elapsed}s/{int(timeout)}s): invoice='{current_status}', orders={orders_status_summary}")

        if current_status in ("complete", "delivered") or (orders and all(o.get("status") == "delivered" for o in orders)):
            delivered = True
            break

        if current_status in ("blocked", "denied", "payment_error"):
            raise RuntimeError(f"Invoice failed with terminal status: {current_status}")

    if not delivered:
        raise TimeoutError(f"Invoice {invoice_id} did not reach delivered status within {timeout}s.")

    print("\n=== Step 6: Order Summary & Delivery Details ===")
    print(f"Invoice ID: {invoice_id}")
    print(f"Final Invoice Status: {latest_invoice.get('status')}")

    orders = latest_invoice.get("orders", [])
    for order_summary in orders:
        order_id = order_summary.get("id")
        try:
            order_data = client.get_order(order_id)
        except Exception as e:
            print(f"Notice: Could not fetch detailed order {order_id} ({e}), using invoice summary.")
            order_data = order_summary

        print(f"\nOrder ID: {order_id}")
        print(f"Order Status: {order_data.get('status')}")

        redemption_info = order_data.get("redemption_info")
        if isinstance(redemption_info, dict):
            code = redemption_info.get("code")
            pin = redemption_info.get("pin")
            link = redemption_info.get("link")
            instructions = redemption_info.get("instructions")

            if code:
                print(f"Redemption Code: {mask_sensitive(code)}")
            if pin:
                print(f"Redemption PIN: {mask_sensitive(pin)}")
            if link:
                print(f"Redemption Link: {link}")
            if instructions:
                print(f"Instructions: {instructions}")
        elif isinstance(redemption_info, str) and redemption_info:
            print(f"Redemption Info: {mask_sensitive(redemption_info)}")
        else:
            print("Redemption Info: Delivered directly (no voucher code required).")

    # Record purchase in ledger after delivery
    ledger_entry = guardrails.record_purchase(
        product=product_id,
        amount_inr=amount_inr,
        recipient=phone,
        invoice_id=invoice_id,
    )
    print(f"\n[Guardrails] Purchase recorded in ledger: {ledger_entry['purchase_key']}")


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
