import asyncio
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Message


DB_PATH = BASE_DIR / "shop.sqlite3"
MAX_CLIENT_ORDER_ITEMS = 7
LIQUID_DISCOUNT_MIN_QTY = 3
LIQUID_DISCUSS_MIN_QTY = 6
LIQUID_DISCOUNT_PRICE = Decimal("40")
CATEGORIES = {
    "liquids": "Płyny",
    "disposables": "Jednorazówki",
    "accessories": "Akcesoria",
}
ACCESSORY_TYPES = {
    "pods": "Kartridże / Pody",
    "devices": "Urządzenia",
    "other": "Inne",
}
BOT_NAME = "DuckVape_bot"
BOT_SHORT_DESCRIPTION = "Rezerwacja liquidów i produktów dostępnych od ręki."
BOT_DESCRIPTION = (
    "Witamy w DuckVape!\n\n"
    "Tutaj szybko i wygodnie zarezerwujesz liquidy oraz inne produkty dostępne od ręki.\n\n"
    "Wybierz coś dla siebie i zostaw rezerwację.\n\n"
    "Udanych zakupów!"
)


def load_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def money(value: int | float | Decimal) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))} zł"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(db()) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS warehouse_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                accessory_type TEXT NOT NULL DEFAULT '',
                flavor TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT NOT NULL DEFAULT '',
                volume TEXT NOT NULL DEFAULT '',
                resistance TEXT NOT NULL DEFAULT '',
                compatibility TEXT NOT NULL DEFAULT '',
                puffs TEXT NOT NULL DEFAULT '',
                strength TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS client_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_product_id INTEGER,
                name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                accessory_type TEXT NOT NULL DEFAULT '',
                flavor TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT NOT NULL DEFAULT '',
                volume TEXT NOT NULL DEFAULT '',
                resistance TEXT NOT NULL DEFAULT '',
                compatibility TEXT NOT NULL DEFAULT '',
                puffs TEXT NOT NULL DEFAULT '',
                strength TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (warehouse_product_id) REFERENCES warehouse_products(id)
            );

            CREATE TABLE IF NOT EXISTS carts (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (user_id, product_id),
                FOREIGN KEY (product_id) REFERENCES client_products(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                total REAL NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                is_custom INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                issued_at TEXT
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                warehouse_product_id INTEGER,
                name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                flavor TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                strength TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                order_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            );
            """
        )
        migrate_db(conn)
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "warehouse_products", "description", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "photo_file_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "accessory_type", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "volume", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "resistance", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "compatibility", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "warehouse_products", "puffs", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "description", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "photo_file_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "accessory_type", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "volume", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "resistance", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "compatibility", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "client_products", "puffs", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "order_items", "description", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "orders", "notes", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "orders", "is_custom", "INTEGER NOT NULL DEFAULT 0")


def set_product_activity(conn: sqlite3.Connection, table: str, product_id: int) -> None:
    conn.execute(
        f"UPDATE {table} SET is_active = CASE WHEN quantity > 0 THEN is_active ELSE 0 END WHERE id = ?",
        (product_id,),
    )


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def order_admin_keyboard(order_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Oznacz jako wydane", callback_data=f"admin:issue:{order_id}"),
                InlineKeyboardButton(text="Anuluj", callback_data=f"admin:cancel_order:{order_id}"),
            ],
            [InlineKeyboardButton(text="Zmień cenę", callback_data=f"admin:price:{order_id}")],
            [InlineKeyboardButton(text="Otwórz klienta", url=f"tg://user?id={user_id}")],
        ]
    )


def contact_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Napisz", url=f"tg://user?id={admin_id}")],
            [InlineKeyboardButton(text="Wróć", callback_data="menu")],
        ]
    )


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [("Dostępność", "catalog")],
        [("Koszyk", "cart"), ("Kontakt", "contact")],
    ]
    if is_admin:
        rows.append([("Panel administratora", "admin")])
    return kb(rows)


def catalog_menu() -> InlineKeyboardMarkup:
    return kb(
        [
            [("Płyny", "cat:liquids")],
            [("Jednorazówki", "cat:disposables")],
            [("Akcesoria", "cat:accessories")],
            [("Wróć", "menu")],
        ]
    )


def accessory_menu() -> InlineKeyboardMarkup:
    return kb(
        [
            [("Kartridże / Pody", "acc:pods")],
            [("Urządzenia", "acc:devices")],
            [("Inne", "acc:other")],
            [("Wróć", "catalog")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return kb(
        [
            [("Dodaj produkt", "admin:add")],
            [("Dodaj zdjęcie", "admin:photo")],
            [("Lista produktów", "admin:list")],
            [("Zamówienia", "admin:orders")],
            [("Zamówienie niestandardowe", "admin:custom")],
            [("Zmień ilość", "admin:stock")],
            [("Zmień dostępność", "admin:availability")],
            [("Usuń produkt", "admin:delete")],
            [("Statystyka", "admin:stats")],
            [("Wróć", "menu")],
        ]
    )


def product_display_name(product: sqlite3.Row | dict) -> str:
    if product["category"] == "accessories" and product["accessory_type"] == "pods":
        details = " / ".join(
            part
            for part in [format_volume(product["volume"]), format_resistance(product["resistance"])]
            if part
        )
        if details:
            return f"{product['name']} — {details}"
    if product["category"] == "disposables" and product["flavor"]:
        return f"{product['brand']} — {product['flavor']}" if product["brand"] else product["name"]
    return product["name"]


def user_link(user_id: int, label: str) -> str:
    safe_label = label.replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{safe_label}</a>'


def format_volume(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return text if "ml" in text.lower() else f"{text} ml"


def format_resistance(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return text if "Ω" in text or "ohm" in text.lower() else f"{text} Ω"


def welcome_text() -> str:
    return (
        "<b>Witamy w DuckVape!</b>\n\n"
        "Tutaj szybko i wygodnie zarezerwujesz liquidy oraz inne produkty dostępne od ręki.\n\n"
        "Wybierz coś dla siebie i zostaw rezerwację.\n\n"
        "<i>Udanych zakupów!</i>"
    )


def product_text(product: sqlite3.Row) -> str:
    if product["category"] == "disposables":
        return "\n".join(
            [
                f"<b>{product_display_name(product)}</b>",
                f"Marka: {product['brand'] or '-'}",
                f"Kategoria: {CATEGORIES.get(product['category'], product['category'])}",
                "",
                f"Smak: {product['flavor'] or '-'}",
                f"Ilość zaciągnięć: {product['puffs'] or '-'}",
                f"Moc: {product['strength'] or '-'}",
                f"Cena: {money(product['price'])}",
                f"Ilość: {product['quantity']}",
            ]
        )

    if product["category"] == "accessories" and product["accessory_type"] == "pods":
        parts = [
            f"<b>{product_display_name(product)}</b>",
            "",
            f"Kartridż do urządzeń kompatybilnych z systemem {product['name']}.",
            f"Pojemność: {format_volume(product['volume']) or '-'}",
            f"Oporność: {format_resistance(product['resistance']) or '-'}",
            "",
            f"Cena: {money(product['price'])}",
            f"Dostępne: {product['quantity']} szt.",
        ]
        return "\n".join(parts)

    parts = [
        f"<b>{product['name']}</b>",
        f"Marka: {product['brand'] or '-'}",
        f"Kategoria: {CATEGORIES.get(product['category'], product['category'])}",
        "",
        f"Opis: {product['description'] or product['flavor'] or '-'}",
        f"Moc: {product['strength'] or '-'}",
        f"Cena: {money(product['price'])}",
        f"Ilość: {product['quantity']}",
    ]
    return "\n".join(parts)


def liquid_count(items: list[sqlite3.Row]) -> int:
    return sum(item["cart_quantity"] for item in items if item["category"] == "liquids")


def cart_quantity(items: list[sqlite3.Row]) -> int:
    return sum(item["cart_quantity"] for item in items)


def effective_unit_price(item: sqlite3.Row, liquids_qty: int) -> Decimal:
    if item["category"] == "liquids" and liquids_qty >= LIQUID_DISCOUNT_MIN_QTY:
        return LIQUID_DISCOUNT_PRICE
    return Decimal(str(item["price"]))


def cart_total(items: list[sqlite3.Row]) -> Decimal:
    liquids_qty = liquid_count(items)
    total = Decimal("0")
    for item in items:
        total += effective_unit_price(item, liquids_qty) * item["cart_quantity"]
    return total


def pricing_note(items: list[sqlite3.Row]) -> str:
    liquids_qty = liquid_count(items)
    if liquids_qty >= LIQUID_DISCUSS_MIN_QTY:
        return "Przy 6+ płynach finalna cena jest do ustalenia z administratorem."
    if liquids_qty >= LIQUID_DISCOUNT_MIN_QTY:
        return "Rabat: przy 3+ płynach cena płynu wynosi 40 zł."
    return "Cena płynu: 45 zł. Przy 3+ płynach cena spada do 40 zł."


def cart_item_text(index: int, item: sqlite3.Row, unit_price: Decimal) -> str:
    quantity = item["cart_quantity"]
    if item["category"] == "liquids":
        return (
            f"{index}. {item['name']} | {item['strength'] or '-'} | "
            f"x{quantity} - {money(unit_price)}"
        )
    if item["category"] == "accessories" and item["accessory_type"] == "pods":
        return (
            f"{index}. {item['name']} | {format_volume(item['volume']) or '-'} | "
            f"{format_resistance(item['resistance']) or '-'} | "
            f"x{quantity} - {money(unit_price)}"
        )
    return f"{index}. {product_display_name(item)} | x{quantity} - {money(unit_price)}"


def admin_product_list_line(product: sqlite3.Row) -> str:
    status = "aktywne" if product["is_active"] else "ukryte"
    if product["category"] == "accessories" and product["accessory_type"] == "pods":
        detail = f"{format_volume(product['volume']) or '-'} / {format_resistance(product['resistance']) or '-'}"
        return (
            f"#{product['id']} {product['name']} | Kartridże / Pody | {detail} | "
            f"{money(product['price'])} | {product['quantity']} szt. | {status}"
        )
    if product["category"] == "disposables":
        return (
            f"#{product['id']} {product_display_name(product)} | Jednorazówki | "
            f"{product['puffs'] or '-'} zaciągnięć | {product['strength'] or '-'} | "
            f"{money(product['price'])} | {product['quantity']} szt. | {status}"
        )
    if product["category"] == "liquids":
        return (
            f"#{product['id']} {product['name']} | Płyny | {product['strength'] or '-'} | "
            f"{money(product['price'])} | {product['quantity']} szt. | {status}"
        )
    return (
        f"#{product['id']} {product_display_name(product)} | "
        f"{CATEGORIES.get(product['category'], product['category'])} | "
        f"{money(product['price'])} | {product['quantity']} szt. | {status}"
    )


def cart_text(items: list[sqlite3.Row]) -> str:
    if not items:
        return "Koszyk jest pusty."

    liquids_qty = liquid_count(items)
    lines = ["<b>Koszyk</b>"]
    for index, item in enumerate(items, start=1):
        unit_price = effective_unit_price(item, liquids_qty)
        lines.append(cart_item_text(index, item, unit_price))
    lines.append(f"\nRazem: <b>{money(cart_total(items))}</b>")
    lines.append(pricing_note(items))
    if cart_quantity(items) > MAX_CLIENT_ORDER_ITEMS:
        lines.append(f"Limit zwykłego zamówienia: {MAX_CLIENT_ORDER_ITEMS} szt. Większe zamówienie tworzy administrator.")
    return "\n".join(lines)


def get_cart_items(user_id: int) -> list[sqlite3.Row]:
    with closing(db()) as conn:
        return conn.execute(
            """
            SELECT p.*, c.quantity AS cart_quantity
            FROM carts c
            JOIN client_products p ON p.id = c.product_id
            WHERE c.user_id = ?
            ORDER BY p.category, p.name
            """,
            (user_id,),
        ).fetchall()


class AddProduct(StatesGroup):
    product_type = State()
    accessory_type = State()
    name = State()
    brand = State()
    flavor = State()
    puffs = State()
    strength = State()
    volume = State()
    resistance = State()
    compatibility = State()
    price = State()
    quantity = State()
    photo = State()
    preview = State()


class QuantityEdit(StatesGroup):
    amount = State()


class CartAdd(StatesGroup):
    amount = State()


class CustomOrder(StatesGroup):
    user_id = State()
    items = State()
    total = State()


class ProductPhoto(StatesGroup):
    photo = State()


class StockEdit(StatesGroup):
    quantity = State()


class OrderPriceEdit(StatesGroup):
    amount = State()


@dataclass
class Config:
    bot_token: str
    admin_id: int


def get_config() -> Config:
    load_env()
    token = os.getenv("BOT_TOKEN", "").strip().strip('"')
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        raise RuntimeError("Brakuje BOT_TOKEN lub ADMIN_ID w pliku .env.")
    return Config(bot_token=token, admin_id=int(admin_id))


async def send_menu(message: Message, admin_id: int) -> None:
    await message.answer(
        "Wybierz opcję:",
        reply_markup=main_menu(message.from_user.id == admin_id),
    )


async def edit_or_answer(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=markup)
    await answer_callback(callback)


async def answer_callback(callback: CallbackQuery, *args, **kwargs) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest:
        pass


async def start(message: Message, admin_id: int) -> None:
    await send_menu(message, admin_id)


async def menu_callback(callback: CallbackQuery, admin_id: int) -> None:
    await edit_or_answer(callback, "Wybierz opcję:", main_menu(callback.from_user.id == admin_id))


async def show_catalog(callback: CallbackQuery) -> None:
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT category, COUNT(*) AS count, SUM(quantity) AS quantity
            FROM client_products
            WHERE quantity > 0 AND is_active = 1
            GROUP BY category
            """
        ).fetchall()

    if products:
        lines = ["<b>Pełna dostępność</b>"]
        for row in products:
            lines.append(
                f"{CATEGORIES.get(row['category'], row['category'])}: "
                f"{row['quantity'] or 0} szt. w {row['count']} pozycjach"
            )
        text = "\n".join(lines)
    else:
        text = "Brak aktywnych produktów w dostępności."
    await edit_or_answer(callback, text, catalog_menu())


async def show_category(callback: CallbackQuery) -> None:
    category = callback.data.split(":", 1)[1]
    if category == "accessories":
        await edit_or_answer(callback, "<b>Akcesoria</b>", accessory_menu())
        return
    if category == "liquids":
        await show_liquid_brands(callback)
        return
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT *
            FROM client_products
            WHERE category = ? AND quantity > 0 AND is_active = 1
            ORDER BY name, flavor, strength
            """,
            (category,),
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "W tej kategorii nie ma teraz produktów.", catalog_menu())
        return

    rows = [[(product_display_name(p), f"prod:{p['id']}")] for p in products]
    rows.append([("Wróć", "catalog")])
    await edit_or_answer(callback, f"<b>{CATEGORIES[category]}</b>", kb(rows))


def get_liquid_brands() -> list[str]:
    with closing(db()) as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(brand), ''), 'Inne') AS brand
            FROM client_products
            WHERE category = 'liquids' AND quantity > 0 AND is_active = 1
            GROUP BY COALESCE(NULLIF(TRIM(brand), ''), 'Inne')
            ORDER BY brand
            """
        ).fetchall()
    return [row["brand"] for row in rows]


async def show_liquid_brands(callback: CallbackQuery) -> None:
    brands = get_liquid_brands()
    if not brands:
        await edit_or_answer(callback, "W tej kategorii nie ma teraz produktów.", catalog_menu())
        return

    rows = [[(brand, f"liqbrand:{index}")] for index, brand in enumerate(brands)]
    rows.append([("Wróć", "catalog")])
    await edit_or_answer(callback, "<b>Płyny</b>\nWybierz markę:", kb(rows))


async def show_liquid_brand(callback: CallbackQuery) -> None:
    brand_index = int(callback.data.split(":", 1)[1])
    brands = get_liquid_brands()
    if brand_index < 0 or brand_index >= len(brands):
        await show_liquid_brands(callback)
        return
    brand = brands[brand_index]
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT *
            FROM client_products
            WHERE category = 'liquids'
              AND COALESCE(NULLIF(TRIM(brand), ''), 'Inne') = ?
              AND quantity > 0
              AND is_active = 1
            ORDER BY name, strength
            """,
            (brand,),
        ).fetchall()

    if not products:
        await show_liquid_brands(callback)
        return

    rows = [[(product_display_name(p), f"prod:{p['id']}")] for p in products]
    rows.append([("Wróć", "cat:liquids")])
    await edit_or_answer(callback, f"<b>{brand}</b>", kb(rows))


async def show_accessory_type(callback: CallbackQuery) -> None:
    accessory_type = callback.data.split(":", 1)[1]
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT *
            FROM client_products
            WHERE category = 'accessories'
              AND accessory_type = ?
              AND quantity > 0
              AND is_active = 1
            ORDER BY name, volume, resistance
            """,
            (accessory_type,),
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "W tej kategorii nie ma teraz produktów.", accessory_menu())
        return

    rows = [[(product_display_name(p), f"prod:{p['id']}")] for p in products]
    rows.append([("Wróć", "cat:accessories")])
    await edit_or_answer(callback, f"<b>{ACCESSORY_TYPES.get(accessory_type, accessory_type)}</b>", kb(rows))


async def show_product(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT * FROM client_products WHERE id = ? AND quantity > 0 AND is_active = 1",
            (product_id,),
        ).fetchone()

    if not product:
        await edit_or_answer(callback, "Produkt nie jest już dostępny.", catalog_menu())
        return

    markup = kb(
        [
            [("Dodaj do koszyka", f"cart:add:{product_id}")],
            [("Wróć", f"cat:{product['category']}")],
        ]
    )
    if product["photo_file_id"]:
        if callback.message:
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=product["photo_file_id"],
                caption=product_text(product),
                reply_markup=markup,
            )
        await answer_callback(callback)
        return
    await edit_or_answer(callback, product_text(product), markup)


async def add_to_cart(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT id, name, quantity FROM client_products WHERE id = ? AND quantity > 0 AND is_active = 1",
            (product_id,),
        ).fetchone()
        if not product:
            await answer_callback(callback, "Produkt nie jest dostępny.", show_alert=True)
            return
    await state.update_data(product_id=product_id)
    await state.set_state(CartAdd.amount)
    await callback.message.answer(
        f"Podaj ilość dla produktu:\n<b>{product['name']}</b>\n\nDostępne: {product['quantity']} szt."
    )
    await answer_callback(callback)


async def save_cart_add(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = int(data["product_id"])
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer("Podaj poprawną ilość większą od 0.")
        return

    with closing(db()) as conn:
        product = conn.execute(
            "SELECT name, quantity FROM client_products WHERE id = ? AND quantity > 0 AND is_active = 1",
            (product_id,),
        ).fetchone()
        if not product:
            await message.answer("Produkt nie jest już dostępny.")
            await state.clear()
            return
        if quantity > product["quantity"]:
            await message.answer(f"Dostępna ilość: {product['quantity']}.")
            return
        conn.execute(
            """
            INSERT INTO carts (user_id, product_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (message.from_user.id, product_id, quantity),
        )
        conn.commit()

    await state.clear()
    await message.answer(
        f"✅ Dodano do koszyka.\n\n{product['name']} x {quantity}",
        reply_markup=kb([[("Pokaż koszyk", "cart")], [("Wróć do katalogu", "catalog")]]),
    )


async def show_cart(callback: CallbackQuery) -> None:
    items = get_cart_items(callback.from_user.id)
    rows: list[list[tuple[str, str]]] = []
    for item in items:
        rows.append([(f"Zmień ilość: {item['name']}", f"cart:qty:{item['id']}")])
        rows.append([(f"Usuń: {item['name']}", f"cart:remove:{item['id']}")])
    if items:
        rows.append([("Potwierdź zamówienie", "cart:confirm")])
        rows.append([("Wyczyść koszyk", "cart:clear")])
    rows.append([("Wróć do katalogu", "catalog")])
    await edit_or_answer(callback, cart_text(items), kb(rows))


async def remove_from_cart(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        conn.execute(
            "DELETE FROM carts WHERE user_id = ? AND product_id = ?",
            (callback.from_user.id, product_id),
        )
        conn.commit()
    await show_cart(callback)


async def clear_cart(callback: CallbackQuery) -> None:
    with closing(db()) as conn:
        conn.execute("DELETE FROM carts WHERE user_id = ?", (callback.from_user.id,))
        conn.commit()
    await show_cart(callback)


async def ask_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[2])
    await state.update_data(product_id=product_id, source_message_id=callback.message.message_id)
    await state.set_state(QuantityEdit.amount)
    await callback.message.answer("Podaj nową ilość. Wpisz 0, aby usunąć produkt.")
    await answer_callback(callback)


async def save_quantity(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = int(data["product_id"])
    try:
        quantity = int(message.text.strip())
    except (TypeError, ValueError):
        await message.answer("Podaj liczbę całkowitą.")
        return

    with closing(db()) as conn:
        product = conn.execute("SELECT quantity FROM client_products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            await message.answer("Produkt nie istnieje.")
            await state.clear()
            return
        if quantity <= 0:
            conn.execute(
                "DELETE FROM carts WHERE user_id = ? AND product_id = ?",
                (message.from_user.id, product_id),
            )
        elif quantity <= product["quantity"]:
            conn.execute(
                """
                INSERT INTO carts (user_id, product_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = excluded.quantity
                """,
                (message.from_user.id, product_id, quantity),
            )
        else:
            await message.answer(f"Dostępna ilość: {product['quantity']}.")
            return
        conn.commit()

    await state.clear()
    await message.answer("Koszyk zaktualizowany.", reply_markup=kb([[("Pokaż koszyk", "cart")]]))


async def confirm_cart(callback: CallbackQuery, bot: Bot, admin_id: int) -> None:
    items = get_cart_items(callback.from_user.id)
    if not items:
        await answer_callback(callback, "Koszyk jest pusty.", show_alert=True)
        return
    if cart_quantity(items) > MAX_CLIENT_ORDER_ITEMS:
        await answer_callback(
            callback,
            f"Limit zwykłego zamówienia to {MAX_CLIENT_ORDER_ITEMS} szt. Napisz do administratora.",
            show_alert=True,
        )
        return

    with closing(db()) as conn:
        try:
            conn.execute("BEGIN")
            fresh_items = conn.execute(
                """
                SELECT p.*, c.quantity AS cart_quantity
                FROM carts c
                JOIN client_products p ON p.id = c.product_id
                WHERE c.user_id = ?
                """,
                (callback.from_user.id,),
            ).fetchall()
            for item in fresh_items:
                if item["is_active"] != 1 or item["quantity"] < item["cart_quantity"]:
                    raise ValueError(f"Brak wymaganej ilości: {item['name']}")
            if cart_quantity(fresh_items) > MAX_CLIENT_ORDER_ITEMS:
                raise ValueError(f"Limit zwykłego zamówienia to {MAX_CLIENT_ORDER_ITEMS} szt.")

            total = cart_total(fresh_items)
            notes = pricing_note(fresh_items)
            cursor = conn.execute(
                """
                INSERT INTO orders (user_id, username, full_name, total, notes, is_custom, status, created_at)
                VALUES (?, ?, ?, ?, ?, 0, 'pending', ?)
                """,
                (
                    callback.from_user.id,
                    callback.from_user.username or "",
                    callback.from_user.full_name,
                    float(total),
                    notes,
                    now_iso(),
                ),
            )
            order_id = cursor.lastrowid
            liquids_qty = liquid_count(fresh_items)
            for item in fresh_items:
                unit_price = effective_unit_price(item, liquids_qty)
                conn.execute(
                    """
                    INSERT INTO order_items
                    (order_id, product_id, warehouse_product_id, name, brand, category, flavor, description, strength, price, quantity)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        item["id"],
                        item["warehouse_product_id"],
                        item["name"],
                        item["brand"],
                        item["category"],
                        item["flavor"],
                        item["description"],
                        item["strength"],
                        float(unit_price),
                        item["cart_quantity"],
                    ),
                )
                conn.execute(
                    "UPDATE client_products SET quantity = quantity - ? WHERE id = ?",
                    (item["cart_quantity"], item["id"]),
                )
                set_product_activity(conn, "client_products", item["id"])
            conn.execute("DELETE FROM carts WHERE user_id = ?", (callback.from_user.id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            await answer_callback(callback, str(exc), show_alert=True)
            return

    order_text = cart_text(items).replace("<b>Koszyk</b>", f"<b>Zamówienie #{order_id}</b>")
    await bot.send_message(
        admin_id,
        "Nowe zamówienie.\n"
        f"Klient: {user_link(callback.from_user.id, callback.from_user.full_name)} "
        f"(@{callback.from_user.username or '-'})\n"
        f"Telegram ID: <code>{callback.from_user.id}</code>\n\n"
        f"{order_text}",
        reply_markup=order_admin_keyboard(order_id, callback.from_user.id),
    )
    await edit_or_answer(
        callback,
        f"Zamówienie #{order_id} zostało przyjęte.\nSkontaktujemy się z Tobą w sprawie odbioru.",
        main_menu(False),
    )


async def contact(callback: CallbackQuery, admin_id: int) -> None:
    await edit_or_answer(
        callback,
        "Kontakt z obsługą:\nNapisz do administratora, aby ustalić odbiór lub szczegóły zamówienia.",
        contact_keyboard(admin_id),
    )


async def admin_only(callback: CallbackQuery, admin_id: int) -> bool:
    if callback.from_user.id != admin_id:
        await answer_callback(callback, "Brak dostępu.", show_alert=True)
        return False
    return True


async def show_admin(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    await edit_or_answer(callback, "Panel administratora:", admin_menu())


async def admin_add_start(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    await state.clear()
    await state.set_state(AddProduct.product_type)
    await edit_or_answer(
        callback,
        "Wybierz typ produktu:",
        kb(
            [
                [("💧 Płyn", "add:type:liquids")],
                [("🚬 Jednorazówka", "add:type:disposables")],
                [("🧩 Akcesorium", "add:type:accessories")],
                [("🔙 Wróć", "admin")],
            ]
        ),
    )


async def add_product_type(callback: CallbackQuery, state: FSMContext) -> None:
    product_type = callback.data.split(":")[2]
    if product_type == "liquids":
        await state.update_data(category="liquids", accessory_type="", brand="", description="", photo_file_id="")
        await state.set_state(AddProduct.name)
        await callback.message.answer("1. Wprowadź nazwę płynu:")
        await answer_callback(callback)
        return

    if product_type == "disposables":
        await state.update_data(
            category="disposables",
            accessory_type="",
            name="",
            description="",
            photo_file_id="",
            volume="",
            resistance="",
            compatibility="",
        )
        await state.set_state(AddProduct.brand)
        await callback.message.answer("1. Wprowadź producenta:")
        await answer_callback(callback)
        return

    await state.update_data(category="accessories")
    await state.set_state(AddProduct.accessory_type)
    await edit_or_answer(
        callback,
        "Wybierz typ akcesorium:",
        kb(
            [
                [("Kartridże / Pody", "addacc:pods")],
                [("Urządzenia", "addacc:devices")],
                [("Inne", "addacc:other")],
                [("🔙 Wróć", "admin:add")],
            ]
        ),
    )


async def add_accessory_type(callback: CallbackQuery, state: FSMContext) -> None:
    accessory_type = callback.data.split(":", 1)[1]
    await state.update_data(accessory_type=accessory_type, brand="", flavor="", strength="")
    await state.set_state(AddProduct.name)
    if accessory_type == "pods":
        await callback.message.answer("1. Wprowadź nazwę produktu:")
    else:
        await callback.message.answer("1. Wprowadź nazwę akcesorium:")
    await answer_callback(callback)


async def add_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(name=message.text.strip())
    if data.get("category") == "liquids":
        await state.set_state(AddProduct.brand)
        await message.answer("2. Wprowadź markę:")
        return
    if data.get("accessory_type") == "pods":
        await state.set_state(AddProduct.volume)
        await message.answer("2. Wprowadź pojemność kartridża w ml:")
        return
    await state.set_state(AddProduct.price)
    await message.answer("2. Wprowadź cenę:")


async def add_brand(message: Message, state: FSMContext) -> None:
    await state.update_data(brand=message.text.strip())
    await state.set_state(AddProduct.flavor)
    data = await state.get_data()
    step = "2" if data.get("category") == "disposables" else "3"
    label = "smak" if data.get("category") == "disposables" else "opis"
    await message.answer(f"{step}. Wprowadź {label}:")


async def add_flavor(message: Message, state: FSMContext) -> None:
    await state.update_data(flavor=message.text.strip())
    data = await state.get_data()
    if data.get("category") == "disposables":
        await state.set_state(AddProduct.puffs)
        await message.answer("3. Wprowadź ilość zaciągnięć:")
        return
    await state.set_state(AddProduct.strength)
    await message.answer("4. Wprowadź moc:")


async def add_puffs(message: Message, state: FSMContext) -> None:
    await state.update_data(puffs=message.text.strip())
    await state.set_state(AddProduct.strength)
    await message.answer("4. Wprowadź moc:")


async def add_strength(message: Message, state: FSMContext) -> None:
    await state.update_data(strength=message.text.strip())
    await state.set_state(AddProduct.price)
    await message.answer("5. Wprowadź cenę:")


async def add_volume(message: Message, state: FSMContext) -> None:
    await state.update_data(volume=message.text.strip())
    await state.set_state(AddProduct.resistance)
    await message.answer("3. Wprowadź oporność w Ω:")


async def add_resistance(message: Message, state: FSMContext) -> None:
    await state.update_data(resistance=message.text.strip())
    await state.set_state(AddProduct.compatibility)
    await message.answer(
        "4. Wprowadź kompatybilność, jeśli potrzebna:",
        reply_markup=kb([[("Pomiń", "add:skip:compatibility")]]),
    )


async def skip_compatibility(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(compatibility="")
    await state.set_state(AddProduct.price)
    await callback.message.answer("5. Wprowadź cenę:")
    await answer_callback(callback)


async def add_compatibility(message: Message, state: FSMContext) -> None:
    await state.update_data(compatibility=message.text.strip())
    await state.set_state(AddProduct.price)
    await message.answer("5. Wprowadź cenę:")


async def add_price(message: Message, state: FSMContext) -> None:
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("Podaj poprawną cenę, np. 29.99.")
        return
    await state.update_data(price=float(price))
    await state.set_state(AddProduct.quantity)
    data = await state.get_data()
    step = "6" if data.get("category") in {"liquids", "disposables"} or data.get("accessory_type") == "pods" else "3"
    await message.answer(f"{step}. Wprowadź ilość:")


async def add_quantity(message: Message, state: FSMContext) -> None:
    try:
        quantity = int(message.text.strip())
        if quantity < 0:
            raise ValueError
    except ValueError:
        await message.answer("Podaj poprawną ilość.")
        return

    await state.update_data(quantity=quantity)
    data = await state.get_data()
    if data.get("category") in {"accessories", "disposables"} and (
        data.get("category") == "disposables" or data.get("accessory_type") == "pods"
    ):
        await state.set_state(AddProduct.photo)
        step = "7" if data.get("accessory_type") == "pods" else "7"
        await message.answer(f"{step}. Wyślij zdjęcie produktu albo pomiń:", reply_markup=kb([[("Pomiń zdjęcie", "add:skip:photo")]]))
        return
    await save_added_product(message, state)


def build_product_payload(data: dict) -> dict:
    category = data["category"]
    accessory_type = data.get("accessory_type", "")
    name = data["name"]
    volume = data.get("volume", "")
    resistance = data.get("resistance", "")
    compatibility = data.get("compatibility", "")
    if category == "accessories" and accessory_type == "pods":
        description = f"Kartridż do urządzeń kompatybilnych z systemem {name}."
        return {
            "name": name,
            "brand": data.get("brand", ""),
            "category": category,
            "accessory_type": accessory_type,
            "flavor": "",
            "description": description,
            "photo_file_id": data.get("photo_file_id", ""),
            "volume": volume,
            "resistance": resistance,
            "compatibility": compatibility,
            "puffs": "",
            "strength": "",
            "price": data["price"],
            "quantity": data["quantity"],
        }
    if category == "disposables":
        brand = data.get("brand", "")
        flavor = data.get("flavor", "")
        generated_name = f"{brand} — {flavor}" if brand and flavor else brand or flavor
        return {
            "name": generated_name,
            "brand": brand,
            "category": category,
            "accessory_type": "",
            "flavor": flavor,
            "description": data.get("description", ""),
            "photo_file_id": data.get("photo_file_id", ""),
            "volume": "",
            "resistance": "",
            "compatibility": "",
            "puffs": data.get("puffs", ""),
            "strength": data.get("strength", ""),
            "price": data["price"],
            "quantity": data["quantity"],
        }
    return {
        "name": name,
        "brand": data.get("brand", ""),
        "category": category,
        "accessory_type": accessory_type,
        "flavor": data.get("flavor", ""),
        "description": data.get("description", ""),
        "photo_file_id": data.get("photo_file_id", ""),
        "volume": "",
        "resistance": "",
        "compatibility": "",
        "puffs": "",
        "strength": data.get("strength", ""),
        "price": data["price"],
        "quantity": data["quantity"],
    }


def cartridge_preview_text(payload: dict) -> str:
    lines = [
        "<b>Sprawdź produkt przed dodaniem:</b>",
        "",
        f"✔️ {payload['name']} — {format_volume(payload['volume'])} / {format_resistance(payload['resistance'])}",
        "",
        f"Kartridż do urządzeń kompatybilnych z systemem {payload['name']}.",
        f"Pojemność: {format_volume(payload['volume'])}",
        f"Oporność: {format_resistance(payload['resistance'])}",
        "",
        f"Cena: {money(payload['price'])}",
        f"Dostępne: {payload['quantity']} szt.",
    ]
    return "\n".join(lines)


def disposable_preview_text(payload: dict) -> str:
    return "\n".join(
        [
            "<b>Sprawdź produkt przed dodaniem:</b>",
            "",
            f"✔️ {product_display_name(payload)}",
            "",
            f"Marka: {payload['brand'] or '-'}",
            "Kategoria: Jednorazówki",
            f"Smak: {payload['flavor'] or '-'}",
            f"Ilość zaciągnięć: {payload['puffs'] or '-'}",
            f"Moc: {payload['strength'] or '-'}",
            "",
            f"Cena: {money(payload['price'])}",
            f"Dostępne: {payload['quantity']} szt.",
        ]
    )


def add_preview_text(payload: dict) -> str:
    if payload["category"] == "accessories" and payload["accessory_type"] == "pods":
        return cartridge_preview_text(payload)
    if payload["category"] == "disposables":
        return disposable_preview_text(payload)
    return product_text(payload)


async def add_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Wyślij zdjęcie produktu albo użyj przycisku Pomiń zdjęcie.")
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await show_add_preview(message, state)


async def skip_add_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo_file_id="")
    await show_add_preview(callback.message, state)
    await answer_callback(callback)


async def show_add_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    payload = build_product_payload(data)
    await state.update_data(payload=payload)
    await state.set_state(AddProduct.preview)
    markup = kb(
        [
            [("✅ Zapisz", "add:save")],
            [("✏️ Edytuj", "admin:add")],
            [("❌ Anuluj", "add:cancel")],
        ]
    )
    text = add_preview_text(payload)
    if payload["photo_file_id"]:
        await message.answer_photo(payload["photo_file_id"], caption=text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def save_added_product(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    payload = build_product_payload(data)
    warehouse_id = insert_product_pair(payload)
    await state.clear()
    if payload["category"] == "accessories" and payload["accessory_type"] == "pods":
        await message.answer(
            "✅ Produkt dodany.\n\n"
            f"{product_display_name(payload)} teraz wyświetla się w sekcji:\n"
            "Akcesoria → Kartridże / Pody",
            reply_markup=admin_menu(),
        )
    elif payload["category"] == "disposables":
        await message.answer(
            "✅ Produkt dodany.\n\n"
            f"{product_display_name(payload)} teraz wyświetla się w sekcji:\n"
            "Jednorazówki",
            reply_markup=admin_menu(),
        )
    else:
        await message.answer("✅ Produkt dodany.", reply_markup=admin_menu())


async def save_preview_product(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payload = data["payload"]
    insert_product_pair(payload)
    await state.clear()
    if payload["category"] == "accessories" and payload["accessory_type"] == "pods":
        section = "Akcesoria → Kartridże / Pody"
    elif payload["category"] == "disposables":
        section = "Jednorazówki"
    else:
        section = CATEGORIES.get(payload["category"], payload["category"])
    await edit_or_answer(callback, "✅ Produkt dodany.\n\n" f"{product_display_name(payload)} teraz wyświetla się w sekcji:\n" f"{section}", admin_menu())


async def cancel_add_product(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(callback, "Dodawanie produktu anulowane.", admin_menu())


def insert_product_pair(payload: dict) -> int:
    quantity = int(payload["quantity"])
    is_active = 1 if quantity > 0 else 0
    with closing(db()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO warehouse_products
            (name, brand, category, accessory_type, flavor, description, photo_file_id, volume, resistance, compatibility, puffs, strength, price, quantity, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["name"],
                payload["brand"],
                payload["category"],
                payload["accessory_type"],
                payload["flavor"],
                payload["description"],
                payload["photo_file_id"],
                payload["volume"],
                payload["resistance"],
                payload["compatibility"],
                payload["puffs"],
                payload["strength"],
                payload["price"],
                quantity,
                is_active,
                now_iso(),
            ),
        )
        warehouse_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO client_products
            (warehouse_product_id, name, brand, category, accessory_type, flavor, description, photo_file_id, volume, resistance, compatibility, puffs, strength, price, quantity, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                warehouse_id,
                payload["name"],
                payload["brand"],
                payload["category"],
                payload["accessory_type"],
                payload["flavor"],
                payload["description"],
                payload["photo_file_id"],
                payload["volume"],
                payload["resistance"],
                payload["compatibility"],
                payload["puffs"],
                payload["strength"],
                payload["price"],
                quantity,
                is_active,
                now_iso(),
            ),
        )
        conn.commit()
    return warehouse_id


async def admin_list(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    with closing(db()) as conn:
        products = conn.execute(
            "SELECT * FROM warehouse_products ORDER BY category, name, flavor, strength"
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "Lista produktów jest pusta.", admin_menu())
        return
    lines = ["<b>Produkty w realnym składzie</b>"]
    for p in products:
        lines.append(admin_product_list_line(p))
    await edit_or_answer(callback, "\n".join(lines), admin_menu())


async def admin_photo_list(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    await state.clear()
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT id, name, flavor, strength, quantity, photo_file_id
            FROM warehouse_products
            ORDER BY category, name, flavor, strength
            """
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "Brak produktów do dodania zdjęcia.", admin_menu())
        return

    rows = [
        [
            (
                f"{'Zmień' if p['photo_file_id'] else 'Dodaj'}: {p['name']} ({p['quantity']})",
                f"admin:photo:select:{p['id']}",
            )
        ]
        for p in products
    ]
    rows.append([("Wróć", "admin")])
    await edit_or_answer(callback, "Wybierz produkt do zdjęcia:", kb(rows))


async def admin_photo_select(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    product_id = int(callback.data.split(":")[3])
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT id, name FROM warehouse_products WHERE id = ?",
            (product_id,),
        ).fetchone()
    if not product:
        await edit_or_answer(callback, "Produkt już nie istnieje.", admin_menu())
        return

    await state.update_data(warehouse_product_id=product_id)
    await state.set_state(ProductPhoto.photo)
    await callback.message.answer(
        f"Wyślij teraz zdjęcie dla produktu:\n<b>{product['name']}</b>\n\n"
        "Najlepiej wyślij je jako zwykłe zdjęcie, nie jako plik."
    )
    await answer_callback(callback)


async def save_product_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Wyślij zdjęcie produktu jako zdjęcie.")
        return

    data = await state.get_data()
    warehouse_product_id = int(data["warehouse_product_id"])
    photo_file_id = message.photo[-1].file_id
    with closing(db()) as conn:
        conn.execute(
            "UPDATE warehouse_products SET photo_file_id = ? WHERE id = ?",
            (photo_file_id, warehouse_product_id),
        )
        conn.execute(
            "UPDATE client_products SET photo_file_id = ? WHERE warehouse_product_id = ?",
            (photo_file_id, warehouse_product_id),
        )
        conn.commit()

    await state.clear()
    await message.answer("Zdjęcie zostało zapisane dla produktu.", reply_markup=admin_menu())


async def admin_availability(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    with closing(db()) as conn:
        products = conn.execute(
            "SELECT id, name, flavor, quantity, is_active FROM client_products ORDER BY category, name"
        ).fetchall()
    if not products:
        await edit_or_answer(callback, "Brak produktów do edycji.", admin_menu())
        return
    rows = [
        [
            (
                f"{'Ukryj' if p['is_active'] else 'Pokaż'}: {p['name']} ({p['quantity']})",
                f"admin:toggle:{p['id']}",
            )
        ]
        for p in products
    ]
    rows.append([("Wróć", "admin")])
    await edit_or_answer(callback, "Zmień dostępność klienta:", kb(rows))


async def admin_toggle(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    product_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        product = conn.execute("SELECT quantity, is_active FROM client_products WHERE id = ?", (product_id,)).fetchone()
        if product and product["quantity"] > 0:
            conn.execute(
                "UPDATE client_products SET is_active = ? WHERE id = ?",
                (0 if product["is_active"] else 1, product_id),
            )
            conn.commit()
    await admin_availability(callback, admin_id)


async def admin_stock_list(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    await state.clear()
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT id, name, category, accessory_type, volume, resistance, quantity
            FROM warehouse_products
            ORDER BY category, name, volume, resistance
            """
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "Brak produktów do edycji ilości.", admin_menu())
        return

    rows = [
        [(f"{product_display_name(p)} ({p['quantity']} szt.)", f"admin:stock:select:{p['id']}")]
        for p in products
    ]
    rows.append([("Wróć", "admin")])
    await edit_or_answer(callback, "Wybierz produkt do zmiany ilości:", kb(rows))


async def admin_stock_select(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    warehouse_id = int(callback.data.split(":")[3])
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT * FROM warehouse_products WHERE id = ?",
            (warehouse_id,),
        ).fetchone()
    if not product:
        await edit_or_answer(callback, "Produkt już nie istnieje.", admin_menu())
        return

    await state.update_data(warehouse_product_id=warehouse_id)
    await state.set_state(StockEdit.quantity)
    await callback.message.answer(
        f"Podaj nową aktualną ilość dla produktu:\n<b>{product_display_name(product)}</b>\n\n"
        f"Obecnie: {product['quantity']} szt."
    )
    await answer_callback(callback)


async def save_stock_quantity(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    warehouse_id = int(data["warehouse_product_id"])
    try:
        quantity = int(message.text.strip())
        if quantity < 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer("Podaj poprawną ilość: 0 lub większą.")
        return

    is_active = 1 if quantity > 0 else 0
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT * FROM warehouse_products WHERE id = ?",
            (warehouse_id,),
        ).fetchone()
        if not product:
            await message.answer("Produkt już nie istnieje.")
            await state.clear()
            return
        conn.execute(
            "UPDATE warehouse_products SET quantity = ?, is_active = ? WHERE id = ?",
            (quantity, is_active, warehouse_id),
        )
        conn.execute(
            "UPDATE client_products SET quantity = ?, is_active = ? WHERE warehouse_product_id = ?",
            (quantity, is_active, warehouse_id),
        )
        conn.commit()

    await state.clear()
    await message.answer(
        f"Ilość została zaktualizowana.\n\n{product_display_name(product)}: {quantity} szt.",
        reply_markup=admin_menu(),
    )


async def admin_delete_list(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    with closing(db()) as conn:
        products = conn.execute(
            """
            SELECT
                w.id AS warehouse_id,
                w.name,
                w.flavor,
                w.strength,
                w.quantity AS warehouse_quantity,
                COALESCE(c.quantity, 0) AS client_quantity
            FROM warehouse_products w
            LEFT JOIN client_products c ON c.warehouse_product_id = w.id
            ORDER BY w.category, w.name, w.flavor, w.strength
            """
        ).fetchall()

    if not products:
        await edit_or_answer(callback, "Brak produktów do usunięcia.", admin_menu())
        return

    rows = [
        [
            (
                f"{p['name']} | {p['flavor'] or '-'} | skład {p['warehouse_quantity']} / klient {p['client_quantity']}",
                f"admin:delete:confirm:{p['warehouse_id']}",
            )
        ]
        for p in products
    ]
    rows.append([("Wróć", "admin")])
    await edit_or_answer(callback, "Wybierz produkt do całkowitego usunięcia:", kb(rows))


async def admin_delete_confirm(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    warehouse_id = int(callback.data.split(":")[3])
    with closing(db()) as conn:
        product = conn.execute(
            "SELECT * FROM warehouse_products WHERE id = ?",
            (warehouse_id,),
        ).fetchone()

    if not product:
        await edit_or_answer(callback, "Produkt już nie istnieje.", admin_menu())
        return

    await edit_or_answer(
        callback,
        (
            "Czy na pewno usunąć ten produkt z realnego składu i dostępności klienta?\n\n"
            f"{product_text(product)}"
        ),
        kb(
            [
                [("Tak, usuń produkt", f"admin:delete:do:{warehouse_id}")],
                [("Nie, wróć", "admin:delete")],
            ]
        ),
    )


async def admin_delete_do(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    warehouse_id = int(callback.data.split(":")[3])
    with closing(db()) as conn:
        try:
            conn.execute("BEGIN")
            client_rows = conn.execute(
                "SELECT id FROM client_products WHERE warehouse_product_id = ?",
                (warehouse_id,),
            ).fetchall()
            client_ids = [row["id"] for row in client_rows]
            for product_id in client_ids:
                conn.execute("DELETE FROM carts WHERE product_id = ?", (product_id,))
            conn.execute("DELETE FROM client_products WHERE warehouse_product_id = ?", (warehouse_id,))
            conn.execute("DELETE FROM warehouse_products WHERE id = ?", (warehouse_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            await answer_callback(callback, str(exc), show_alert=True)
            return

    await edit_or_answer(callback, "Produkt został całkowicie usunięty.", admin_menu())


async def custom_order_start(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    await state.clear()
    await state.set_state(CustomOrder.user_id)
    await callback.message.answer("Podaj Telegram ID klienta dla zamówienia niestandardowego.")
    await answer_callback(callback)


async def custom_order_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except (TypeError, ValueError):
        await message.answer("Podaj poprawny Telegram ID, np. 123456789.")
        return
    await state.update_data(user_id=user_id)
    await state.set_state(CustomOrder.items)
    await message.answer(
        "Podaj produkty w formacie ID:ILOŚĆ, oddzielone przecinkami.\n"
        "Przykład: 1:8, 3:2\n\n"
        "ID sprawdzisz w: Panel administratora -> Lista produktów."
    )


def parse_custom_items(raw_text: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for raw_part in raw_text.replace("\n", ",").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError("Użyj formatu ID:ILOŚĆ.")
        product_id_text, quantity_text = part.split(":", 1)
        product_id = int(product_id_text.strip())
        quantity = int(quantity_text.strip())
        if product_id <= 0 or quantity <= 0:
            raise ValueError("ID i ilość muszą być większe od 0.")
        result.append((product_id, quantity))
    if not result:
        raise ValueError("Podaj przynajmniej jeden produkt.")
    return result


async def custom_order_items(message: Message, state: FSMContext) -> None:
    try:
        requested_items = parse_custom_items(message.text)
    except (TypeError, ValueError):
        await message.answer("Niepoprawny format. Przykład: 1:8, 3:2")
        return

    with closing(db()) as conn:
        products = []
        for warehouse_id, quantity in requested_items:
            product = conn.execute(
                "SELECT * FROM warehouse_products WHERE id = ?",
                (warehouse_id,),
            ).fetchone()
            if not product:
                await message.answer(f"Nie znaleziono produktu #{warehouse_id}.")
                return
            if product["quantity"] < quantity:
                await message.answer(f"Produkt #{warehouse_id} ma w realnym składzie tylko {product['quantity']} szt.")
                return
            products.append((product, quantity))

    estimated_total = Decimal("0")
    lines = ["<b>Zamówienie niestandardowe</b>"]
    liquid_qty = sum(quantity for product, quantity in products if product["category"] == "liquids")
    for product, quantity in products:
        unit_price = LIQUID_DISCOUNT_PRICE if product["category"] == "liquids" and liquid_qty >= LIQUID_DISCOUNT_MIN_QTY else Decimal(str(product["price"]))
        estimated_total += unit_price * quantity
        lines.append(f"#{product['id']} {product['name']} x {quantity} = {money(unit_price * quantity)}")
    lines.append(f"\nSzacunkowo: {money(estimated_total)}")
    lines.append("Podaj finalną kwotę brutto albo wpisz: do ustalenia")

    await state.update_data(items=requested_items, estimated_total=float(estimated_total))
    await state.set_state(CustomOrder.total)
    await message.answer("\n".join(lines))


async def custom_order_total(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    raw_total = message.text.strip()
    notes = "Zamówienie niestandardowe utworzone przez administratora."
    if raw_total.lower() in {"do ustalenia", "ustalenia", "do uzgodnienia"}:
        total = Decimal(str(data["estimated_total"]))
        notes += " Finalna cena do ustalenia."
    else:
        try:
            total = Decimal(raw_total.replace(",", "."))
            if total < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer("Podaj kwotę, np. 320, albo wpisz: do ustalenia")
            return

    user_id = int(data["user_id"])
    requested_items = data["items"]
    with closing(db()) as conn:
        try:
            conn.execute("BEGIN")
            cursor = conn.execute(
                """
                INSERT INTO orders (user_id, username, full_name, total, notes, is_custom, status, created_at)
                VALUES (?, '', 'Zamówienie niestandardowe', ?, ?, 1, 'pending', ?)
                """,
                (user_id, float(total), notes, now_iso()),
            )
            order_id = cursor.lastrowid
            liquid_qty = 0
            products = []
            for warehouse_id, quantity in requested_items:
                product = conn.execute("SELECT * FROM warehouse_products WHERE id = ?", (warehouse_id,)).fetchone()
                if not product or product["quantity"] < quantity:
                    raise ValueError(f"Brak wymaganej ilości produktu #{warehouse_id}.")
                products.append((product, quantity))
                if product["category"] == "liquids":
                    liquid_qty += quantity

            for product, quantity in products:
                client_product = conn.execute(
                    "SELECT id, quantity FROM client_products WHERE warehouse_product_id = ?",
                    (product["id"],),
                ).fetchone()
                client_product_id = client_product["id"] if client_product else None
                unit_price = LIQUID_DISCOUNT_PRICE if product["category"] == "liquids" and liquid_qty >= LIQUID_DISCOUNT_MIN_QTY else Decimal(str(product["price"]))
                conn.execute(
                    """
                    INSERT INTO order_items
                    (order_id, product_id, warehouse_product_id, name, brand, category, flavor, description, strength, price, quantity)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        client_product_id,
                        product["id"],
                        product["name"],
                        product["brand"],
                        product["category"],
                        product["flavor"],
                        product["description"],
                        product["strength"],
                        float(unit_price),
                        quantity,
                    ),
                )
                if client_product_id:
                    conn.execute(
                        "UPDATE client_products SET quantity = MAX(quantity - ?, 0) WHERE id = ?",
                        (quantity, client_product_id),
                    )
                    set_product_activity(conn, "client_products", client_product_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            await message.answer(str(exc))
            return

    await state.clear()
    await message.answer(f"Zamówienie niestandardowe #{order_id} zostało utworzone.", reply_markup=admin_menu())
    try:
        await bot.send_message(
            user_id,
            f"Administrator utworzył dla Ciebie zamówienie niestandardowe #{order_id}.\n"
            f"Kwota: {money(total)}\n"
            "Szczegóły odbioru ustalisz z administratorem.",
        )
    except Exception as exc:
        await message.answer(
            "Nie udało się wysłać wiadomości do klienta. Zamówienie jest zapisane w panelu.\n\n"
            "Najczęstszy powód: klient jeszcze nie uruchomił bota przyciskiem Start albo zablokował bota.\n"
            f"Szczegóły Telegram: <code>{str(exc)}</code>"
        )


async def admin_orders(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    with closing(db()) as conn:
        orders = conn.execute(
            """
            SELECT id, full_name, username, total, notes, is_custom, status, created_at
            FROM orders
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
    if not orders:
        await edit_or_answer(callback, "Brak zamówień.", admin_menu())
        return
    rows = []
    lines = ["<b>Zamówienia</b>"]
    for order in orders:
        lines.append(
            f"#{order['id']} | {order['full_name']} (@{order['username'] or '-'}) | "
            f"{money(order['total'])} | {'custom' if order['is_custom'] else 'standard'} | {order['status']} | {order['created_at']}"
        )
        if order["notes"]:
            lines.append(f"Notatka: {order['notes']}")
        if order["status"] == "pending":
            rows.append(
                [
                    (f"Wydane #{order['id']}", f"admin:issue:{order['id']}"),
                    (f"Anuluj #{order['id']}", f"admin:cancel_order:{order['id']}"),
                ]
            )
            rows.append([(f"Zmień cenę #{order['id']}", f"admin:price:{order['id']}")])
    rows.append([("Wróć", "admin")])
    await edit_or_answer(callback, "\n".join(lines), kb(rows))


async def admin_issue(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    order_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        try:
            conn.execute("BEGIN")
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order or order["status"] != "pending":
                raise ValueError("Zamówienie nie jest aktywne.")
            items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            for item in items:
                if item["warehouse_product_id"]:
                    conn.execute(
                        "UPDATE warehouse_products SET quantity = MAX(quantity - ?, 0) WHERE id = ?",
                        (item["quantity"], item["warehouse_product_id"]),
                    )
                    set_product_activity(conn, "warehouse_products", item["warehouse_product_id"])
            conn.execute(
                "UPDATE orders SET status = 'issued', issued_at = ? WHERE id = ?",
                (now_iso(), order_id),
            )
            conn.execute(
                """
                INSERT INTO customers (user_id, username, full_name, order_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (order["user_id"], order["username"], order["full_name"], order_id, now_iso()),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            await answer_callback(callback, str(exc), show_alert=True)
            return
    await answer_callback(callback, "Zamówienie oznaczone jako wydane.", show_alert=True)
    await admin_orders(callback, admin_id)


async def admin_cancel_order(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    order_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        try:
            conn.execute("BEGIN")
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order or order["status"] != "pending":
                raise ValueError("Zamówienie nie jest aktywne.")
            items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            for item in items:
                if item["product_id"]:
                    conn.execute(
                        """
                        UPDATE client_products
                        SET quantity = quantity + ?,
                            is_active = CASE WHEN quantity + ? > 0 THEN 1 ELSE is_active END
                        WHERE id = ?
                        """,
                        (item["quantity"], item["quantity"], item["product_id"]),
                    )
            conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?",
                (order_id,),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            await answer_callback(callback, str(exc), show_alert=True)
            return

    await answer_callback(callback, "Zamówienie anulowane.", show_alert=True)
    await admin_orders(callback, admin_id)


async def admin_price_start(callback: CallbackQuery, state: FSMContext, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    order_id = int(callback.data.split(":")[2])
    with closing(db()) as conn:
        order = conn.execute(
            "SELECT id, total, status FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    if not order:
        await answer_callback(callback, "Zamówienie nie istnieje.", show_alert=True)
        return
    if order["status"] != "pending":
        await answer_callback(callback, "Cenę można zmienić tylko dla aktywnego zamówienia.", show_alert=True)
        return

    await state.update_data(order_id=order_id)
    await state.set_state(OrderPriceEdit.amount)
    await callback.message.answer(
        f"Podaj nową cenę brutto dla zamówienia #{order_id}.\n"
        f"Obecnie: {money(order['total'])}\n\n"
        "Przykład: 120 albo 120.50"
    )
    await answer_callback(callback)


async def save_order_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = int(data["order_id"])
    try:
        new_total = Decimal(message.text.strip().replace(",", "."))
        if new_total < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("Podaj poprawną kwotę, np. 120 albo 120.50.")
        return

    with closing(db()) as conn:
        order = conn.execute("SELECT notes, status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            await message.answer("Zamówienie nie istnieje.")
            await state.clear()
            return
        if order["status"] != "pending":
            await message.answer("Cenę można zmienić tylko dla aktywnego zamówienia.")
            await state.clear()
            return

        price_note = f"Cena zmieniona ręcznie przez administratora na {money(new_total)}."
        notes = order["notes"] or ""
        notes = f"{notes}\n{price_note}".strip() if notes else price_note
        conn.execute(
            "UPDATE orders SET total = ?, notes = ? WHERE id = ?",
            (float(new_total), notes, order_id),
        )
        conn.commit()

    await state.clear()
    await message.answer(
        f"Cena zamówienia #{order_id} została zmieniona na {money(new_total)}.",
        reply_markup=admin_menu(),
    )


def bar(value: int, max_value: int) -> str:
    if max_value <= 0 or value <= 0:
        return ""
    return "█" * max(1, round((value / max_value) * 12))


async def admin_stats(callback: CallbackQuery, admin_id: int) -> None:
    if not await admin_only(callback, admin_id):
        return
    with closing(db()) as conn:
        stock = conn.execute("SELECT COALESCE(SUM(quantity), 0) AS total FROM warehouse_products").fetchone()["total"]
        sold = conn.execute(
            """
            SELECT COALESCE(SUM(oi.quantity), 0) AS total
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.status = 'issued'
            """
        ).fetchone()["total"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total), 0) AS total FROM orders WHERE status = 'issued'"
        ).fetchone()["total"]
        by_category = conn.execute(
            """
            SELECT oi.category, COALESCE(SUM(oi.quantity), 0) AS qty, COALESCE(SUM(oi.price * oi.quantity), 0) AS total
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.status = 'issued'
            GROUP BY oi.category
            """
        ).fetchall()
        by_hour = conn.execute(
            """
            SELECT strftime('%H', created_at) AS hour, COUNT(*) AS count
            FROM orders
            GROUP BY hour
            ORDER BY hour
            """
        ).fetchall()

    max_hour = max([row["count"] for row in by_hour], default=0)
    lines = [
        "<b>Statystyka</b>",
        f"Płyny i inne produkty w realnym składzie: {stock} szt.",
        f"Sprzedano łącznie: {sold} szt.",
        f"Przychód brutto: {money(revenue)}",
        "",
        "<b>Sprzedaż według kategorii</b>",
    ]
    if by_category:
        for row in by_category:
            lines.append(f"{CATEGORIES.get(row['category'], row['category'])}: {row['qty']} szt. | {money(row['total'])}")
    else:
        lines.append("Brak zakończonej sprzedaży.")

    lines.extend(["", "<b>Godziny zamówień</b>"])
    if by_hour:
        for row in by_hour:
            lines.append(f"{row['hour']}:00 {bar(row['count'], max_hour)} {row['count']}")
    else:
        lines.append("Brak danych.")

    await edit_or_answer(callback, "\n".join(lines), admin_menu())


async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Anulowano.", reply_markup=main_menu(False))


async def setup_bot_profile(bot: Bot) -> None:
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Start")])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramRetryAfter:
        return


async def main() -> None:
    config = get_config()
    init_db()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    async def start_handler(message: Message) -> None:
        await start(message, config.admin_id)

    async def menu_handler(callback: CallbackQuery) -> None:
        await menu_callback(callback, config.admin_id)

    async def confirm_cart_handler(callback: CallbackQuery) -> None:
        await confirm_cart(callback, bot, config.admin_id)

    async def contact_handler(callback: CallbackQuery) -> None:
        await contact(callback, config.admin_id)

    async def show_admin_handler(callback: CallbackQuery) -> None:
        await show_admin(callback, config.admin_id)

    async def admin_add_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_add_start(callback, state, config.admin_id)

    async def admin_list_handler(callback: CallbackQuery) -> None:
        await admin_list(callback, config.admin_id)

    async def admin_photo_list_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_photo_list(callback, state, config.admin_id)

    async def admin_photo_select_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_photo_select(callback, state, config.admin_id)

    async def admin_orders_handler(callback: CallbackQuery) -> None:
        await admin_orders(callback, config.admin_id)

    async def custom_order_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await custom_order_start(callback, state, config.admin_id)

    async def custom_order_total_handler(message: Message, state: FSMContext) -> None:
        await custom_order_total(message, state, bot)

    async def admin_availability_handler(callback: CallbackQuery) -> None:
        await admin_availability(callback, config.admin_id)

    async def admin_toggle_handler(callback: CallbackQuery) -> None:
        await admin_toggle(callback, config.admin_id)

    async def admin_stock_list_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_stock_list(callback, state, config.admin_id)

    async def admin_stock_select_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_stock_select(callback, state, config.admin_id)

    async def admin_delete_list_handler(callback: CallbackQuery) -> None:
        await admin_delete_list(callback, config.admin_id)

    async def admin_delete_confirm_handler(callback: CallbackQuery) -> None:
        await admin_delete_confirm(callback, config.admin_id)

    async def admin_delete_do_handler(callback: CallbackQuery) -> None:
        await admin_delete_do(callback, config.admin_id)

    async def admin_issue_handler(callback: CallbackQuery) -> None:
        await admin_issue(callback, config.admin_id)

    async def admin_cancel_order_handler(callback: CallbackQuery) -> None:
        await admin_cancel_order(callback, config.admin_id)

    async def admin_price_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await admin_price_start(callback, state, config.admin_id)

    async def admin_stats_handler(callback: CallbackQuery) -> None:
        await admin_stats(callback, config.admin_id)

    dp.message.register(start_handler, CommandStart())
    dp.message.register(cancel, Command("cancel"))

    dp.callback_query.register(menu_handler, F.data == "menu")
    dp.callback_query.register(show_catalog, F.data == "catalog")
    dp.callback_query.register(show_category, F.data.startswith("cat:"))
    dp.callback_query.register(show_liquid_brand, F.data.startswith("liqbrand:"))
    dp.callback_query.register(show_accessory_type, F.data.startswith("acc:"))
    dp.callback_query.register(show_product, F.data.startswith("prod:"))
    dp.callback_query.register(add_to_cart, F.data.startswith("cart:add:"))
    dp.callback_query.register(show_cart, F.data == "cart")
    dp.callback_query.register(remove_from_cart, F.data.startswith("cart:remove:"))
    dp.callback_query.register(clear_cart, F.data == "cart:clear")
    dp.callback_query.register(ask_quantity, F.data.startswith("cart:qty:"))
    dp.callback_query.register(confirm_cart_handler, F.data == "cart:confirm")
    dp.callback_query.register(contact_handler, F.data == "contact")

    dp.callback_query.register(show_admin_handler, F.data == "admin")
    dp.callback_query.register(admin_add_start_handler, F.data == "admin:add")
    dp.callback_query.register(admin_photo_list_handler, F.data == "admin:photo")
    dp.callback_query.register(admin_photo_select_handler, F.data.startswith("admin:photo:select:"))
    dp.callback_query.register(admin_list_handler, F.data == "admin:list")
    dp.callback_query.register(admin_orders_handler, F.data == "admin:orders")
    dp.callback_query.register(custom_order_start_handler, F.data == "admin:custom")
    dp.callback_query.register(admin_stock_list_handler, F.data == "admin:stock")
    dp.callback_query.register(admin_stock_select_handler, F.data.startswith("admin:stock:select:"))
    dp.callback_query.register(admin_availability_handler, F.data == "admin:availability")
    dp.callback_query.register(admin_toggle_handler, F.data.startswith("admin:toggle:"))
    dp.callback_query.register(admin_delete_list_handler, F.data == "admin:delete")
    dp.callback_query.register(admin_delete_confirm_handler, F.data.startswith("admin:delete:confirm:"))
    dp.callback_query.register(admin_delete_do_handler, F.data.startswith("admin:delete:do:"))
    dp.callback_query.register(admin_issue_handler, F.data.startswith("admin:issue:"))
    dp.callback_query.register(admin_cancel_order_handler, F.data.startswith("admin:cancel_order:"))
    dp.callback_query.register(admin_price_start_handler, F.data.startswith("admin:price:"))
    dp.callback_query.register(admin_stats_handler, F.data == "admin:stats")

    dp.callback_query.register(add_product_type, AddProduct.product_type, F.data.startswith("add:type:"))
    dp.callback_query.register(add_accessory_type, AddProduct.accessory_type, F.data.startswith("addacc:"))
    dp.callback_query.register(skip_compatibility, AddProduct.compatibility, F.data == "add:skip:compatibility")
    dp.callback_query.register(skip_add_photo, AddProduct.photo, F.data == "add:skip:photo")
    dp.callback_query.register(save_preview_product, AddProduct.preview, F.data == "add:save")
    dp.callback_query.register(cancel_add_product, AddProduct.preview, F.data == "add:cancel")
    dp.message.register(add_name, AddProduct.name)
    dp.message.register(add_brand, AddProduct.brand)
    dp.message.register(add_flavor, AddProduct.flavor)
    dp.message.register(add_puffs, AddProduct.puffs)
    dp.message.register(add_strength, AddProduct.strength)
    dp.message.register(add_volume, AddProduct.volume)
    dp.message.register(add_resistance, AddProduct.resistance)
    dp.message.register(add_compatibility, AddProduct.compatibility)
    dp.message.register(add_price, AddProduct.price)
    dp.message.register(add_quantity, AddProduct.quantity)
    dp.message.register(add_photo, AddProduct.photo)
    dp.message.register(save_cart_add, CartAdd.amount)
    dp.message.register(save_quantity, QuantityEdit.amount)
    dp.message.register(save_product_photo, ProductPhoto.photo)
    dp.message.register(save_stock_quantity, StockEdit.quantity)
    dp.message.register(save_order_price, OrderPriceEdit.amount)
    dp.message.register(custom_order_user, CustomOrder.user_id)
    dp.message.register(custom_order_items, CustomOrder.items)
    dp.message.register(custom_order_total_handler, CustomOrder.total)

    await setup_bot_profile(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
