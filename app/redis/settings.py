from pydantic import Field, BaseModel


class Settings(BaseModel):
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    STREAM_TTL_SECONDS: int = 600          # 10 minutes max
    STREAM_MAXLEN: int = 5000              # trim per stream (approximate)
    COALESCE_MS: int = 40                  # group token bursts to cut the event rate
    CHECKPOINT_BYTES: int = 2048           # write checkpoint to DB every N bytes

settings = Settings()
