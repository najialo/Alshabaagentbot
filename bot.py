import os
import json
import logging
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    DateTime,
    select,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base


# =========================
# CONFIG
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY غير موجود")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL غير موجود")

# SQLAlchemy async requires the asyncpg driver prefix.
# Supabase / Railway usually give you a plain postgresql:// URL,
# so we normalize it here automatically.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("alshahba")


# =========================
# DATABASE
# =========================

Base = declarative_base()

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True)

    property_code = Column(String(30), unique=True, nullable=False)

    status = Column(String(30), default="available")
    operation = Column(String(30))

    property_type = Column(String(100))

    city = Column(String(100))
    district = Column(String(150))
    neighborhood = Column(String(150))
    address = Column(Text)

    area_m2 = Column(Float)
    rooms = Column(Integer)
    bathrooms = Column(Integer)

    floor = Column(String(50))
    total_floors = Column(Integer)

    building_age = Column(Integer)

    furnished = Column(String(30))

    price = Column(Float)
    currency = Column(String(10))

    description = Column(Text)

    owner_name = Column(String(200))
    owner_phone = Column(String(100))

    source = Column(String(100))
    source_url = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


async def init_db():

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")


# =========================
# OPENAI
# =========================

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY
)


# =========================
# PROPERTY EXTRACTION
# =========================

EXTRACTION_PROMPT = """
أنت نظام استخراج بيانات عقارات لمكتب الشهباء العقاري.

مهمتك استخراج المعلومات الموجودة فعلياً في النص أو الصور.

ممنوع التخمين.

إذا لم تكن المعلومة موجودة أو واضحة:
اجعل قيمتها null.

إذا كان هناك تعارض بين النص والصورة:
ضع التعارض داخل conflicts.

أخرج JSON فقط.

المفاتيح المطلوبة:

{
  "operation": null,
  "property_type": null,
  "city": null,
  "district": null,
  "neighborhood": null,
  "address": null,
  "area_m2": null,
  "rooms": null,
  "bathrooms": null,
  "floor": null,
  "total_floors": null,
  "building_age": null,
  "furnished": null,
  "price": null,
  "currency": null,
  "description": null,
  "owner_name": null,
  "owner_phone": null,
  "conflicts": [],
  "missing_fields": []
}

السعر يجب أن يكون رقماً فقط.
المساحة يجب أن تكون رقماً بالمتر المربع.
عدد الغرف رقماً.
لا تخترع أي معلومة.
"""


async def extract_property(text, image_urls=None):

    content = [
        {
            "type": "input_text",
            "text": EXTRACTION_PROMPT + "\n\nالنص:\n" + (text or "")
        }
    ]

    if image_urls:
        for url in image_urls:
            content.append(
                {
                    "type": "input_image",
                    "image_url": url,
                }
            )

    response = await client.responses.create(
        model="gpt-5",
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )

    raw = response.output_text

    try:
        return json.loads(raw)
    except json.JSONDecodeError:

        logger.error("AI returned invalid JSON: %s", raw)

        return {
            "operation": None,
            "property_type": None,
            "city": None,
            "district": None,
            "neighborhood": None,
            "address": None,
            "area_m2": None,
            "rooms": None,
            "bathrooms": None,
            "floor": None,
            "total_floors": None,
            "building_age": None,
            "furnished": None,
            "price": None,
            "currency": None,
            "description": None,
            "owner_name": None,
            "owner_phone": None,
            "conflicts": ["تعذر تحليل البيانات"],
            "missing_fields": [],
        }


# =========================
# CODE GENERATOR
# =========================

async def generate_property_code(session):

    result = await session.execute(
        select(Property.id)
        .order_by(Property.id.desc())
        .limit(1)
    )

    last_id = result.scalar()

    number = (last_id or 0) + 1

    return f"SH-{number:04d}"


# =========================
# FORMAT PROPERTY
# =========================

def format_property(data):

    return f"""
🏠 نوع العقار: {data.get("property_type") or "غير محدد"}

📋 العملية: {data.get("operation") or "غير محدد"}

📍 المدينة: {data.get("city") or "غير محدد"}
📍 المنطقة: {data.get("district") or "غير محدد"}
📍 الحي: {data.get("neighborhood") or "غير محدد"}

📐 المساحة: {data.get("area_m2") or "غير محدد"} م²

🛏️ الغرف: {data.get("rooms") or "غير محدد"}

🚿 الحمامات: {data.get("bathrooms") or "غير محدد"}

🏢 الطابق: {data.get("floor") or "غير محدد"}

💰 السعر: {data.get("price") or "غير محدد"} {data.get("currency") or ""}

📝 الوصف:
{data.get("description") or "غير موجود"}
"""


# =========================
# COMMAND START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        """
🧠 أهلاً بك في مساعد مكتب الشهباء العقاري

أرسل لي تفاصيل العقار كنص أو صور أو الاثنين معاً.

مثال:

"شقة للبيع بالحمدانية
3 غرف
140 متر
الطابق الثالث
السعر 45 ألف دولار"

وسأستخرج المعلومات وأطلب منك تأكيدها قبل حفظها.

الأوامر:

/add - إضافة عقار
/search - البحث
/list - عرض العقارات
/stats - الإحصائيات
"""
    )


# =========================
# ADD PROPERTY
# =========================

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["adding_property"] = True
    context.user_data["searching"] = False
    context.user_data["pending_images"] = []

    await update.message.reply_text(
        "📥 أرسل الآن نص العقار أو صور العقار (يمكنك إرسال عدة صور، ثم اكتب انتهيت)."
    )


# =========================
# PROCESS EXTRACTED DATA (shared by text & photo flow)
# =========================

async def process_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE, text, image_urls=None):

    await update.message.reply_text(
        "🧠 جارٍ تحليل بيانات العقار..."
    )

    data = await extract_property(text, image_urls=image_urls)

    context.user_data["pending_property"] = data

    conflicts = data.get("conflicts") or []
    missing = data.get("missing_fields") or []

    message = format_property(data)

    if conflicts:

        message += "\n\n⚠️ تعارضات:\n"

        for conflict in conflicts:
            message += f"- {conflict}\n"

    if missing:

        message += "\n\n⚠️ معلومات ناقصة:\n"

        for field in missing:
            message += f"- {field}\n"

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ تأكيد وحفظ",
                callback_data="confirm_property",
            ),
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="cancel_property",
            ),
        ]
    ]

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================
# HANDLE PHOTOS
# =========================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.user_data.get("adding_property"):

        await update.message.reply_text(
            "أرسل /add أولاً لإضافة عقار."
        )

        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    pending_images = context.user_data.setdefault("pending_images", [])
    pending_images.append(file.file_path)

    caption = update.message.caption

    if caption:
        # Caption present: treat this as the final message, run extraction now
        await process_extraction(update, context, caption, image_urls=pending_images)
        context.user_data["pending_images"] = []
    else:
        await update.message.reply_text(
            f"📷 تم استلام الصورة ({len(pending_images)}). "
            "أرسل المزيد من الصور، أو اكتب النص/التفاصيل الآن لبدء التحليل."
        )


# =========================
# HANDLE TEXT (routes between add / search / idle)
# =========================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    if context.user_data.get("searching"):
        await search_text(update, context)
        return

    if context.user_data.get("adding_property"):

        pending_images = context.user_data.get("pending_images") or []

        await process_extraction(update, context, text, image_urls=pending_images or None)

        context.user_data["pending_images"] = []
        return

    await update.message.reply_text(
        "أرسل /add أولاً لإضافة عقار، أو /search للبحث."
    )


# =========================
# CONFIRM
# =========================

async def confirm_property(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = context.user_data.get("pending_property")

    if not data:

        await query.edit_message_text(
            "❌ لا يوجد عقار بانتظار التأكيد."
        )

        return

    if data.get("conflicts"):

        await query.edit_message_text(
            "⚠️ لا يمكن الحفظ بسبب وجود تعارض في المعلومات."
        )

        return

    async with SessionLocal() as session:

        code = await generate_property_code(session)

        property_obj = Property(
            property_code=code,
            status="available",
            operation=data.get("operation"),
            property_type=data.get("property_type"),
            city=data.get("city"),
            district=data.get("district"),
            neighborhood=data.get("neighborhood"),
            address=data.get("address"),
            area_m2=data.get("area_m2"),
            rooms=data.get("rooms"),
            bathrooms=data.get("bathrooms"),
            floor=data.get("floor"),
            total_floors=data.get("total_floors"),
            building_age=data.get("building_age"),
            furnished=data.get("furnished"),
            price=data.get("price"),
            currency=data.get("currency"),
            description=data.get("description"),
            owner_name=data.get("owner_name"),
            owner_phone=data.get("owner_phone"),
            source="telegram",
        )

        session.add(property_obj)

        await session.commit()

    context.user_data.pop("pending_property", None)
    context.user_data["adding_property"] = False
    context.user_data["pending_images"] = []

    await query.edit_message_text(
        f"""
✅ تم حفظ العقار بنجاح.

🆔 رقم العقار:
{code}

وضع العقار:
🟢 متاح
"""
    )


# =========================
# CANCEL
# =========================

async def cancel_property(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    context.user_data.pop("pending_property", None)
    context.user_data["adding_property"] = False
    context.user_data["pending_images"] = []

    await query.edit_message_text(
        "❌ تم إلغاء إضافة العقار."
    )


# =========================
# SEARCH
# =========================

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["searching"] = True
    context.user_data["adding_property"] = False

    await update.message.reply_text(
        """
🔎 اكتب طلب البحث بشكل طبيعي.

مثال:

بدي شقة بالحمدانية أقل من 50 ألف دولار 3 غرف
"""
    )


async def perform_search(query_text):

    prompt = f"""
حوّل طلب البحث التالي إلى JSON.

لا تخترع شروطاً غير موجودة.

الحقول:

property_type
city
district
neighborhood
min_price
max_price
currency
rooms
min_area
max_area

طلب العميل:
{query_text}

أخرج JSON فقط.
"""

    response = await client.responses.create(
        model="gpt-5",
        input=prompt,
    )

    try:
        return json.loads(response.output_text)

    except Exception:

        return {}


async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query_text = update.message.text

    criteria = await perform_search(query_text)

    async with SessionLocal() as session:

        stmt = select(Property).where(
            Property.status == "available"
        )

        if criteria.get("property_type"):
            stmt = stmt.where(
                Property.property_type.ilike(
                    f"%{criteria['property_type']}%"
                )
            )

        if criteria.get("city"):
            stmt = stmt.where(
                Property.city.ilike(
                    f"%{criteria['city']}%"
                )
            )

        if criteria.get("district"):
            stmt = stmt.where(
                Property.district.ilike(
                    f"%{criteria['district']}%"
                )
            )

        if criteria.get("rooms"):
            stmt = stmt.where(
                Property.rooms == criteria["rooms"]
            )

        if criteria.get("min_price") is not None:
            stmt = stmt.where(
                Property.price >= criteria["min_price"]
            )

        if criteria.get("max_price") is not None:
            stmt = stmt.where(
                Property.price <= criteria["max_price"]
            )

        result = await session.execute(stmt)

        properties = result.scalars().all()

    context.user_data["searching"] = False

    if not properties:

        await update.message.reply_text(
            "❌ لم أجد عقارات مطابقة لطلبك."
        )

        return

    text = f"🔎 وجدت {len(properties)} عقار:\n\n"

    for p in properties[:20]:

        text += f"""
🆔 {p.property_code}
🏠 {p.property_type or "-"}
📍 {p.city or "-"} - {p.district or "-"}
📐 {p.area_m2 or "-"} م²
🛏️ {p.rooms or "-"} غرف
💰 {p.price or "-"} {p.currency or ""}

----------------
"""

    await update.message.reply_text(text)


# =========================
# LIST
# =========================

async def list_properties(update: Update, context: ContextTypes.DEFAULT_TYPE):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Property)
            .where(Property.status == "available")
            .order_by(Property.id.desc())
            .limit(20)
        )

        properties = result.scalars().all()

    if not properties:

        await update.message.reply_text(
            "لا توجد عقارات مسجلة."
        )

        return

    text = "🏠 آخر العقارات:\n\n"

    for p in properties:

        text += f"""
🆔 {p.property_code}
📍 {p.city or "-"} - {p.district or "-"}
🏠 {p.property_type or "-"}
💰 {p.price or "-"} {p.currency or ""}
"""

    await update.message.reply_text(text)


# =========================
# STATS
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Property)
        )

        properties = result.scalars().all()

    total = len(properties)

    available = len(
        [p for p in properties if p.status == "available"]
    )

    sold = len(
        [p for p in properties if p.status == "sold"]
    )

    await update.message.reply_text(
        f"""
📊 إحصائيات مكتب الشهباء

🏠 إجمالي العقارات: {total}

🟢 المتاحة: {available}

🔴 المباعة: {sold}
"""
    )


# =========================
# MAIN
# =========================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("add", add_command)
    )

    application.add_handler(
        CommandHandler("search", search_command)
    )

    application.add_handler(
        CommandHandler("list", list_properties)
    )

    application.add_handler(
        CommandHandler("stats", stats)
    )

    application.add_handler(
        CallbackQueryHandler(
            confirm_property,
            pattern="^confirm_property$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_property,
            pattern="^cancel_property$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    async def startup(app):

        await init_db()

    application.post_init = startup

    logger.info("Alshahba AI started")

    application.run_polling()


if __name__ == "__main__":
    main()
