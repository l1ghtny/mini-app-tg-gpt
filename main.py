import sentry_sdk
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.openai import OpenAIIntegration
from sqlmodel import SQLModel
import fastapi_swagger_dark as fsd
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.metrics import metrics
from app.api.payments import payments
from app.api.access_codes import access_codes
from app.api.images import images
from app.api.routes import router as chat_router
from app.api.tiers import tiers
from app.api.user_subscription import user_subscription
from app.api.user_usage import user_usage
from app.core.config import settings
from app.db.database import engine
from app.api.auth import auth
from app.db.models import AppUser

logger = settings.custom_logger


def before_send(event, hint):
    # If the error is a known HTTP exception (like 401, 403, 404), ignore it
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, HTTPException):
            if exc_value.status_code < 500:
                return None  # Don't send to Sentry
    return event







app = FastAPI(
    title="Telegram ChatGPT API",
    version="0.9.2",
    docs_url=None
)


if settings.SENTRY_DSN:
    logger.info(f'Initializing Sentry in {settings.ENVIRONMENT}')
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=app.version,
        # Capture only 10% of transactions for performance monitoring
        traces_sample_rate=0.1 if settings.ENVIRONMENT == "production" or "production_main_server" else 1.0,
        # Capture 100% of errors (this is the default, but good to know)
        before_send=before_send, # filter non-500 http errors
        send_default_pii=True, # send info about http calls (includes AI, currently using for openAI costs)
        integrations=[
            OpenAIIntegration(
                include_prompts=False,
                # LLM/tokenizer inputs/outputs will be not sent to Sentry, despite send_default_pii=True
            )],
        enable_logs=True,
        _experiments={
            "metrics_aggregator": True,
        },
    )


# async def create_db_and_tables():
#     async with engine.begin() as conn:
#         await conn.run_sync(SQLModel.metadata.create_all)


origins = [
    "http://localhost:5172",
    "http://127.0.0.1:5172",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "https://gpt-mini-app.lightny.pro",
    "http://192.168.1.137:5173",
    "http://192.168.1.137:4173",
    "https://gpt-mini-app-ru.lightny.pro"
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["gpt-mini-app-api.lightny.pro", "*.lightny.pro", "localhost", "192.168.1.137"],
)


# -------------------------

dark = APIRouter()
fsd.install(dark, path="/docs")
app.include_router(dark)
app.include_router(chat_router, prefix="/api/v1", tags=['conversations'])
app.include_router(auth, prefix="/api/v1")
app.include_router(images, prefix="/api/v1")
app.include_router(user_usage, prefix="/api/v1")
app.include_router(user_subscription, prefix="/api/v1")
app.include_router(access_codes, prefix="/api/v1")
app.include_router(tiers, prefix="/api/v1")
app.include_router(payments, prefix="/api/v1")
app.include_router(metrics, prefix="/api/v1")