import os
import pytest
from unittest.mock import patch, MagicMock

import buy

class TestBuyRecipientPhone:
    def test_get_default_recipient_phone_from_env(self):
        with patch.dict(os.environ, {"RECIPIENT_PHONE": "+919876543210"}):
            assert buy.get_default_recipient_phone() == "+919876543210"

    def test_get_default_recipient_phone_from_file(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("buy.load_env_file", return_value={"RECIPIENT_PHONE": "+919123456789"}):
                assert buy.get_default_recipient_phone() == "+919123456789"

    def test_get_default_recipient_phone_none(self):
        with patch.dict(os.environ, {"RECIPIENT_PHONE": ""}):
            with patch("buy.load_env_file", return_value={}):
                assert buy.get_default_recipient_phone() is None

    @patch("buy.BitrefillClient")
    @patch("buy.get_api_key", return_value="dummy_api_key")
    def test_phone_needed_and_missing_raises_before_invoice(self, mock_key, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.search_products.return_value = []
        mock_client.get_product.return_value = {
            "id": "airtel-india",
            "name": "Airtel India",
            "recipient_type": "phone_number",
            "packages": [{"package_id": "pkg-1", "value": "10", "price": 140, "amount": 10}],
        }

        with patch.dict(os.environ, {"RECIPIENT_PHONE": ""}):
            with patch("buy.load_env_file", return_value={}):
                with pytest.raises(RuntimeError, match="Recipient phone number is required"):
                    buy.run_purchase_flow(
                        product_id="airtel-india",
                        package_value="10",
                        recipient_phone=None,
                        dry_run=True,
                    )

        # Ensure create_invoice was never called
        assert not mock_client.create_invoice.called

    @patch("buy.BitrefillClient")
    @patch("buy.get_api_key", return_value="dummy_api_key")
    @patch("guardrails.check_purchase")
    def test_phone_needed_uses_default_and_attaches_to_invoice(
        self, mock_guardrail, mock_key, mock_client_cls, capsys
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.search_products.return_value = []
        mock_client.get_product.return_value = {
            "id": "reliance-jio-india-bundles",
            "name": "Jio Bundles",
            "recipient_type": "phone_number",
            "packages": [{"package_id": "pkg-jio-1", "value": "10", "price": 140, "amount": 10}],
        }
        mock_guardrail.return_value = MagicMock(status="OK", reason="Approved")
        mock_client.create_invoice.return_value = {
            "id": "inv-test-jio-123",
            "status": "created",
            "price": 140,
            "currency": "sats",
        }

        with patch.dict(os.environ, {"RECIPIENT_PHONE": "+919876543210"}):
            res = buy.run_purchase_flow(
                product_id="reliance-jio-india-bundles",
                package_value="10",
                recipient_phone=None,
                dry_run=True,
                verbose=True,
            )

        assert res["recipient"] == "+919876543210"
        assert mock_client.create_invoice.called
        call_kwargs = mock_client.create_invoice.call_args.kwargs
        products = call_kwargs["products_payload"]
        assert products[0]["phone_number"] == "+919876543210"

        # Check printed output has masked phone number
        out = capsys.readouterr().out
        assert "+91******3210" in out
        assert "987654" not in out

    @patch("buy.BitrefillClient")
    @patch("buy.get_api_key", return_value="dummy_api_key")
    @patch("guardrails.check_purchase")
    def test_cli_argument_overrides_default_phone(
        self, mock_guardrail, mock_key, mock_client_cls, capsys
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.search_products.return_value = []
        mock_client.get_product.return_value = {
            "id": "airtel-india",
            "name": "Airtel India",
            "recipient_type": "phone_number",
            "packages": [{"package_id": "pkg-airtel-1", "value": "500", "price": 7000, "amount": 500}],
        }
        mock_guardrail.return_value = MagicMock(status="OK", reason="Approved")
        mock_client.create_invoice.return_value = {
            "id": "inv-test-airtel-456",
            "status": "created",
            "price": 7000,
            "currency": "sats",
        }

        # Environment has phone A, CLI passes phone B
        with patch.dict(os.environ, {"RECIPIENT_PHONE": "+919876543210"}):
            res = buy.run_purchase_flow(
                product_id="airtel-india",
                package_value="500",
                recipient_phone="+919123456789",
                dry_run=True,
                verbose=True,
            )

        assert res["recipient"] == "+919123456789"
        products = mock_client.create_invoice.call_args.kwargs["products_payload"]
        assert products[0]["phone_number"] == "+919123456789"

        out = capsys.readouterr().out
        assert "+91******6789" in out
        assert "912345" not in out

    @patch("buy.BitrefillClient")
    @patch("buy.get_api_key", return_value="dummy_api_key")
    @patch("guardrails.check_purchase")
    def test_non_phone_product_does_not_require_phone(
        self, mock_guardrail, mock_key, mock_client_cls
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.search_products.return_value = []
        mock_client.get_product.return_value = {
            "id": "test-gift-card-code",
            "name": "Test Gift Card",
            "recipient_type": None,
            "packages": [{"package_id": "pkg-gc-1", "value": "10", "price": 140, "amount": 10}],
        }
        mock_guardrail.return_value = MagicMock(status="OK", reason="Approved")
        mock_client.create_invoice.return_value = {
            "id": "inv-test-gc-789",
            "status": "created",
            "price": 140,
            "currency": "sats",
        }

        with patch.dict(os.environ, {"RECIPIENT_PHONE": ""}):
            with patch("buy.load_env_file", return_value={}):
                res = buy.run_purchase_flow(
                    product_id="test-gift-card-code",
                    package_value="10",
                    recipient_phone=None,
                    dry_run=True,
                )

        assert res["invoice_id"] == "inv-test-gc-789"
        products = mock_client.create_invoice.call_args.kwargs["products_payload"]
        assert "phone_number" not in products[0]
