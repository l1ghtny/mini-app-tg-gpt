"""Register every SQLModel table used by standalone application processes."""

from app.db import models as _models  # noqa: F401
from app.db import subscription_tiers as _subscription_tiers  # noqa: F401
from app.db import work_agent_models as _work_agent_models  # noqa: F401
