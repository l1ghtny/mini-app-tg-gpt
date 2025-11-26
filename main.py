from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware # Import this
from sqlmodel import SQLModel
import fastapi_swagger_dark as fsd

from app.api.access_codes import access_codes
from app.api.images import images
from app.api.routes import router as chat_router
from app.api.tiers import tiers
from app.api.user_subscription import user_subscription
from app.api.user_usage import user_usage
from app.db.database import engine
from app.api.auth import auth
from app.db.models import AppUser

app = FastAPI(
    title="Telegram ChatGPT API",
    version="0.5.0",
    docs_url=None
)

async def create_db_and_tables():
    # With async engine, use run_sync to execute metadata creation
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


origins = [
    "http://localhost:5172",
    "http://127.0.0.1:5172",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "https://gpt-mini-app.lightny.pro",
    "http://192.168.1.137:5173"
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# -------------------------

app.add_event_handler("startup", create_db_and_tables)

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