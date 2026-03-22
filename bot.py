"""
Traffic Bot — aiogram 3.x
"""
import asyncio
import math
import random
import logging
import os
from datetime import datetime, timedelta

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

TOKEN         = os.environ["TOKEN"]
PAYMENT_TOKEN = os.environ.get("PAYMENT_TOKEN", "")
SUPPORT_ID    = int(os.environ.get("SUPPORT_ID", "0"))
MINI_APP_URL  = os.environ.get("MINI_APP_URL", "")
DB_PATH       = "taxi.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot     = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
router  = Router()
dp.include_router(router)

class DriverReg(StatesGroup):
    waiting_car_info = State()

class PassengerOrder(StatesGroup):
    waiting_destination = State()

class Chat(StatesGroup):
    chatting = State()

class Support(StatesGroup):
    waiting_message = State() async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                role TEXT, car TEXT, color TEXT, number TEXT,
                lat REAL, lon REAL, online INTEGER DEFAULT 0,
                nickname TEXT, avatar TEXT, payment_type TEXT DEFAULT NULL,
                sub_active INTEGER DEFAULT 0, sub_expiry TEXT DEFAULT NULL,
                trial_expiry TEXT DEFAULT NULL, card TEXT DEFAULT NULL
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, driver_id INTEGER,
                status TEXT, price INTEGER DEFAULT 0
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS routes (
                order_id INTEGER, point_index INTEGER,
                lat REAL, lon REAL
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                driver_id INTEGER, user_id INTEGER,
                order_id INTEGER, rating INTEGER
            )""")
        await db.commit()

async def add_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (uid,))
        await db.commit()

async def set_role(uid, role):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        await db.commit()

async def save_driver(uid, car, color, number):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET car=?, color=?, number=? WHERE id=?", (car, color, number, uid))
        await db.commit()

async def set_online(uid, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET online=? WHERE id=?", (status, uid))
        await db.commit()

async def save_location(uid, lat, lon):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET lat=?, lon=? WHERE id=?", (lat, lon, uid))
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id=?", (uid,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_online_drivers():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE role='driver' AND online=1") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def create_order(uid, did, price):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders(user_id,driver_id,status,price) VALUES (?,?,?,?)",
            (uid, did, "pending", price))
        await db.commit()
        return cur.lastrowid

async def set_order_status(oid, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        await db.commit()

async def get_active_order_by_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE user_id=? AND status IN ('pending','accepted') ORDER BY id DESC LIMIT 1", (uid,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_active_order_by_driver(did):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE driver_id=? AND status='accepted' ORDER BY id DESC LIMIT 1", (did,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def save_route(oid, points):
    async with aiosqlite.connect(DB_PATH) as db:
        for idx, (lat, lon) in enumerate(points):
            await db.execute("INSERT INTO routes VALUES (?,?,?,?)", (oid, idx, lat, lon))
        await db.commit()

async def save_rating(driver_id, user_id, order_id, rating):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO ratings VALUES (?,?,?,?)", (driver_id, user_id, order_id, rating))
        await db.commit()

async def get_driver_rating(driver_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT AVG(rating) FROM ratings WHERE driver_id=?", (driver_id,)) as cur:
            row = await cur.fetchone()
            return round(row[0], 1) if row[0] else 5.0

async def activate_subscription(uid):
    expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET sub_active=1, sub_expiry=? WHERE id=?", (expiry, uid))
        await db.commit()

async def is_sub_active(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT sub_active, sub_expiry FROM users WHERE id=?", (uid,)) as cur:
            row = await cur.fetchone()
            if not row or not row[0] or not row[1]:
                return False
            return datetime.strptime(row[1], "%Y-%m-%d") >= datetime.now() def generate_nickname(role):
    return f"{'Водитель' if role=='driver' else 'Пассажир'}{random.randint(10,99)}"

def distance_km(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1-lat2)**2 + (lon1-lon2)**2) * 111

def route_distance(points):
    total = 0.0
    for i in range(len(points)-1):
        total += distance_km(*points[i], *points[i+1])
    return total

def calculate_price(points, tariff=100):
    return max(int(route_distance(points) * tariff), 200)

def calculate_eta(d_loc, p_loc, avg_speed=40):
    return max(int(distance_km(*d_loc, *p_loc) / avg_speed * 60), 1)

def calculate_trip_time(points, avg_speed=40):
    return max(int(route_distance(points) / avg_speed * 60), 1)

async def geocode(addr):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": addr, "format": "json"},
                headers={"User-Agent": "TrafficTaxiBot/1.0"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                if data:
                    return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.error(f"Geocode error: {e}")
    return None, None

async def find_nearest_driver(lat, lon):
    drivers = await get_online_drivers()
    best, dmin = None, 999999.0
    for d in drivers:
        if d["lat"] and d["lon"]:
            dist = distance_km(lat, lon, d["lat"], d["lon"])
            if dist < dmin:
                dmin, best = dist, d
    return best

def driver_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🟢 На линии"), KeyboardButton(text="🔴 Не на линии")],
        [KeyboardButton(text="📍 Геолокация", request_location=True)],
        [KeyboardButton(text="✍️ Ввести адрес")],
        [KeyboardButton(text="💬 Чат с пассажиром")],
        [KeyboardButton(text="📊 Мой профиль")],
    ], resize_keyboard=True)

def passenger_menu(with_miniapp=False):
    kb = [
        [KeyboardButton(text="🚕 Вызвать такси", request_location=True)],
        [KeyboardButton(text="💬 Чат с водителем")],
        [KeyboardButton(text="⭐ Оценить водителя")],
        [KeyboardButton(text="📊 Мой профиль")],
        [KeyboardButton(text="🛠 Поддержка")],
    ]
    if with_miniapp and MINI_APP_URL:
        kb.insert(0, [KeyboardButton(
            text="📱 Открыть приложение Traffic",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def payment_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💰 Оплата за поездку")],
        [KeyboardButton(text="🗓 Подписка")],
    ], resize_keyboard=True)

def role_menu():
    return ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="🧑 Пассажир"),
        KeyboardButton(text="🚖 Водитель"),
    ]], resize_keyboard=True)

def order_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{order_id}"),
    ]])

def rating_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{'⭐'*i}", callback_data=f"rate_{order_id}_{i}")
        for i in range(1, 6)
    ]]) @router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await add_user(msg.from_user.id)
    user = await get_user(msg.from_user.id)
    if user and user["role"]:
        kb = driver_menu() if user["role"] == "driver" else passenger_menu(with_miniapp=True)
        await msg.answer(f"👋 С возвращением, <b>{user['nickname']}</b> {user['avatar']}!", reply_markup=kb)
    else:
        await msg.answer("🚕 <b>Добро пожаловать в Traffic!</b>\n\nВыберите вашу роль:", reply_markup=role_menu())

@router.message(F.text == "🧑 Пассажир")
async def choose_passenger(msg: Message):
    uid = msg.from_user.id
    await set_role(uid, "passenger")
    nickname = generate_nickname("passenger")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET nickname=?, avatar=? WHERE id=?", (nickname, "🧑", uid))
        await db.commit()
    await msg.answer(f"✅ Никнейм: <b>{nickname}</b> 🧑\n\nВыберите способ оплаты:", reply_markup=payment_menu())

@router.message(F.text == "🚖 Водитель")
async def choose_driver(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    await set_role(uid, "driver")
    nickname = generate_nickname("driver")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET nickname=?, avatar=? WHERE id=?", (nickname, "🚖", uid))
        await db.commit()
    await state.set_state(DriverReg.waiting_car_info)
    await msg.answer(f"✅ Никнейм: <b>{nickname}</b> 🚖\n\nВведите данные через запятую:\n<b>Марка, Цвет, Номер</b>\n\nПример: <code>Toyota Camry, Белый, A123BC</code>", reply_markup=ReplyKeyboardRemove())

@router.message(DriverReg.waiting_car_info)
async def driver_car_info(msg: Message, state: FSMContext):
    parts = [x.strip() for x in msg.text.split(",")]
    if len(parts) != 3:
        await msg.answer("⚠️ Введите ровно 3 параметра:\n<code>Марка, Цвет, Номер</code>")
        return
    car, color, number = parts
    await save_driver(msg.from_user.id, car, color, number)
    await state.clear()
    await msg.answer(f"✅ Сохранено!\n🚗 {car} | 🎨 {color} | 🔢 {number}\n\nВыберите оплату:", reply_markup=payment_menu())

@router.message(F.text.in_(["💰 Оплата за поездку", "🗓 Подписка"]))
async def payment_choice(msg: Message):
    uid = msg.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        if msg.text == "💰 Оплата за поездку":
            await db.execute("UPDATE users SET payment_type='per_ride' WHERE id=?", (uid,))
            await db.commit()
            user = await get_user(uid)
            kb = driver_menu() if user["role"]=="driver" else passenger_menu(with_miniapp=True)
            await msg.answer("✅ Оплата за поездку выбрана!", reply_markup=kb)
        else:
            await db.execute("UPDATE users SET payment_type='subscription' WHERE id=?", (uid,))
            await db.commit()
            await activate_subscription(uid)
            user = await get_user(uid)
            kb = driver_menu() if user["role"]=="driver" else passenger_menu(with_miniapp=True)
            await msg.answer("✅ Подписка активирована на 30 дней! 🎉", reply_markup=kb)

@router.message(F.text == "🟢 На линии")
async def go_online(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] != "driver":
        await msg.answer("⚠️ Только для водителей.")
        return
    await set_online(msg.from_user.id, 1)
    await msg.answer("🟢 Вы вышли на линию!", reply_markup=driver_menu())

@router.message(F.text == "🔴 Не на линии")
async def go_offline(msg: Message):
    await set_online(msg.from_user.id, 0)
    await msg.answer("🔴 Вы сняты с линии.", reply_markup=driver_menu())

@router.message(F.location)
async def handle_location(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    lat, lon = msg.location.latitude, msg.location.longitude
    await save_location(uid, lat, lon)
    user = await get_user(uid)
    if not user:
        return
    if user["role"] == "driver":
        order = await get_active_order_by_driver(uid)
        if order:
            await bot.send_location(order["user_id"], lat, lon)
            await msg.answer("📍 Локация отправлена пассажиру.")
        else:
            await msg.answer("📍 Локация обновлена.")
    elif user["role"] == "passenger":
        await state.update_data(pickup_lat=lat, pickup_lon=lon)
        await state.set_state(PassengerOrder.waiting_destination)
        await msg.answer("📍 Геолокация получена!\n\nВведите адрес назначения:", reply_markup=ReplyKeyboardRemove()) @router.message(PassengerOrder.waiting_destination)
async def handle_destination(msg: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    pickup_lat = data.get("pickup_lat")
    pickup_lon = data.get("pickup_lon")
    if not pickup_lat:
        await msg.answer("⚠️ Сначала отправьте геолокацию.", reply_markup=passenger_menu(with_miniapp=True))
        return
    dest_text = msg.text.strip()
    await msg.answer(f"🔍 Ищу адрес «{dest_text}»...")
    dest_lat, dest_lon = await geocode(dest_text)
    if not dest_lat:
        await msg.answer("❌ Адрес не найден. Попробуйте точнее.", reply_markup=passenger_menu(with_miniapp=True))
        return
    points = [(pickup_lat, pickup_lon), (dest_lat, dest_lon)]
    price = calculate_price(points)
    trip_time = calculate_trip_time(points)
    driver = await find_nearest_driver(pickup_lat, pickup_lon)
    if not driver:
        await msg.answer("😔 Свободных водителей нет. Попробуйте позже.", reply_markup=passenger_menu(with_miniapp=True))
        return
    eta = calculate_eta((driver["lat"], driver["lon"]), (pickup_lat, pickup_lon))
    order_id = await create_order(msg.from_user.id, driver["id"], price)
    await save_route(order_id, points)
    user = await get_user(msg.from_user.id)
    driver_rating = await get_driver_rating(driver["id"])
    await msg.answer(
        f"🚕 <b>Заказ #{order_id} создан!</b>\n\n"
        f"📍 Куда: <b>{dest_text}</b>\n"
        f"💰 Стоимость: <b>{price}₸</b>\n"
        f"⏱ Время в пути: ~{trip_time} мин\n\n"
        f"🚗 <b>Водитель:</b> {driver['nickname']}\n"
        f"   {driver['car']} · {driver['color']} · {driver['number']}\n"
        f"⭐ Рейтинг: {driver_rating}\n"
        f"🕐 Прибудет через: ~{eta} мин",
        reply_markup=passenger_menu(with_miniapp=True))
    await bot.send_message(driver["id"],
        f"🔔 <b>Новый заказ #{order_id}!</b>\n\n"
        f"👤 Пассажир: {user['nickname']}\n"
        f"📍 Куда: <b>{dest_text}</b>\n"
        f"💰 Стоимость: <b>{price}₸</b>\n"
        f"⏱ Время поездки: ~{trip_time} мин",
        reply_markup=order_kb(order_id))

@router.callback_query(F.data.startswith("accept_"))
async def accept_order(cb: CallbackQuery):
    order_id = int(cb.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
            order = dict(await cur.fetchone())
    if order["status"] != "pending":
        await cb.answer("Заказ уже обработан.")
        return
    await set_order_status(order_id, "accepted")
    driver = await get_user(cb.from_user.id)
    await cb.message.edit_text(f"✅ Вы приняли заказ #{order_id}.")
    await cb.answer()
    await bot.send_message(order["user_id"],
        f"✅ <b>Водитель принял заказ!</b>\n\n"
        f"🚗 {driver['car']} · {driver['color']} · {driver['number']}\n"
        f"👤 {driver['nickname']}\n\nИспользуйте 💬 для связи.")

@router.callback_query(F.data.startswith("decline_"))
async def decline_order(cb: CallbackQuery):
    order_id = int(cb.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
            order = dict(await cur.fetchone())
    await set_order_status(order_id, "declined")
    await cb.message.edit_text(f"❌ Вы отклонили заказ #{order_id}.")
    await cb.answer()
    await bot.send_message(order["user_id"], "😔 Водитель отклонил заказ. Ищем другого...")

@router.message(F.text.in_(["💬 Чат с водителем", "💬 Чат с пассажиром"]))
async def start_chat(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    user = await get_user(uid)
    if user["role"] == "passenger":
        order = await get_active_order_by_user(uid)
        if not order:
            await msg.answer("❌ Нет активного заказа.")
            return
        partner_id = order["driver_id"]
    else:
        order = await get_active_order_by_driver(uid)
        if not order:
            await msg.answer("❌ Нет активного заказа.")
            return
        partner_id = order["user_id"]
    partner = await get_user(partner_id)
    await state.set_state(Chat.chatting)
    await state.update_data(partner_id=partner_id)
    await msg.answer(f"💬 Чат с {partner['nickname']} открыт.\nДля выхода — /stopchat", reply_markup=ReplyKeyboardRemove())

@router.message(Command("stopchat"))
async def stop_chat(msg: Message, state: FSMContext):
    await state.clear()
    user = await get_user(msg.from_user.id)
    kb = driver_menu() if user["role"]=="driver" else passenger_menu(with_miniapp=True)
    await msg.answer("💬 Чат закрыт.", reply_markup=kb)

@router.message(Chat.chatting)
async def relay_chat(msg: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = data.get("partner_id")
    user = await get_user(msg.from_user.id)
    try:
        await bot.send_message(partner_id, f"💬 <b>{user['nickname']}:</b> {msg.text}")
        await msg.answer("✉️ Доставлено")
    except Exception:
        await msg.answer("❌ Не удалось доставить сообщение.")

@router.message(F.text == "⭐ Оценить водителя")
async def rate_driver(msg: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT 1",
            (msg.from_user.id,)) as cur:
            order = await cur.fetchone()
    if not order:
        await msg.answer("❌ Нет завершённых поездок.")
        return
    order = dict(order)
    driver = await get_user(order["driver_id"])
    await msg.answer(f"⭐ Оцените водителя <b>{driver['nickname']}</b>:", reply_markup=rating_kb(order["id"]))

@router.callback_query(F.data.startswith("rate_"))
async def handle_rating(cb: CallbackQuery):
    parts = cb.data.split("_")
    order_id, rating = int(parts[1]), int(parts[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
            order = dict(await cur.fetchone())
    await save_rating(order["driver_id"], cb.from_user.id, order_id, rating)
    new_rating = await get_driver_rating(order["driver_id"])
    driver = await get_user(order["driver_id"])
    await cb.message.edit_text(f"{'⭐'*rating} Спасибо!\nРейтинг {driver['nickname']}: {new_rating}")
    await cb.answer()
    try:
        await bot.send_message(order["driver_id"], f"⭐ Новая оценка: {rating}/5\nВаш рейтинг: {new_rating}")
    except Exception:
        pass

@router.message(F.text == "📊 Мой профиль")
async def my_profile(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user:
        return
    sub = "✅ Активна" if await is_sub_active(msg.from_user.id) else "❌ Нет"
    text = (f"{user['avatar']} <b>{user['nickname']}</b>\n\n"
            f"💳 Оплата: {'Подписка' if user['payment_type']=='subscription' else 'За поездку'}\n"
            f"📅 Подписка: {sub}\n")
    if user["role"] == "driver":
        rating = await get_driver_rating(msg.from_user.id)
        text += (f"🚗 {user['car']} · {user['color']} · {user['number']}\n"
                f"⭐ Рейтинг: {rating}\n"
                f"🟢 Статус: {'На линии' if user['online'] else 'Офлайн'}\n")
    await msg.answer(text)

@router.message(F.text == "🛠 Поддержка")
async def support_start(msg: Message, state: FSMContext):
    await state.set_state(Support.waiting_message)
    await msg.answer("📝 Опишите проблему. /cancel для отмены:", reply_markup=ReplyKeyboardRemove())

@router.message(Command("cancel"))
async def cancel(msg: Message, state: FSMContext):
    await state.clear()
    user = await get_user(msg.from_user.id)
    kb = driver_menu() if user and user["role"]=="driver" else passenger_menu(with_miniapp=True)
    await msg.answer("Отменено.", reply_markup=kb)

@router.message(Support.waiting_message)
async def support_message(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    nick = user["nickname"] if user else str(msg.from_user.id)
    await state.clear()
    try:
        await bot.send_message(SUPPORT_ID,
            f"📨 <b>Обращение от {nick}</b> (ID: <code>{msg.from_user.id}</code>)\n\n{msg.text}")
        kb = driver_menu() if user and user["role"]=="driver" else passenger_menu(with_miniapp=True)
        await msg.answer("✅ Сообщение отправлено!", reply_markup=kb)
    except Exception:
        await msg.answer("❌ Ошибка. Попробуйте позже.")

@router.message(Command("complete"))
async def complete_trip(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] != "driver":
        await msg.answer("⚠️ Только для водителей.")
        return
    order = await get_active_order_by_driver(msg.from_user.id)
    if not order:
        await msg.answer("❌ Нет активной поездки.")
        return
    await set_order_status(order["id"], "completed")
    await msg.answer("✅ Поездка завершена!", reply_markup=driver_menu())
    await bot.send_message(order["user_id"],
        f"🏁 Поездка завершена!\nСтоимость: <b>{order['price']}₸</b>\n\nНажмите ⭐ чтобы оценить водителя.",
        reply_markup=passenger_menu(with_miniapp=True))

async def main():
    await init_db()
    log.info("🚀 Traffic Bot запущен!")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
