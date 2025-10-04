from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware # Import this
from sqlmodel import SQLModel

from app.api.routes import router as chat_router
from app.db.database import engine
from app.db.models import User, Conversation, Message, MessageContent

app = FastAPI(
    title="Telegram ChatGPT Clone",
    version="0.1.0",
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# --- Add this section ---
origins = [
    "http://localhost:5173", # The default Vue dev server port
    "http://127.0.0.1:5173",
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


app.include_router(chat_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "Welcome to the ChatGPT API"}