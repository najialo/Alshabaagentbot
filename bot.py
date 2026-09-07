import os
import json
import logging
import asyncio
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ✅ النموذج النهائي المحدث
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# ... باقي الكود كما هو بدون تغيير
