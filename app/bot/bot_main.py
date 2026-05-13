import asyncio
import logging
import os
import sys

# 1. Setup path to import 'app' modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))


from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import sentry_sdk

from app.core.config import settings
from app.db.database import engine
from app.db.models import AppUser
from app.core.metrics import track_event
from main import app

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('aiogram')

# Initialize Bot
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

logger.info('starting...')
if settings.SENTRY_DSN:
    logger.info(f'Initializing Sentry in {settings.ENVIRONMENT}')
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=app.version,
        # Capture only 10% of transactions for performance monitoring
        traces_sample_rate=0.1 if settings.ENVIRONMENT in ("production", "production_main_server") else 1.0,
        # Capture 100% of errors (this is the default, but good to know)
        send_default_pii=True,  # send info about http calls (includes AI, currently using for openAI costs)
        enable_logs=True,
        _experiments={
            "metrics_aggregator": True,
        },
    )

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    language_code = message.from_user.language_code  # e.g., 'en', 'ru', 'de'

    track_event_send = False

    # 2. Extract Campaign (e.g., /start campaign_123)
    args = message.text.split('?start=')
    payload = args[1] if len(args) > 1 else None

    campaign_param = None

    if payload:
        # Simple parsing logic
        parts = payload.split('_')
        for part in parts:
            if part.startswith('cid-'):
                campaign_param = part.replace('cid-', '')

    # 3. DB Operation: Register/Update User Immediately
    async with AsyncSession(engine) as session:
        statement = select(AppUser).where(AppUser.telegram_id == telegram_id)
        result = await session.exec(statement)
        user = result.first()

        if not user:
            # NEW USER: Create with campaign
            user = AppUser(
                telegram_id=telegram_id,
                campaign=campaign_param
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logging.info(f"New user registered via Bot: {telegram_id} (Campaign: {campaign_param})")

            track_event_send = True

        elif campaign_param and not user.campaign:
            # EXISTING USER (First Touch Attribution): Update if they don't have a campaign yet
            user.campaign = campaign_param
            session.add(user)
            await session.commit()
            logging.info(f"Existing user attributed via Bot: {telegram_id} (Campaign: {campaign_param})")

    # 4. Prepare Launch URL
    # We pass ?startapp=... so the Mini App (Frontend) knows the campaign context
    startapp_param = f"?startapp={campaign_param}" if campaign_param else ""
    webapp_url = f"{settings.WEBAPP_URL}{startapp_param}"

    # 5. Localization Logic
    if language_code == 'ru':
        welcome_text = (
            f"👋 Добро пожаловать, {message.from_user.first_name}!\n\n"
            "Я — ваш доступ к мощнейшим нейросетям: GPT-5.5 и инструментам для генерации изображений.\n\n"
            "Никаких сложных текстовых команд. Вся работа происходит в красивом и удобном приложении прямо внутри Telegram.\n\n"
            "🎁 Вам уже начислены бесплатные запросы к нашим флагманским моделям, чтобы вы могли оценить их интеллект.\n\n"
            "Нажмите на кнопку «Открыть приложение» ниже (или на иконку меню слева от поля ввода), чтобы начать!"
        )
        welcome_text_no_name = (
            "👋 Добро пожаловать!\n\n"
            "Я — ваш доступ к мощнейшим нейросетям: GPT-5.5 и инструментам для генерации изображений.\n\n"
            "Никаких сложных текстовых команд. Вся работа происходит в красивом и удобном приложении прямо внутри Telegram.\n\n"
            "🎁 Вам уже начислены бесплатные запросы к нашим флагманским моделям, чтобы вы могли оценить их интеллект.\n\n"
            "Нажмите на кнопку «Открыть приложение» ниже (или на иконку меню слева от поля ввода), чтобы начать!"
        )
        button_text = "🚀 Запустить приложение"
    else:
        # Default to English for 'en' or any other unknown language
        welcome_text = (
            f"👋 Welcome, {message.from_user.first_name}!\n\n"
            "I’m your gateway to powerful AI models: GPT-5.5 and image generation tools.\n\n"
            "No complicated text commands. Everything works inside a beautiful and convenient app right in Telegram.\n\n"
            "🎁 You already have free requests to our flagship models, so you can evaluate their intelligence.\n\n"
            "Tap the “Open app” button below (or the menu icon to the left of the input field) to get started!"
        )
        welcome_text_no_name = (
            "👋 Welcome!\n\n"
            "I’m your gateway to powerful AI models: GPT-5.5 and image generation tools.\n\n"
            "No complicated text commands. Everything works inside a beautiful and convenient app right in Telegram.\n\n"
            "🎁 You already have free requests to our flagship models, so you can evaluate their intelligence.\n\n"
            "Tap the “Open app” button below (or the menu icon to the left of the input field) to get started!"
        )
        button_text = "🚀 Open app"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=button_text,
            web_app=WebAppInfo(url=webapp_url)
        )]
    ])
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

    if language_code == 'ru':
        text = (
            "🤖 **Я переехал в приложение!**\n\n"
            "Теперь я работаю только через наше удобное мини-приложение прямо внутри Telegram.\n\n"
            "Там тебя ждет история чатов, выбор моделей (GPT-5.5!) и мощная генерация изображений. "
            "Это гораздо удобнее и быстрее!\n\n"
            "Нажми кнопку ниже, чтобы продолжить общение там 👇"
        )
        button_text = "🚀 Открыть приложение"
    else:
        text = (
            "🤖 **I've moved to the App!**\n\n"
            "I'm now exclusively available through our convenient Mini App right inside Telegram.\n\n"
            "There you'll find chat history, model selection (GPT-5.5!), and powerful image generation. "
            "It's much smoother and more feature-rich!\n\n"
            "Tap the button below to continue our conversation there 👇"
        )
        button_text = "🚀 Open App"

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
