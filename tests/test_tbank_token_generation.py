import hashlib

from app.services.banking.tbank import TBankService


def test_generate_token_ignores_nested_add_account_qr_data():
    service = TBankService()
    service.password = "secret"

    payload = {
        "TerminalKey": "TERM123",
        "Description": "Bind account",
        "DataType": "PAYLOAD",
        "Data": {
            "user_id": "u-1",
            "tier_name": "Advanced",
        },
    }

    token = service._generate_token(payload)

    expected_source = "PAYLOAD" + "Bind account" + "secret" + "TERM123"
    expected_token = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()

    assert token == expected_token


def test_generate_token_ignores_nested_init_fields():
    service = TBankService()
    service.password = "secret"

    payload = {
        "TerminalKey": "TERM123",
        "Amount": 10000,
        "OrderId": "order-1",
        "Description": "Subscription",
        "DATA": {"QR": "true"},
        "Receipt": {"Email": "user@example.com"},
    }

    token = service._generate_token(payload)

    expected_source = "10000" + "Subscription" + "order-1" + "secret" + "TERM123"
    expected_token = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()

    assert token == expected_token
