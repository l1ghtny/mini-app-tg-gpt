import asyncio
import logging
import os
import sys

# 1. Setup path to import 'app' modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import sentry_sdk
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.version import APP_VERSION
from app.db.database import engine
from app.db.models import AppUser
from app.core.metrics import track_event

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aiogram")

# Initialize Bot
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

logger.info('starting...')
if settings.SENTRY_DSN:
    logger.info(f'Initializing Sentry in {settings.ENVIRONMENT}')
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=APP_VERSION,
        # Capture only 10% of transactions for performance monitoring
        traces_sample_rate=0.1 if settings.ENVIRONMENT in ("production", "production_main_server") else 1.0,
        send_default_pii=True,
        enable_logs=True,
        _experiments={
            "metrics_aggregator": True,
        },
    )


def _start_welcome_copy(language_code: str | None, first_name: str | None, *, from_campaign: bool) -> tuple[str, str, str]:
    if language_code == "ru":
        intro_with_name = f"👋 Привет, {first_name}!"
        intro_no_name = "👋 Привет!"
        body = (
            "Это премиум-ИИ прямо в Telegram.\n\n"
            "Что внутри:\n"
            "• GPT + Gemini в одном месте\n"
            "• изображения и работа с файлами\n"
            "• оплата в рублях\n"
            "• без VPN и без отдельного приложения"
        )
        ad_line = (
            "\n\nЕсли вам важен не просто «бесплатный бот», а реально сильный результат и удобство, вы по адресу."
            if from_campaign
            else ""
        )
        trial_line = (
            "\n\nДля старта у вас уже есть бесплатные запросы — сможете сразу почувствовать разницу в качестве."
        )
        cta_line = "\n\n👇 Нажмите кнопку ниже, чтобы открыть приложение."
        text_with_name = f"{intro_with_name}\n\n{body}{ad_line}{trial_line}{cta_line}"
        text_no_name = f"{intro_no_name}\n\n{body}{ad_line}{trial_line}{cta_line}"
        return text_with_name, text_no_name, "🚀 Открыть приложение"

    intro_with_name = f"👋 Hi, {first_name}!"
    intro_no_name = "👋 Hi!"
    body = (
        "This is premium AI right inside Telegram.\n\n"
        "What you get:\n"
        "• GPT + Gemini in one place\n"
        "• images and file workflows\n"
        "• pay in rubles\n"
        "• no VPN and no extra app"
    )
    ad_line = (
        '\n\nIf you care about real output quality and convenience, not just "free and good enough", you\'re in the right place.'
        if from_campaign
        else ""
    )
    trial_line = (
        "\n\nYou already have free requests to get started, so you can feel the quality difference right away."
    )
    cta_line = "\n\n👇 Tap the button below to open the app."
    text_with_name = f"{intro_with_name}\n\n{body}{ad_line}{trial_line}{cta_line}"
    text_no_name = f"{intro_no_name}\n\n{body}{ad_line}{trial_line}{cta_line}"
    return text_with_name, text_no_name, "🚀 Open app"


def _nudge_copy(language_code: str | None) -> tuple[str, str]:
    if language_code == "ru":
        return (
            "🤖 **Лучший опыт — внутри приложения.**\n\n"
            "Там у вас будут:\n"
            "• GPT + Gemini в одном месте\n"
            "• изображения и файлы\n"
            "• история чатов и настройки\n"
            "• оплата в рублях без лишней боли\n\n"
            "Нажмите кнопку ниже и продолжим внутри Telegram 👇",
            "🚀 Открыть приложение",
        )

    return (
        "🤖 **The best experience is inside the app.**\n\n"
        "There you get:\n"
        "• GPT + Gemini in one place\n"
        "• images and file workflows\n"
        "• chat history and settings\n"
        "• pay in rubles with less friction\n\n"
        "Tap the button below and continue inside Telegram 👇",
        "🚀 Open app",
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    language_code = message.from_user.language_code

    track_event_send = False

    args = message.text.split("?start=")
    payload = args[1] if len(args) > 1 else None

    campaign_param = None
    if payload:
        parts = payload.split("_")
        for part in parts:
            if part.startswith("cid-"):
                campaign_param = part.replace("cid-", "")

    async with AsyncSession(engine) as session:
        statement = select(AppUser).where(AppUser.telegram_id == telegram_id)
        result = await session.exec(statement)
        user = result.first()

        if not user:
            user = AppUser(
                telegram_id=telegram_id,
                campaign=campaign_param,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logging.info(f"New user registered via Bot: {telegram_id} (Campaign: {campaign_param})")
            track_event_send = True
        elif campaign_param and not user.campaign:
            user.campaign = campaign_param
            session.add(user)
            await session.commit()
            logging.info(f"Existing user attributed via Bot: {telegram_id} (Campaign: {campaign_param})")

    startapp_param = f"?startapp={campaign_param}" if campaign_param else ""
    webapp_url = f"{settings.WEBAPP_URL}{startapp_param}"

    welcome_text, welcome_text_no_name, button_text = _start_welcome_copy(
        language_code,
        message.from_user.first_name,
        from_campaign=campaign_param is not None,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button_text,
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]
        ]
    )
    try:
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        await message.answer(welcome_text_no_name, reply_markup=keyboard, parse_mode="Markdown")

    if track_event_send:
        track_event("user_registered", str(user.id), {"campaign": campaign_param or "organic"})


@dp.message()
async def nudge_to_app(message: types.Message):
    """
    Catch-all handler to nudge users to use the Mini App instead of direct chat.
    """
    language_code = message.from_user.language_code
    webapp_url = settings.WEBAPP_URL
    text, button_text = _nudge_copy(language_code)

    is_private_chat = message.chat and message.chat.type == "private"
    open_app_button = InlineKeyboardButton(
        text=button_text,
        web_app=WebAppInfo(url=webapp_url) if is_private_chat else None,
        url=webapp_url if not is_private_chat else None,
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[open_app_button]])

    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


async def main():
    # Drop pending updates so the bot doesn't spam old messages on restart
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
