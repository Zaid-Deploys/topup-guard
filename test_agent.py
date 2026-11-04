import json
import os
import pytest
from unittest.mock import patch, MagicMock

import agent
import buy
import guardrails


class TestValidation:
    def test_valid_gift_card(self):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': 'self'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is True
        assert err is None
        assert parsed == {'product': 'test-gift-card-code', 'value': '10', 'recipient': 'self'}

    def test_valid_airtel_with_phone(self):
        req = {'product': 'airtel-india', 'value': '500', 'recipient': '+919876543210'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is True
        assert err is None
        assert parsed == {'product': 'airtel-india', 'value': '500', 'recipient': '+919876543210'}

    def test_valid_jio_with_int_value(self):
        req = {'product': 'reliance-jio-india-bundles', 'value': 10, 'recipient': '+919123456789'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is True
        assert err is None
        assert parsed == {'product': 'reliance-jio-india-bundles', 'value': '10', 'recipient': '+919123456789'}

    def test_reject_unknown_product(self):
        req = {'product': 'vodafone-india', 'value': '10', 'recipient': 'self'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Unknown product' in err
        assert parsed is None

    def test_reject_non_numeric_value(self):
        req = {'product': 'test-gift-card-code', 'value': 'ten_dollars', 'recipient': 'self'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Value must be numeric' in err
        assert parsed is None

    def test_reject_negative_value(self):
        req = {'product': 'test-gift-card-code', 'value': '-10', 'recipient': 'self'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Value must be positive' in err
        assert parsed is None

    def test_reject_zero_value(self):
        req = {'product': 'test-gift-card-code', 'value': '0', 'recipient': 'self'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Value must be positive' in err
        assert parsed is None

    def test_reject_disallowed_package_value(self):
        # Airtel only allows 500 and 1000
        req = {'product': 'airtel-india', 'value': '10', 'recipient': '+919876543210'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'not an allowed package value' in err
        assert parsed is None

    def test_reject_invalid_recipient_no_plus91(self):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': '9876543210'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Invalid recipient' in err
        assert parsed is None

    def test_reject_invalid_recipient_too_short(self):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': '+9198765'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Invalid recipient' in err
        assert parsed is None

    def test_reject_missing_fields(self):
        req = {'product': 'test-gift-card-code', 'value': '10'}
        valid, err, parsed = agent.validate_purchase_request(req)
        assert valid is False
        assert 'Missing required field' in err
        assert parsed is None

    def test_reject_non_dict_input(self):
        valid, err, parsed = agent.validate_purchase_request('not a json object')
        assert valid is False
        assert 'Output must be a valid JSON object' in err
        assert parsed is None


class TestJSONParsing:
    def test_parse_plain_json(self):
        raw = '{"product": "test-gift-card-code", "value": "10", "recipient": "self"}'
        res = agent.parse_llm_json(raw)
        assert res['product'] == 'test-gift-card-code'
        assert res['value'] == '10'
        assert res['recipient'] == 'self'

    def test_parse_markdown_fenced_json(self):
        raw = "```json\n{\"product\": \"test-gift-card-code\", \"value\": \"10\", \"recipient\": \"self\"}\n```"
        res = agent.parse_llm_json(raw)
        assert res['product'] == 'test-gift-card-code'

    def test_parse_invalid_json_raises(self):
        raw = 'Invalid non-json response'
        with pytest.raises(ValueError, match='Failed to decode JSON'):
            agent.parse_llm_json(raw)


class TestMasking:
    def test_mask_recipient_self(self):
        assert agent.mask_recipient('self') == 'self'

    def test_mask_recipient_phone(self):
        masked = agent.mask_recipient('+919876543210')
        assert masked == '+91******3210'
        assert '987654' not in masked

    def test_mask_sensitive_code(self):
        code = 'ABCD1234EF'
        masked = buy.mask_sensitive(code)
        assert masked == 'AB******EF'
        assert '1234' not in masked


class TestConfirmation:
    @patch('builtins.input', side_effect=['y'])
    def test_confirmation_yes(self, mock_input):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': 'self'}
        assert agent.ask_user_confirmation(req) is True

    @patch('builtins.input', side_effect=['n'])
    def test_confirmation_no(self, mock_input):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': 'self'}
        assert agent.ask_user_confirmation(req) is False

    @patch('builtins.input', side_effect=['maybe', 'n'])
    def test_confirmation_invalid_then_no(self, mock_input):
        req = {'product': 'test-gift-card-code', 'value': '10', 'recipient': 'self'}
        assert agent.ask_user_confirmation(req) is False


class TestSecurityConstraints:
    @patch('requests.post')
    def test_llm_never_receives_bitrefill_key(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'choices': [{
                'message': {
                    'content': '{"product": "test-gift-card-code", "value": "10", "recipient": "self"}'
                }
            }]
        }
        mock_post.return_value = mock_response

        # Execute call_llm
        dummy_llm_key = 'groq-dummy-test-key-12345'
        agent.call_llm('groq', dummy_llm_key, 'Buy test gift card 10')

        # Inspect headers and payload sent to LLM
        called_args, called_kwargs = mock_post.call_args
        headers = called_kwargs.get('headers', {})
        payload = called_kwargs.get('json', {})

        # Verify GROQ key is in headers, not Bitrefill key
        assert headers['Authorization'] == f'Bearer {dummy_llm_key}'

        # Ensure no bitrefill keys exist in messages
        payload_str = json.dumps(payload)
        bf_key = os.getenv('BITREFILL_API_KEY', '')
        if bf_key:
            assert bf_key not in payload_str

    @patch('requests.post')
    def test_llm_never_receives_bitrefill_key_gemini(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{'text': '{"product": "test-gift-card-code", "value": "10", "recipient": "self"}'}]
                }
            }]
        }
        mock_post.return_value = mock_response

        dummy_gemini_key = 'gemini-dummy-secret-key-9999'
        agent.call_llm('gemini', dummy_gemini_key, 'Buy test gift card 10')

        called_args, called_kwargs = mock_post.call_args
        headers = called_kwargs.get('headers', {})
        payload = called_kwargs.get('json', {})

        # Verify x-goog-api-key header
        assert headers['x-goog-api-key'] == dummy_gemini_key
        # Verify no bitrefill key in payload
        bf_key = os.getenv('BITREFILL_API_KEY', '')
        if bf_key:
            assert bf_key not in json.dumps(payload)


class TestCredentials:
    def test_get_llm_credentials_gemini(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'AIzaSyTestGeminiKey', 'GROQ_API_KEY': '', 'OPENROUTER_API_KEY': ''}):
            with patch('buy.load_env_file', return_value={}):
                provider, key = agent.get_llm_credentials()
                assert provider == 'gemini'
                assert key == 'AIzaSyTestGeminiKey'

    def test_get_llm_credentials_priority_gemini_over_groq_and_openrouter(self):
        with patch.dict(os.environ, {
            'GEMINI_API_KEY': 'gemini_key_1',
            'GROQ_API_KEY': 'groq_key_2',
            'OPENROUTER_API_KEY': 'openrouter_key_3',
        }):
            with patch('buy.load_env_file', return_value={}):
                provider, key = agent.get_llm_credentials()
                assert provider == 'gemini'
                assert key == 'gemini_key_1'

    def test_get_llm_credentials_groq(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': 'gsk_test123', 'OPENROUTER_API_KEY': ''}):
            with patch('buy.load_env_file', return_value={}):
                provider, key = agent.get_llm_credentials()
                assert provider == 'groq'
                assert key == 'gsk_test123'

    def test_get_llm_credentials_openrouter(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'OPENROUTER_API_KEY': 'sk-or-v1-test456'}):
            with patch('buy.load_env_file', return_value={}):
                provider, key = agent.get_llm_credentials()
                assert provider == 'openrouter'
                assert key == 'sk-or-v1-test456'

    def test_get_llm_credentials_missing(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'OPENROUTER_API_KEY': ''}):
            with patch('buy.load_env_file', return_value={}):
                with pytest.raises(RuntimeError) as exc_info:
                    agent.get_llm_credentials()
                msg = str(exc_info.value)
                assert 'GEMINI_API_KEY' in msg
                assert 'GROQ_API_KEY' in msg
                assert 'OPENROUTER_API_KEY' in msg

    def test_configuration_error_message(self, capsys):
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'OPENROUTER_API_KEY': ''}):
            with patch('buy.load_env_file', return_value={}):
                agent.run_agent_loop()
                err = capsys.readouterr().err
                assert 'Configuration Error:' in err
                assert 'GEMINI_API_KEY' in err
                assert 'GROQ_API_KEY' in err
                assert 'OPENROUTER_API_KEY' in err


class TestReceiptPrinting:
    def test_print_masked_receipt(self, capsys):
        mock_result = {
            'invoice_id': 'inv-1234-abcd',
            'status': 'delivered',
            'product_id': 'reliance-jio-india-bundles',
            'amount_inr': 10.0,
            'recipient': '+919876543210',
            'orders': [{
                'id': 'ord-5678',
                'status': 'delivered',
                'redemption_info': {'code': 'SECRET1234CODE', 'pin': '9876'},
            }],
        }
        agent.print_masked_receipt(mock_result)
        captured = capsys.readouterr().out

        assert 'Invoice ID:       inv-1234-abcd' in captured
        assert 'Status:           DELIVERED' in captured
        assert 'reliance-jio-india-bundles' in captured
        assert '₹10.00' in captured
        assert '+91******3210' in captured
        assert '987654' not in captured
        # Code masked
        assert 'SE**********DE' in captured
        assert 'SECRET1234CODE' not in captured


class TestAgentLoopIntegration:
    @patch('agent.get_llm_credentials', return_value=('groq', 'fake-key'))
    @patch('agent.call_llm', return_value='{"product": "test-gift-card-code", "value": "10", "recipient": "self"}')
    @patch('builtins.input', side_effect=['Buy test card', 'y', 'exit'])
    @patch('buy.run_purchase_flow')
    def test_agent_loop_confirmed(self, mock_buy_flow, mock_input, mock_call_llm, mock_creds):
        mock_buy_flow.return_value = {
            'invoice_id': 'inv-test-999',
            'status': 'complete',
            'product_id': 'test-gift-card-code',
            'amount_inr': 10.0,
            'recipient': None,
            'orders': [],
        }
        agent.run_agent_loop(dry_run=True)
        assert mock_buy_flow.called
        kwargs = mock_buy_flow.call_args.kwargs
        assert kwargs['product_id'] == 'test-gift-card-code'
        assert kwargs['package_value'] == '10'
        assert kwargs['recipient_phone'] is None
        assert kwargs['dry_run'] is True

    @patch('agent.get_llm_credentials', return_value=('groq', 'fake-key'))
    @patch('agent.call_llm', return_value='{"product": "test-gift-card-code", "value": "10", "recipient": "self"}')
    @patch('builtins.input', side_effect=['Buy test card', 'n', 'exit'])
    @patch('buy.run_purchase_flow')
    def test_agent_loop_declined(self, mock_buy_flow, mock_input, mock_call_llm, mock_creds):
        agent.run_agent_loop(dry_run=True)
        assert not mock_buy_flow.called


class TestVideoFormatting:
    def test_spending_banner_display(self, capsys):
        with patch('guardrails.get_daily_spending', return_value=120.50):
            with patch('guardrails.get_max_per_day', return_value=500.00):
                with patch('guardrails.get_max_per_purchase', return_value=200.00):
                    agent.print_spending_banner()
        captured = capsys.readouterr().out
        assert 'DAILY SPEND:' in captured
        assert '₹120.50 / ₹500.00 INR' in captured
        assert 'REMAINING:' in captured
        assert '₹379.50' in captured
        assert 'TX CAP:' in captured
        assert '₹200.00 INR' in captured

    def test_colored_status_words_defined(self):
        assert 'OK' in agent.STATUS_OK
        assert '\033[1;32m' in agent.STATUS_OK  # Green
        assert 'BLOCKED' in agent.STATUS_BLOCKED
        assert '\033[1;31m' in agent.STATUS_BLOCKED  # Red
        assert 'APPROVAL NEEDED' in agent.STATUS_APPROVAL_NEEDED
        assert '\033[1;33m' in agent.STATUS_APPROVAL_NEEDED  # Yellow
        assert 'DELIVERED' in agent.STATUS_DELIVERED
        assert '\033[1;92m' in agent.STATUS_DELIVERED or '\033[1;32m' in agent.STATUS_DELIVERED  # Green

    def test_mask_text_phone_numbers(self):
        # +91 followed by 10 digits
        assert agent.mask_text('+919876543210') == '+91******3210'
        # Embedded in reason string
        reason = "Key 'reliance-jio-india-bundles:18.14:+919123456789:2026-11-04' completed"
        masked = agent.mask_text(reason)
        assert '+91******6789' in masked
        assert '912345' not in masked
        # Standalone 10 digit Indian number
        assert '9*****3210' in agent.mask_text('Call 9876543210 today')

    @patch('agent.get_llm_credentials', return_value=('groq', 'fake-key'))
    @patch('agent.call_llm', return_value='{"product": "airtel-india", "value": "500", "recipient": "+919876543210"}')
    @patch('builtins.input', side_effect=['Buy airtel 500 for +919876543210', 'y', 'exit'])
    @patch('buy.run_purchase_flow')
    def test_step_by_step_one_line_outputs(self, mock_buy_flow, mock_input, mock_call_llm, mock_creds, capsys):
        mock_buy_flow.return_value = {
            'invoice_id': 'inv-abc-123',
            'status': 'delivered',
            'product_id': 'airtel-india',
            'amount_inr': 500.0,
            'recipient': '+919876543210',
            'orders': [],
        }
        agent.run_agent_loop(dry_run=False)
        captured = capsys.readouterr().out

        # Ensure one clear line per step with colored words
        assert '[1/4] Parse Intent:' in captured
        assert agent.STATUS_OK in captured
        assert '+91******3210' in captured  # Masked in intent
        assert '[2/4] Validation:' in captured
        assert '987654' not in captured  # Raw unmasked phone never present


