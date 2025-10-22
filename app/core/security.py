import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def validate_telegram_data(init_data: str, test_env: bool = False) -> dict:
    try:
        data_check_string = []
        received_hash = ''
        params = sorted([unquote(p) for p in init_data.split('&')])

        for param in params:
            key, value = param.split('=', 1)
            if key == 'hash':
                received_hash = value
            else:
                data_check_string.append(f"{key}={value}")

        secret_key = hmac.new("WebAppData".encode(), settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, "\n".join(data_check_string).encode(), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            raise ValueError("Invalid hash")

        user_data_str = [p for p in data_check_string if p.startswith('user=')][0].split('=', 1)[1]
        user_data = json.loads(user_data_str)
        return user_data
    except Exception as e:
        raise ValueError(f"Invalid initData: {e}")