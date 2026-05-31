# DuckVape Telegram Bot

Telegram bot for product reservations with a Polish user interface, catalog, cart, orders, admin panel, stock management, product photos, and SQLite storage.

## Features

- Customer catalog: `Płyny`, `Jednorazówki`, `Akcesoria`
- Accessory subcatalog: `Kartridże / Pody`, `Urządzenia`, `Inne`
- Cart with editable quantities
- Order confirmation and admin notifications
- Admin panel:
  - add products
  - add product photos
  - list products
  - view, issue, or cancel orders
  - change product visibility
  - change stock quantity
  - create custom orders
  - view statistics
- Separate stock concepts:
  - `warehouse_products`: real physical stock
  - `client_products`: stock visible to customers
- Local SQLite database created automatically on first run

## Requirements

- Python 3.11+
- Telegram bot token from BotFather

## Setup

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create `.env` from the example:

```powershell
Copy-Item .env.example .env
```

4. Edit `.env`:

```env
ADMIN_ID=123456789
BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
```

To get your Telegram ID, message a bot such as `@userinfobot`.

## Run

```powershell
python .\main.py
```

The bot creates `shop.sqlite3` automatically in the project folder.

## GitHub Notes

Do not commit:

- `.env`
- `shop.sqlite3`
- `vendor/`
- `.venv/`
- Python cache folders

These are already covered by `.gitignore`.

## Deployment Notes

For a server or VPS:

1. Clone the repository.
2. Create `.env` from `.env.example`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run `python main.py`.

For production, run the bot with a process manager such as `systemd`, `pm2`, Docker, or your hosting provider's worker process.
