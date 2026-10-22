import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import aiosqlite
from dotenv import load_dotenv

# AI (ixtiyoriy)
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

load_dotenv()

# ==================== SOZLAMALAR ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # Kage Dub kanal ID
CHANNEL_USERNAME = "KageDub"  # @sizningkanal (tugma uchun)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_PATH = "kage_bot.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ==================== FSM HOLATLAR ====================
class PostBuilder(StatesGroup):
    waiting_media = State()
    waiting_text = State()
    waiting_buttons = State()
    confirm = State()

class AnimeAdd(StatesGroup):
    name = State()
    code = State()
    genre = State()
    description = State()
    link = State()
    is_vip = State()

class VIPManage(StatesGroup):
    set_price = State()
    add_admin_id = State()
    remove_admin_id = State()
    add_vip_id = State()
    remove_vip_id = State()

class SearchStates(StatesGroup):
    waiting_query = State()

class AIChat(StatesGroup):
    chatting = State()

# ==================== DATABASE ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                is_vip INTEGER DEFAULT 0,
                vip_until TEXT,
                joined_at TEXT,
                is_banned INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS animes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT UNIQUE,
                genre TEXT,
                description TEXT,
                link TEXT,
                is_vip INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                content TEXT,
                sent_at TEXT
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("vip_price", "50000")
        )
        await db.commit()

async def get_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def add_user(user_id: int, username: str = None, full_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, username, full_name, datetime.now().isoformat())
        )
        await db.commit()

async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def get_vip_price() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'vip_price'") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 50000

async def set_vip_price(price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('vip_price', ?)",
            (str(price),)
        )
        await db.commit()

async def set_vip(user_id: int, days: int = 30):
    until = (datetime.now() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_vip = 1, vip_until = ? WHERE user_id = ?",
            (until, user_id)
        )
        await db.commit()

async def remove_vip(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_vip = 0, vip_until = NULL WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def check_vip_valid(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user or not user["is_vip"]:
        return False
    if user["vip_until"]:
        until = datetime.fromisoformat(user["vip_until"])
        if until < datetime.now():
            await remove_vip(user_id)
            return False
    return True

# ==================== MAJBURIY OBUNA ====================
async def is_subscribed(user_id: int) -> bool:
    if CHANNEL_ID == 0:
        return True  # Agar kanal sozlanmagan bo'lsa o'tkazib yuboradi
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in [
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        ]
    except Exception as e:
        logger.error(f"Subscribe check error: {e}")
        return False

def subscribe_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Kage Dub kanaliga obuna bo‘lish", url=f"https://t.me/{CHANNEL_USERNAME}")
    builder.button(text="✅ Tekshirish", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()

# ==================== KLAVIATURALAR ====================
def main_menu(is_admin_user: bool = False, is_vip: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔍 Anime qidirish")
    builder.button(text="🎲 Tasodifiy anime")
    builder.button(text="🔥 Top animelar")
    builder.button(text="🆕 Eng yangi")
    builder.button(text="💎 VIP bo‘lim")
    builder.button(text="🤖 AI Rejim")
    if is_admin_user:
        builder.button(text="🛠 Admin panel")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def admin_menu(is_owner_user: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Post tayyorlash")
    builder.button(text="➕ Anime qo‘shish")
    builder.button(text="📋 Animelar ro‘yxati")
    builder.button(text="📢 Broadcast")
    if is_owner_user:
        builder.button(text="👑 Owner panel")
    builder.button(text="◀️ Orqaga")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def owner_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Admin qo‘shish")
    builder.button(text="➖ Admin olib tashlash")
    builder.button(text="💰 VIP narxini o‘zgartirish")
    builder.button(text="💎 VIP foydalanuvchi qo‘shish")
    builder.button(text="❌ VIP olib tashlash")
    builder.button(text="📊 Statistika")
    builder.button(text="◀️ Orqaga")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

# ==================== START VA MAJBURIY OBUNA ====================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    await add_user(user.id, user.username, user.full_name)

    if not await is_subscribed(user.id):
        await message.answer(
            "<b>👋 Xush kelibsiz!</b>\n\n"
            "Botdan foydalanish uchun avval <b>Kage Dub</b> kanaliga obuna bo‘ling.",
            reply_markup=subscribe_keyboard()
        )
        return

    is_adm = await is_admin(user.id)
    is_v = await check_vip_valid(user.id)
    await message.answer(
        f"<b>🎬 Kage Dub Botga xush kelibsiz!</b>\n\n"
        f"Salom, {user.full_name}!\n"
        f"{'💎 VIP foydalanuvchi' if is_v else 'Oddiy foydalanuvchi'}",
        reply_markup=main_menu(is_adm, is_v)
    )

@router.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery, state: FSMContext):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        is_adm = await is_admin(callback.from_user.id)
        is_v = await check_vip_valid(callback.from_user.id)
        await callback.message.answer(
            "✅ Obuna tasdiqlandi! Endi botdan to‘liq foydalanishingiz mumkin.",
            reply_markup=main_menu(is_adm, is_v)
        )
    else:
        await callback.answer("❌ Hali obuna bo‘lmadingiz!", show_alert=True)

async def require_sub(message: Message) -> bool:
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun kanalga obuna bo‘ling!",
            reply_markup=subscribe_keyboard()
        )
        return False
    return True

# ==================== FOYDALANUVCHI MENYUSI ====================
@router.message(F.text == "🔍 Anime qidirish")
async def search_start(message: Message, state: FSMContext):
    if not await require_sub(message):
        return
    await state.set_state(SearchStates.waiting_query)
    await message.answer(
        "🔍 Qidiruv: anime nomi, kodi yoki janrini yozing.\n"
        "Masalan: <code>Naruto</code>, <code>NRT</code>, <code>action</code>",
        reply_markup=cancel_kb()
    )

@router.message(SearchStates.waiting_query)
async def search_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        is_adm = await is_admin(message.from_user.id)
        await message.answer("Bekor qilindi.", reply_markup=main_menu(is_adm))
        return

    query = message.text.strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM animes 
               WHERE LOWER(name) LIKE ? OR LOWER(code) LIKE ? OR LOWER(genre) LIKE ?
               ORDER BY views DESC LIMIT 15""",
            (f"%{query}%", f"%{query}%", f"%{query}%")
        ) as cur:
            results = await cur.fetchall()

    if not results:
        await message.answer("😕 Hech narsa topilmadi. Boshqa so‘z bilan urinib ko‘ring.")
        return

    text = f"<b>🔍 Qidiruv natijalari:</b> ({len(results)} ta)\n\n"
    for a in results:
        vip_mark = "💎 " if a["is_vip"] else ""
        text += f"{vip_mark}<b>{a['name']}</b> [{a['code']}]\n"
        text += f"   Janr: {a['genre'] or 'Noma’lum'} | Ko‘rishlar: {a['views']}\n\n"

    await message.answer(text)
    builder = InlineKeyboardBuilder()
    for a in results[:8]:
        builder.button(text=f"{a['name'][:20]}", callback_data=f"anime_{a['id']}")
    builder.adjust(2)
    await message.answer("Tanlang:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("anime_"))
async def show_anime(callback: CallbackQuery):
    anime_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM animes WHERE id = ?", (anime_id,)) as cur:
            anime = await cur.fetchone()
        if anime:
            await db.execute("UPDATE animes SET views = views + 1 WHERE id = ?", (anime_id,))
            await db.commit()

    if not anime:
        await callback.answer("Anime topilmadi", show_alert=True)
        return

    is_v = await check_vip_valid(callback.from_user.id)
    if anime["is_vip"] and not is_v and not await is_admin(callback.from_user.id):
        await callback.answer("💎 Bu anime faqat VIP foydalanuvchilar uchun!", show_alert=True)
        return

    text = (
        f"<b>🎬 {anime['name']}</b>\n"
        f"Kod: <code>{anime['code']}</code>\n"
        f"Janr: {anime['genre'] or '—'}\n"
        f"Ko‘rishlar: {anime['views'] + 1}\n\n"
        f"{anime['description'] or ''}\n\n"
    )
    builder = InlineKeyboardBuilder()
    if anime["link"]:
        builder.button(text="📺 Ko‘rish", url=anime["link"])
        builder.button(text="📥 Yuklab olish", url=anime["link"])
    builder.button(text="◀️ Yopish", callback_data="close")
    builder.adjust(2)

    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()

@router.message(F.text == "🎲 Tasodifiy anime")
async def random_anime(message: Message):
    if not await require_sub(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM animes ORDER BY RANDOM() LIMIT 1"
        ) as cur:
            anime = await cur.fetchone()

    if not anime:
        await message.answer("Hali anime qo‘shilmagan.")
        return

    class FakeCallback:
        def __init__(self, msg, data):
            self.message = msg
            self.data = data
            self.from_user = msg.from_user
            self.answer = lambda *a, **k: asyncio.sleep(0)
    await show_anime(FakeCallback(message, f"anime_{anime['id']}"))

@router.message(F.text == "🔥 Top animelar")
async def top_animes(message: Message):
    if not await require_sub(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM animes ORDER BY views DESC LIMIT 10"
        ) as cur:
            results = await cur.fetchall()

    if not results:
        await message.answer("Hali ma’lumot yo‘q.")
        return

    text = "<b>🔥 Eng ko‘p ko‘rilgan animelar:</b>\n\n"
    for i, a in enumerate(results, 1):
        text += f"{i}. <b>{a['name']}</b> — {a['views']} ko‘rish\n"
    await message.answer(text)

@router.message(F.text == "🆕 Eng yangi")
async def newest_animes(message: Message):
    if not await require_sub(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM animes ORDER BY created_at DESC LIMIT 10"
        ) as cur:
            results = await cur.fetchall()

    if not results:
        await message.answer("Hali anime yo‘q.")
        return

    text = "<b>🆕 Eng yangi qo‘shilgan animelar:</b>\n\n"
    for a in results:
        text += f"• <b>{a['name']}</b> [{a['code']}]\n"
    await message.answer(text)

# ==================== VIP BO‘LIM ====================
@router.message(F.text == "💎 VIP bo‘lim")
async def vip_section(message: Message):
    if not await require_sub(message):
        return
    price = await get_vip_price()
    is_v = await check_vip_valid(message.from_user.id)
    user = await get_user(message.from_user.id)

    if is_v:
        until = user["vip_until"][:10] if user["vip_until"] else "—"
        text = (
            f"<b>💎 Siz VIP foydalanuvchisiz!</b>\n\n"
            f"Amal qilish muddati: <code>{until}</code>\n\n"
            f"VIP afzalliklari:\n"
            f"• Yopiq/maxsus animelar\n"
            f"• Oldindan yangi qismlar\n"
            f"• AI cheksiz foydalanish"
        )
    else:
        text = (
            f"<b>💎 VIP tarif</b>\n\n"
            f"Narxi: <b>{price:,} so‘m</b> / 30 kun\n\n"
            f"Nimalar bor:\n"
            f"• Maxsus va yopiq animelar\n"
            f"• Yangi qismlarga erta kirish\n"
            f"• AI rejimida ustuvorlik\n\n"
            f"To‘lov uchun admin bilan bog‘laning."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="📞 Admin bilan bog‘lanish", url=f"https://t.me/{CHANNEL_USERNAME}")
        await message.answer(text, reply_markup=builder.as_markup())
        return

    await message.answer(text)

# ==================== AI REJIM ====================
@router.message(F.text == "🤖 AI Rejim")
async def ai_start(message: Message, state: FSMContext):
    if not await require_sub(message):
        return
    await state.set_state(AIChat.chatting)
    await message.answer(
        "<b>🤖 AI Anime Yordamchisi</b>\n\n"
        "Menga anime haqida savol bering, tavsiya so‘rang yoki suhbatlashing.\n"
        "Chiqish uchun: <code>/stop</code> yoki «❌ Bekor qilish»",
        reply_markup=cancel_kb()
    )

@router.message(AIChat.chatting)
async def ai_chat(message: Message, state: FSMContext):
    if message.text in ["❌ Bekor qilish", "/stop"]:
        await state.clear()
        is_adm = await is_admin(message.from_user.id)
        await message.answer("AI rejim yopildi.", reply_markup=main_menu(is_adm))
        return

    prompt = (
        "Sen anime bo‘yicha mutaxassis yordamchisan. "
        "O‘zbek tilida qisqa, foydali va qiziqarli javob ber. "
        f"Foydalanuvchi savoli: {message.text}"
    )

    response_text = "⚠️ AI xizmati hozircha sozlanmagan."

    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600
            )
            response_text = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI error: {e}")

    elif GEMINI_AVAILABLE and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = await asyncio.to_thread(model.generate_content, prompt)
            response_text = resp.text
        except Exception as e:
            logger.error(f"Gemini error: {e}")

    await message.answer(response_text)

# ==================== ADMIN PANEL ====================
@router.message(F.text == "🛠 Admin panel")
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    is_own = await is_owner(message.from_user.id)
    await message.answer("🛠 <b>Admin panel</b>", reply_markup=admin_menu(is_own))

@router.message(F.text == "◀️ Orqaga")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    is_adm = await is_admin(message.from_user.id)
    await message.answer("Asosiy menyu", reply_markup=main_menu(is_adm))

# ----- Post Builder -----
@router.message(F.text == "📝 Post tayyorlash")
async def post_builder_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.set_state(PostBuilder.waiting_media)
    await message.answer(
        "📸 Rasm yoki video yuboring (yoki /skip deb o‘tkazib yuboring).",
        reply_markup=cancel_kb()
    )

@router.message(PostBuilder.waiting_media, F.photo | F.video)
async def post_media(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    else:
        file_id = message.video.file_id
        media_type = "video"
    await state.update_data(media_type=media_type, file_id=file_id)
    await state.set_state(PostBuilder.waiting_text)
    await message.answer("✍️ Endi post matnini yozing:")

@router.message(PostBuilder.waiting_media, F.text == "/skip")
async def post_skip_media(message: Message, state: FSMContext):
    await state.update_data(media_type=None, file_id=None)
    await state.set_state(PostBuilder.waiting_text)
    await message.answer("✍️ Post matnini yozing:")

@router.message(PostBuilder.waiting_text)
async def post_text(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu(await is_owner(message.from_user.id)))
        return
    await state.update_data(text=message.html_text or message.text)
    await state.set_state(PostBuilder.waiting_buttons)
    await message.answer(
        "🔘 Inline tugmalar qo‘shing.\n"
        "Format: <code>Tugma matni | https://url.com</code>\n"
        "Har birini yangi qatordan yozing.\n"
        "Tugma kerak emas bo‘lsa /skip yuboring."
    )

@router.message(PostBuilder.waiting_buttons)
async def post_buttons(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu(await is_owner(message.from_user.id)))
        return

    buttons = []
    if message.text != "/skip":
        for line in message.text.strip().split("\n"):
            if "|" in line:
                text, url = line.split("|", 1)
                buttons.append({"text": text.strip(), "url": url.strip()})

    await state.update_data(buttons=buttons)
    data = await state.get_data()

    kb = None
    if buttons:
        builder = InlineKeyboardBuilder()
        for b in buttons:
            builder.button(text=b["text"], url=b["url"])
        builder.adjust(2)
        kb = builder.as_markup()

    preview_text = data.get("text", "")
    if data.get("media_type") == "photo":
        await message.answer_photo(data["file_id"], caption=preview_text, reply_markup=kb)
    elif data.get("media_type") == "video":
        await message.answer_video(data["file_id"], caption=preview_text, reply_markup=kb)
    else:
        await message.answer(preview_text, reply_markup=kb)

    confirm_kb = InlineKeyboardBuilder()
    confirm_kb.button(text="📢 Kanalga joylash", callback_data="post_channel")
    confirm_kb.button(text="📨 Barcha obunachilarga", callback_data="post_broadcast")
    confirm_kb.button(text="❌ Bekor", callback_data="post_cancel")
    confirm_kb.adjust(1)
    await message.answer("Qayerga yuboramiz?", reply_markup=confirm_kb.as_markup())
    await state.set_state(PostBuilder.confirm)

@router.callback_query(PostBuilder.confirm, F.data == "post_channel")
async def post_to_channel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kb = None
    if data.get("buttons"):
        builder = InlineKeyboardBuilder()
        for b in data["buttons"]:
            builder.button(text=b["text"], url=b["url"])
        builder.adjust(2)
        kb = builder.as_markup()

    try:
        if CHANNEL_ID != 0:
            if data.get("media_type") == "photo":
                await bot.send_photo(CHANNEL_ID, data["file_id"], caption=data.get("text"), reply_markup=kb)
            elif data.get("media_type") == "video":
                await bot.send_video(CHANNEL_ID, data["file_id"], caption=data.get("text"), reply_markup=kb)
            else:
                await bot.send_message(CHANNEL_ID, data.get("text"), reply_markup=kb)
            await callback.message.answer("✅ Kanalga muvaffaqiyatli joylandi!")
        else:
            await callback.message.answer("⚠️ CHANNEL_ID sozlanmagan!")
    except Exception as e:
        await callback.message.answer(f"❌ Xato: {e}")
    await state.clear()
    await callback.answer()

@router.callback_query(PostBuilder.confirm, F.data == "post_broadcast")
async def post_broadcast(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kb = None
    if data.get("buttons"):
        builder = InlineKeyboardBuilder()
        for b in data["buttons"]:
            builder.button(text=b["text"], url=b["url"])
        builder.adjust(2)
        kb = builder.as_markup()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cur:
            users = await cur.fetchall()

    success = 0
    for (uid,) in users:
        try:
            if data.get("media_type") == "photo":
                await bot.send_photo(uid, data["file_id"], caption=data.get("text"), reply_markup=kb)
            elif data.get("media_type") == "video":
                await bot.send_video(uid, data["file_id"], caption=data.get("text"), reply_markup=kb)
            else:
                await bot.send_message(uid, data.get("text"), reply_markup=kb)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await callback.message.answer(f"✅ Broadcast tugadi. Yetkazildi: {success} ta foydalanuvchi")
    await state.clear()
    await callback.answer()

@router.callback_query(PostBuilder.confirm, F.data == "post_cancel")
async def post_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Bekor qilindi.")
    await callback.answer()

# ----- Anime qo‘shish -----
@router.message(F.text == "➕ Anime qo‘shish")
async def add_anime_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.set_state(AnimeAdd.name)
    await message.answer("Anime nomini yozing:", reply_markup=cancel_kb())

@router.message(AnimeAdd.name)
async def add_anime_name(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu(await is_owner(message.from_user.id)))
        return
    await state.update_data(name=message.text)
    await state.set_state(AnimeAdd.code)
    await message.answer("Kodini yozing (masalan: NRT001):")

@router.message(AnimeAdd.code)
async def add_anime_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.upper())
    await state.set_state(AnimeAdd.genre)
    await message.answer("Janr(lar)ini yozing (vergul bilan):")

@router.message(AnimeAdd.genre)
async def add_anime_genre(message: Message, state: FSMContext):
    await state.update_data(genre=message.text)
    await state.set_state(AnimeAdd.description)
    await message.answer("Qisqacha tavsif (yoki /skip):")

@router.message(AnimeAdd.description)
async def add_anime_desc(message: Message, state: FSMContext):
    desc = None if message.text == "/skip" else message.text
    await state.update_data(description=desc)
    await state.set_state(AnimeAdd.link)
    await message.answer("Havola (ko‘rish/yuklab olish) yoki /skip:")

@router.message(AnimeAdd.link)
async def add_anime_link(message: Message, state: FSMContext):
    link = None if message.text == "/skip" else message.text
    await state.update_data(link=link)
    await state.set_state(AnimeAdd.is_vip)
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 VIP", callback_data="anime_vip_1")
    kb.button(text="Ochiq", callback_data="anime_vip_0")
    await message.answer("VIP anime mi?", reply_markup=kb.as_markup())

@router.callback_query(AnimeAdd.is_vip, F.data.startswith("anime_vip_"))
async def add_anime_finish(callback: CallbackQuery, state: FSMContext):
    is_vip = int(callback.data.split("_")[-1])
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO animes (name, code, genre, description, link, is_vip, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (data["name"], data["code"], data["genre"], data.get("description"),
                 data.get("link"), is_vip, datetime.now().isoformat())
            )
            await db.commit()
            await callback.message.answer(f"✅ Anime qo‘shildi: <b>{data['name']}</b>")
        except aiosqlite.IntegrityError:
            await callback.message.answer("❌ Bu kod allaqachon mavjud!")
    await state.clear()
    await callback.answer()

@router.message(F.text == "📋 Animelar ro‘yxati")
async def list_animes(message: Message):
    if not await is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, code, is_vip, views FROM animes ORDER BY id DESC LIMIT 30") as cur:
            rows = await cur.fetchall()
    if not rows:
        await message.answer("Hali anime yo‘q.")
        return
    text = "<b>📋 Animelar:</b>\n\n"
    for r in rows:
        vip = "💎" if r["is_vip"] else ""
        text += f"{vip} <b>{r['name']}</b> [{r['code']}] — {r['views']} ko‘rish\n"
    await message.answer(text)

# ==================== OWNER PANEL ====================
@router.message(F.text == "👑 Owner panel")
async def owner_panel(message: Message):
    if not await is_owner(message.from_user.id):
        return
    await message.answer("👑 <b>Owner panel</b>", reply_markup=owner_menu())

@router.message(F.text == "➕ Admin qo‘shish")
async def add_admin_start(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        return
    await state.set_state(VIPManage.add_admin_id)
    await message.answer("Yangi adminning Telegram ID sini yuboring:", reply_markup=cancel_kb())

@router.message(VIPManage.add_admin_id)
async def add_admin_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=owner_menu())
        return
    try:
        new_admin_id = int(message.text.strip())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
                (new_admin_id, message.from_user.id, datetime.now().isoformat())
            )
            await db.commit()
        await message.answer(f"✅ ID {new_admin_id} adminlar safiga qo‘shildi!", reply_markup=owner_menu())
    except ValueError:
        await message.answer("❌ Faqat raqam (Telegram ID) kiriting!")
    await state.clear()

@router.message(F.text == "➖ Admin olib tashlash")
async def remove_admin_start(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        return
    await state.set_state(VIPManage.remove_admin_id)
    await message.answer("Olib tashlanadigan admin ID sini yuboring:", reply_markup=cancel_kb())

@router.message(VIPManage.remove_admin_id)
async def remove_admin_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=owner_menu())
        return
    try:
        admin_id = int(message.text.strip())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
            await db.commit()
        await message.answer(f"✅ ID {admin_id} adminlikdan olib tashlandi!", reply_markup=owner_menu())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!")
    await state.clear()

@router.message(F.text == "💰 VIP narxini o‘zgartirish")
async def set_price_start(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        return
    await state.set_state(VIPManage.set_price)
    price = await get_vip_price()
    await message.answer(f"Joriy narx: {price:,} so‘m\nYangi narxni yozing (faqat raqam):", reply_markup=cancel_kb())

@router.message(VIPManage.set_price)
async def set_price_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=owner_menu())
        return
    try:
        price = int(message.text.replace(" ", "").replace(",", ""))
        await set_vip_price(price)
        await message.answer(f"✅ VIP narxi {price:,} so‘m etib belgilandi!", reply_markup=owner_menu())
        await state.clear()
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!")

@router.message(F.text == "💎 VIP foydalanuvchi qo‘shish")
async def add_vip_start(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        return
    await state.set_state(VIPManage.add_vip_id)
    await message.answer("VIP bermoqchi bo‘lgan foydalanuvchi ID sini yuboring:", reply_markup=cancel_kb())

@router.message(VIPManage.add_vip_id)
async def add_vip_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=owner_menu())
        return
    try:
        uid = int(message.text.strip())
        await set_vip(uid, days=30)
        await message.answer(f"✅ ID {uid} ga 30 kunga VIP berildi!", reply_markup=owner_menu())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!")
    await state.clear()

@router.message(F.text == "❌ VIP olib tashlash")
async def remove_vip_start(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        return
    await state.set_state(VIPManage.remove_vip_id)
    await message.answer("VIP holatini olib tashlamoqchi bo‘lgan foydalanuvchi ID sini yuboring:", reply_markup=cancel_kb())

@router.message(VIPManage.remove_vip_id)
async def remove_vip_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=owner_menu())
        return
    try:
        uid = int(message.text.strip())
        await remove_vip(uid)
        await message.answer(f"✅ ID {uid} dan VIP olib tashlandi!", reply_markup=owner_menu())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!")
    await state.clear()

@router.message(F.text == "📊 Statistika")
async def stats_process(message: Message):
    if not await is_owner(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1") as cur:
            vip_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM animes") as cur:
            anime_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM admins") as cur:
            admin_count = (await cur.fetchone())[0]

    text = (
        f"<b>📊 Bot Statistikasi:</b>\n\n"
        f"👥 Foydalanuvchilar: {users_count} ta\n"
        f"💎 VIP foydalanuvchilar: {vip_count} ta\n"
        f"🎬 Animelar bazasi: {anime_count} ta\n"
        f"🛠 Adminlar soni: {admin_count + 1} ta (Owner bilan)"
    )
    await message.answer(text, reply_markup=owner_menu())

# ==================== MAIN ====================
async def main():
    await init_db()
    print("🚀 Kage Dub boti muvaffaqiyatli ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi!")
