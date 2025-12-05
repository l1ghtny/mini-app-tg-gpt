import hashlib
import httpx
from app.core.config import settings


class TBankService:
    def __init__(self):
        self.terminal_key = settings.TBANK_TERMINAL_KEY
        self.password = settings.TBANK_PASSWORD
        self.base_url = settings.TBANK_API_URL

    def _generate_token(self, params: dict) -> str:
        """
        Generates the TBank signature (Token).
        Rules:
        1. Exclude 'Token', 'DATA', 'Receipt'.
        2. Add 'Password'.
        3. Sort keys.
        4. Convert values to strings (Booleans must be lowercased!).
        5. Concatenate and Hash.
        """
        # 1. Filter params
        safe_params = {k: v for k, v in params.items() if k not in ["Token", "Receipt", "DATA"]}

        # 2. Add Password
        safe_params["Password"] = self.password

        # 3. Sort Keys
        sorted_keys = sorted(safe_params.keys())

        # 4. Concatenate Values
        values = []
        for k in sorted_keys:
            val = safe_params[k]

            # CRITICAL FIX: Handle Python Booleans
            if isinstance(val, bool):
                # TBank expects "true"/"false", Python gives "True"/"False"
                val = str(val).lower()
            else:
                val = str(val)

            values.append(val)

        sorted_values = "".join(values)

        # 5. SHA-256
        return hashlib.sha256(sorted_values.encode()).hexdigest()

    # NOTE: This is now a synchronous method (removed 'async')
    def verify_notification(self, data: dict) -> bool:
        received_token = data.get("Token")
        if not received_token:
            return False

        # We calculate what the token *should* be based on the data received
        calculated_token = self._generate_token(data)
        return received_token == calculated_token

    async def init_payment(self, order_id: str, amount_cents: int, description: str, user_id: str) -> tuple[str, str]:
        """
        Initializes payment and returns (PaymentURL, PaymentId)
        """
        payload = {
            "TerminalKey": self.terminal_key,
            "Amount": amount_cents,
            "OrderId": order_id,
            "Description": description,
            "DATA": {"user_id": user_id}
        }

        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/Init", json=payload)
            data = resp.json()

        if not data.get("Success", False):
            # TBank errors often come in 'Message' or 'Details'
            raise Exception(f"TBank Init Failed: {data.get('Message', 'Unknown error')} ({data.get('Details', '')})")

        return data.get("PaymentURL"), str(data.get("PaymentId"))


tbank_service = TBankService()