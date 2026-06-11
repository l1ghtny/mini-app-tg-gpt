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
        1. Exclude 'Token' and any nested objects/arrays.
        2. Add 'Password'.
        3. Sort keys.
        4. Convert values to strings (Booleans must be lowercased!).
        5. Concatenate and Hash.
        """
        # Only root-level scalar fields participate in the signature.
        # T-Bank docs explicitly exclude nested objects like DATA/Receipt and
        # the SBP binding flow uses the camel-cased Data field.
        safe_params = {
            k: v
            for k, v in params.items()
            if k != "Token" and not isinstance(v, (dict, list, tuple))
        }

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

    async def _post(self, endpoint: str, payload: dict) -> dict:
        payload["Token"] = self._generate_token(payload)
        timeout = httpx.Timeout(
            timeout=settings.TBANK_TIMEOUT_SECONDS,
            connect=settings.TBANK_TIMEOUT_SECONDS,
            read=settings.TBANK_TIMEOUT_SECONDS,
            write=settings.TBANK_TIMEOUT_SECONDS,
            pool=settings.TBANK_TIMEOUT_SECONDS,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/{endpoint}", json=payload)

        try:
            data = resp.json()
        except Exception as exc:
            body_preview = (resp.text or "")[:400]
            raise Exception(
                f"TBank {endpoint} Invalid JSON: status={resp.status_code}, body={body_preview}"
            ) from exc

        if not data.get("Success", False):
            raise Exception(
                f"TBank {endpoint} Failed: status={resp.status_code}, "
                f"{data.get('Message')} {data.get('Details', '')}"
            )
        return data

    async def init_payment(
        self,
        order_id: str,
        amount_cents: int,
        description: str,
        user_id: str,
        recurrent: bool = False,
        receipt: dict = None,
        data: dict = None,
        operation_initiator_type: str | None = None,
    ) -> tuple[str, str]:
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

        if data:
            payload["DATA"].update(data)

        if recurrent:
            payload["Recurrent"] = "Y"
            payload["CustomerKey"] = user_id
        else:
            payload["Recurrent"] = "N"

        if operation_initiator_type:
            payload["OperationInitiatorType"] = operation_initiator_type

        if receipt:
            payload["Receipt"] = receipt

        payload["Token"] = self._generate_token(payload)

        timeout = httpx.Timeout(
            timeout=settings.TBANK_TIMEOUT_SECONDS,
            connect=settings.TBANK_TIMEOUT_SECONDS,
            read=settings.TBANK_TIMEOUT_SECONDS,
            write=settings.TBANK_TIMEOUT_SECONDS,
            pool=settings.TBANK_TIMEOUT_SECONDS,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self.base_url}/Init", json=payload)
        except httpx.TimeoutException as exc:
            raise Exception(
                f"TBank Init Timeout: {exc.__class__.__name__} "
                f"(order_id={order_id}, timeout={settings.TBANK_TIMEOUT_SECONDS}s)"
            ) from exc
        except httpx.RequestError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise Exception(
                f"TBank Init Request Error: {detail} (order_id={order_id})"
            ) from exc

        try:
            data = resp.json()
        except Exception as exc:
            body_preview = (resp.text or "")[:400]
            raise Exception(
                f"TBank Init Invalid JSON: status={resp.status_code}, body={body_preview}"
            ) from exc

        if not data.get("Success", False):
            # TBank errors often come in 'Message' or 'Details'
            message = data.get("Message", "Unknown error")
            details = data.get("Details", "")
            raise Exception(
                f"TBank Init Failed: status={resp.status_code}, message={message}, details={details}"
            )

        return data.get("PaymentURL"), str(data.get("PaymentId"))

    async def add_card(self, *, customer_key: str, check_type: str = "3DSHOLD", ip: str | None = None) -> dict:
        payload = {
            "TerminalKey": self.terminal_key,
            "CustomerKey": customer_key,
            "CheckType": check_type,
        }
        if ip:
            payload["IP"] = ip
        return await self._post("AddCard", payload)

    async def get_add_card_state(self, request_key: str) -> dict:
        payload = {
            "TerminalKey": self.terminal_key,
            "RequestKey": request_key,
        }
        return await self._post("GetAddCardState", payload)

    async def add_account_qr(
        self,
        *,
        description: str,
        data_type: str = "PAYLOAD",
        data: dict | None = None,
        bank_id: str | None = None,
    ) -> dict:
        payload = {
            "TerminalKey": self.terminal_key,
            "Description": description,
            "DataType": data_type,
        }
        if bank_id:
            payload["BankId"] = bank_id
        if data:
            payload["Data"] = data
        return await self._post("AddAccountQr", payload)

    async def get_add_account_qr_state(self, request_key: str) -> dict:
        payload = {
            "TerminalKey": self.terminal_key,
            "RequestKey": request_key,
        }
        return await self._post("GetAddAccountQrState", payload)

    async def get_card_list(self, user_id: str) -> list[dict]:
        """
        Fetches saved cards for a user to find the RebillId.
        Robustly handles API response (List or Dict).
        """
        payload = {
            "TerminalKey": self.terminal_key,
            "CustomerKey": user_id,
        }
        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/GetCardList", json=payload)
            try:
                data = resp.json()
            except Exception:
                print(f"TBank GetCardList Error: Non-JSON response. Body: {resp.text}")
                return []

        card_list = []

        # CASE 1: Direct List (What you are seeing)
        if isinstance(data, list):
            card_list = data

        # CASE 2: Wrapped Dictionary (What docs sometimes say)
        elif isinstance(data, dict):
            # Sometimes TBank returns {"Success": false, "Message": "..."} if no cards found
            if not data.get("Success", True) and "CardInfo" not in data:
                return []
            card_list = data.get("CardInfo", [])

        else:
            print(f"TBank GetCardList Error: Unexpected type {type(data)}. Data: {data}")
            return []

        # Filter active cards
        # We also check if 'c' is a dict to be safe against weird list contents
        active_cards = [
            c for c in card_list
            if isinstance(c, dict) and c.get("Status") == "A"
        ]

        return active_cards

    async def charge_qr(self, payment_id: str, account_token: str) -> bool:
        """
        Executes a recurring SBP charge using AccountToken.
        """
        payload = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
            "AccountToken": account_token,
        }
        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/ChargeQr", json=payload)
            data = resp.json()

        if not data.get("Success", False):
            raise Exception(f"TBank ChargeQr Failed: {data.get('Message')} {data.get('Details', '')}")

        return True

    async def charge(self, payment_id: str, rebill_id: str, payment_type: str = "card") -> bool:
        """
        Executes a recurring charge using a previous PaymentId (from Init) and RebillId (or AccountToken).
        """
        if payment_type == "sbp":
            return await self.charge_qr(payment_id, rebill_id)

        payload = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
            "RebillId": rebill_id,
        }
        payload["Token"] = self._generate_token(payload)

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/Charge", json=payload)
            data = resp.json()

        # If Success=True, the payment is processing (or done).
        # Webhook will confirm final status.
        if not data.get("Success", False):
            raise Exception(f"TBank Charge Failed: {data.get('Message')} {data.get('Details', '')}")

        return True

    async def cancel_payment(self, payment_id: str, amount: int | None = None) -> dict:
        payload = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
        }
        if amount is not None:
            payload["Amount"] = amount
        return await self._post("Cancel", payload)


tbank_service = TBankService()
