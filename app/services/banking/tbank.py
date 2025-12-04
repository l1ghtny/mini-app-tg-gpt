import hashlib
from typing import Any

import httpx
from app.core.config import settings


class TBankService:
    def __init__(self):
        self.terminal_key = settings.TBANK_TERMINAL_KEY
        self.password = settings.TBANK_PASSWORD
        self.base_url = settings.TBANK_API_URL

    def _generate_token(self, params: dict) -> str:
        """
        TBank Signature Generation:
        1. Add Password to params
        2. Sort keys alphabetically
        3. Concatenate values
        4. SHA-256 hash
        """
        # Exclude 'Token' itself and 'Receipt', 'DATA' if they exist (standard TBank exclusion rules)
        safe_params = {k: v for k, v in params.items() if k not in ["Token", "Receipt", "DATA"]}
        safe_params["Password"] = self.password

        sorted_values = "".join(str(safe_params[k]) for k in sorted(safe_params.keys()))
        return hashlib.sha256(sorted_values.encode()).hexdigest()

    async def init_payment(self, order_id: str, amount_cents: int, description: str, user_id: str) -> tuple[Any, Any]:
        """
        Initializes payment and returns the PaymentURL
        """
        payload = {
            "TerminalKey": self.terminal_key,
            "Amount": amount_cents,
            "OrderId": order_id,
            "Description": description,
            # Pass user_id in DATA to retrieve it easily in webhooks if needed
            "DATA": {"user_id": user_id}
        }

        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/Init", json=payload)
            data = resp.json()

        if not data.get("Success", False):
            raise Exception(f"TBank Init Failed: {data.get('Message', 'Unknown error')} ({data.get('Details', '')})")

        return data.get("PaymentURL"), data.get("PaymentId")

    async def verify_notification(self, data: dict) -> bool:
        """
        Validates the incoming webhook request from TBank.
        """
        received_token = data.get("Token")
        if not received_token:
            return False

        # TBank sends numbers/bools, but signature generation requires strings
        # We need to filter out the incoming Token to calculate our own version
        clean_data = {k: v for k, v in data.items() if k != "Token"}

        calculated_token = self._generate_token(clean_data)
        return received_token == calculated_token

tbank_service = TBankService()