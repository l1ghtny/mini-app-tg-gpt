import hmac
import hashlib
import json
from urllib.parse import unquote
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Add JWT imports and security helpers in a bit

router = APIRouter()

# You'd get this from your .env file
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"


class AuthRequest(BaseModel):
    initData: str


def validate_telegram_data(init_data: str) -> dict:
    """
    Validates the initData string from Telegram.
    """
    try:
        # Split and sort the data
        data_check_string = []
        received_hash = ''
        params = sorted([unquote(p) for p in init_data.split('&')])

        for param in params:
            key, value = param.split('=', 1)
            if key == 'hash':
                received_hash = value
            else:
                data_check_string.append(f"{key}={value}")

        # Create the secret key
        secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
        # Create the hash
        calculated_hash = hmac.new(secret_key, "\n".join(data_check_string).encode(), hashlib.sha256).hexdigest()

        # Compare hashes
        if calculated_hash != received_hash:
            raise HTTPException(status_code=403, detail="Invalid hash")

        # Optional: Check if data is outdated
        user_data = json.loads([p for p in data_check_string if p.startswith('user=')][0].split('=', 1)[1])
        if time.time() - int(user_data['auth_date']) > 3600: # 1 hour
            raise HTTPException(status_code=403, detail="Data is outdated")

        return {k: v for k, v in [p.split('=', 1) for p in data_check_string]}

    except Exception:
        raise HTTPException(status_code=400, detail="Invalid initData")


@router.post("/auth/telegram")
def authenticate_telegram_user(request: AuthRequest):
    # This is a simplified version. A full implementation would:
    # 1. Call validate_telegram_data
    # 2. Extract the user's Telegram ID
    # 3. Find or create a user in your database
    # 4. Create and return a JWT session token

    # For now, let's just validate and return a success message
    validated_data = validate_telegram_data(request.initData)
    return {"status": "ok", "user_data": validated_data}