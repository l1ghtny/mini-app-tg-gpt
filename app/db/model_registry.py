"""Register every SQLModel table used by standalone application processes."""

from app.db import models as _models  # noqa: F401
from app.db import subscription_tiers as _subscription_tiers  # noqa: F401
