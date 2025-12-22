import logging
import sys

import sentry_sdk
from sentry_sdk import metrics  # Import the metrics module directly


logging.basicConfig(level=logging.INFO)
if len(sys.argv) > 0 and "bot_main.py" in sys.argv[0]:
    logger = logging.getLogger('aiogram')
else:
    logger = logging.getLogger('uvicorn')

logger.info(f'Logger in use: {logger.name}')

def _send_metric(key: str, value: float, tags: dict, metric_type: str = "increment", unit: str = "none"):
    """
    Internal wrapper for Sentry Metrics.
    Safe to call; catches errors so metrics don't crash the app.
    """
    try:
        # Ensure we don't send None tags
        clean_tags = {k: str(v) for k, v in tags.items() if v is not None}

        if metric_type == "count":
            # "incr" in other stats systems, "increment" in Sentry Python SDK
            metrics.count(key, value=value, attributes=clean_tags, unit=unit)

        elif metric_type == "distribution":
            metrics.distribution(key, value=value, attributes=clean_tags, unit=unit)

        elif metric_type == "gauge":
            metrics.gauge(key, value=value, attributes=clean_tags, unit=unit)

        logger.info(f'Sent metric: {key}: {value}')

    except Exception as e:
        logger.warning(f"Failed to send metric {key}: {e}")


def track_event(key: str, user_id: str, tags: dict = None):
    """
    Standard Counter wrapper.
    Usage: track_event("user_login", user.id, {"campaign": "ads_1"})
    """
    final_tags = tags or {}
    final_tags["user_id"] = str(user_id)
    # We default to 'increment' for simple event tracking
    _send_metric(key, 1.0, final_tags, metric_type="count")


def track_value(key: str, value: float, user_id: str, tags: dict = None, unit: str = "none"):
    """
    Distribution wrapper (e.g. for payments amounts or latency).
    Usage: track_value("payment_amount", 490.0, user.id, unit="rub")
    """
    final_tags = tags or {}
    final_tags["user_id"] = str(user_id)
    _send_metric(key, value, final_tags, metric_type="distribution", unit=unit)