import asyncio
import logging
import sys
import os

# 1. Setup path to import 'app' modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.db.models import AppUser

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Bot
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    language_code = message.from_user.language_code  # e.g., 'en', 'ru', 'de'

    # 2. Extract Campaign (e.g., /start campaign_123)
    args = message.text.split()
    campaign_param = args[1] if len(args) > 1 else None

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
            logging.info(f"New user registered via Bot: {telegram_id} (Campaign: {campaign_param})")

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
            f"👋 **Привет, {message.from_user.first_name}!**\n\n"
            "Я твой ИИ-ассистент. Я помогу тебе писать тексты, анализировать изображения и решать сложные задачи с помощью GPT-5.\n\n"
            "👇 **Нажми на кнопку ниже, чтобы запустить приложение:**"
        )
        button_text = "🚀 Запустить AI"
    else:
        # Default to English for 'en' or any other unknown language
        welcome_text = (
            f"👋 **Hi {message.from_user.first_name}!**\n\n"
            "I am your AI Assistant. I can help you write text, analyze images, and solve problems using GPT-5.\n\n"
            "👇 **Tap the button below to launch the app:**"
        )
        button_text = "🚀 Launch AI App"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=button_text,
            web_app=WebAppInfo(url=webapp_url)
        )]
    ])

    await message.answer(welcome_text, reply_markup=keyboard, parse_mode="Markdown")


async def main():
    # Drop pending updates so the bot doesn't spam old messages on restart
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())