from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware # Import this
from sqlmodel import SQLModel
import fastapi_swagger_dark as fsd

from app.api.images import images
from app.api.routes import router as chat_router
from app.db.database import engine
from app.api.auth import auth
from app.db.models import AppUser

app = FastAPI(
    title="Telegram ChatGPT API",
    version="0.5.0",
    docs_url=None
)


# def create_db_and_tables():
#     SQLModel.metadata.create_all(engine)

# --- Add this section ---
origins = [
    "http://localhost:5172",
    "http://127.0.0.1:5172",
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# -------------------------


# app.add_event_handler("startup", create_db_and_tables)

dark = APIRouter()
fsd.install(dark, path="/docs")
app.include_router(dark)
app.include_router(chat_router, prefix="/api/v1", tags=['conversations'])
app.include_router(auth, prefix="/api/v1")
app.include_router(images, prefix="/api/v1")