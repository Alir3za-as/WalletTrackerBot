from dotenv import load_dotenv
import os
load_dotenv()
import logging
import sqlite3
import os
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, filters
import requests

# تنظیمات لاگینگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# توکن ربات تلگرام خود را در اینجا وارد کنید
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.critical("توکن ربات تلگرام وارد نشده است. برنامه متوقف شد.")
    exit()
else:
    logger.info("توکن ربات تلگرام با موفقیت خوانده شد.")

# کلیدهای API بلاکچین (به صورت امن ذخیره کنید!)
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY')
BSCSCAN_API_KEY = os.getenv('BSCSCAN_API_KEY')
SOLSCAN_API_KEY = os.getenv('SOLSCAN_API_KEY')

# نام پایگاه داده SQLite
DATABASE_NAME = 'wallet_tracker.db'

# --- توابع مربوط به پایگاه داده SQLite ---
def create_connection():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        logger.info(f"اتصال به پایگاه داده {DATABASE_NAME} برقرار شد.")
        return conn
    except sqlite3.Error as e:
        logger.error(f"خطا در اتصال به پایگاه داده: {e}")
    return conn

def create_tables():
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        # جدول کیف‌پول‌ها
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                network TEXT NOT NULL,
                name TEXT,
                UNIQUE (user_id, address, network)
            )
        """)
        # جدول جدید برای ذخیره دارایی‌های قبلی
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                network TEXT NOT NULL,
                token_symbol TEXT NOT NULL,
                balance REAL NOT NULL,
                UNIQUE (user_id, address, network, token_symbol)
            )
        """)
        conn.commit()
        conn.close()
        logger.info("جداول wallets و token_balances ایجاد یا بررسی شدند.")
    else:
        logger.error("عدم اتصال به پایگاه داده، جداول ایجاد نشدند.")

def add_wallet_db(user_id, wallet_address, network, name=None):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO wallets (user_id, address, network, name) VALUES (?, ?, ?, ?)", (user_id, wallet_address, network, name))
            conn.commit()
            conn.close()
            logger.info(f"کیف پول {wallet_address} در شبکه {network} با نام '{name}' برای کاربر {user_id} ذخیره شد.")
            return True
        except sqlite3.IntegrityError:
            conn.close()
            logger.warning(f"کیف پول {wallet_address} در شبکه {network} قبلاً برای کاربر {user_id} ذخیره شده است.")
            return False
    return False

def remove_wallet_db(user_id, wallet_address, network):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM wallets WHERE user_id = ? AND address = ? AND network = ?", (user_id, wallet_address, network))
        cursor.execute("DELETE FROM token_balances WHERE user_id = ? AND address = ? AND network = ?", (user_id, wallet_address, network))
        conn.commit()
        conn.close()
        if cursor.rowcount > 0:
            logger.info(f"کیف پول {wallet_address} در شبکه {network} برای کاربر {user_id} حذف شد.")
            return True
        else:
            logger.warning(f"کیف پول {wallet_address} در شبکه {network} برای کاربر {user_id} یافت نشد.")
            return False
    return False

def get_wallets_db(user_id):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT address, network, name FROM wallets WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    return []

def save_token_balances(user_id, address, network, tokens):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        for symbol, balance in tokens.items():
            cursor.execute("""
                INSERT OR REPLACE INTO token_balances (user_id, address, network, token_symbol, balance)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, address, network, symbol, balance))
        conn.commit()
        conn.close()
        logger.info(f"دارایی‌های کیف پول {address} در شبکه {network} برای کاربر {user_id} ذخیره شد.")

def get_previous_balances(user_id, address, network):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT token_symbol, balance FROM token_balances WHERE user_id = ? AND address = ? AND network = ?",
                       (user_id, address, network))
        rows = cursor.fetchall()
        conn.close()
        return {symbol: balance for symbol, balance in rows}
    return {}

# --- توابع دریافت اطلاعات از بلاکچین ---
def get_eth_tokens(address):
    url = f"https://api.etherscan.io/api?module=account&action=tokentx&address={address}&sort=desc&apikey={ETHERSCAN_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data['status'] == '1':
            tokens = {}
            for tx in data['result']:
                token_symbol = tx['tokenSymbol']
                token_decimal = int(tx['tokenDecimal'])
                token_value = int(tx['value']) / (10 ** token_decimal)
                tokens[token_symbol] = tokens.get(token_symbol, 0) + token_value
            return tokens
        else:
            logger.warning(f"Etherscan API error: {data['message']}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching ETH tokens: {e}")
        return None

def get_bsc_tokens(address):
    url = f"https://api.bscscan.com/api?module=account&action=tokentx&address={address}&sort=desc&apikey={BSCSCAN_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data['status'] == '1':
            tokens = {}
            for tx in data['result']:
                token_symbol = tx['tokenSymbol']
                token_decimal = int(tx['tokenDecimal'])
                token_value = int(tx['value']) / (10 ** token_decimal)
                tokens[token_symbol] = tokens.get(token_symbol, 0) + token_value
            return tokens
        else:
            logger.warning(f"BscScan API error: {data['message']}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching BSC tokens: {e}")
        return None

def get_sol_tokens(address):
    url = f"https://api.solscan.io/account/tokens?address={address}&limit=50"
    headers = {'accept': 'application/json', 'content-type': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        tokens = {}
        for token_info in data:
            token_symbol = token_info['tokenName']
            token_decimal = token_info['decimals']
            token_amount = token_info['amount'] / (10 ** token_decimal)
            tokens[token_symbol] = token_amount
        return tokens
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching SOL tokens: {e}")
        return None

# --- تابع بررسی خودکار دارایی‌ها ---
async def check_wallets_auto(context: CallbackContext):
    conn = create_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM wallets")
        user_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        for user_id in user_ids:
            wallets = get_wallets_db(user_id)
            for address, network, name in wallets:
                previous_balances = get_previous_balances(user_id, address, network)
                current_tokens = None
                if network == 'eth':
                    current_tokens = get_eth_tokens(address)
                elif network == 'bsc':
                    current_tokens = get_bsc_tokens(address)
                elif network == 'sol':
                    current_tokens = get_sol_tokens(address)

                if current_tokens:
                    changes = []
                    for symbol, balance in current_tokens.items():
                        prev_balance = previous_balances.get(symbol, 0)
                        if symbol not in previous_balances:
                            changes.append(f"توکن جدید: {symbol} ({balance:.4f})")
                        elif balance > prev_balance:
                            diff = balance - prev_balance
                            changes.append(f"افزایش: {symbol} (+{diff:.4f})")

                    if changes:
                        message = f"🔍 تغییرات در کیف پول {name} ({network.upper()}):\n" + "\n".join(changes)
                        await context.bot.send_message(chat_id=user_id, text=message)
                    save_token_balances(user_id, address, network, current_tokens)

# --- مدیریت کیف پول‌ها ---
async def start_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    await update.message.reply_text("سلام! به ربات مدیریت کیف پول خوش آمدید.")

async def add_wallet_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if context.args:
        wallet_address = context.args[0]
        context.user_data['wallet_address_to_add'] = wallet_address
        await update.message.reply_text("لطفاً یک نام برای این کیف پول وارد کنید:")
        return
    else:
        await update.message.reply_text(⚠️ لطفاً آدرس کیف پول را وارد کنید. مثال: `/add <آدرس_کیف_پول>`")

async def name_wallet_message(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    name = update.message.text
    wallet_address = context.user_data.get('wallet_address_to_add')
    if wallet_address:
        networks = ['eth', 'bsc', 'sol']
        added_count = 0
        for network in networks:
            if add_wallet_db(user_id, wallet_address, network, name):
                added_count += 1
        if added_count > 0:
            await update.message.reply_text(f"✅ کیف پول {wallet_address} با نام '{name}' برای شبکه‌های Ethereum, Binance Smart Chain و Solana اضافه شد.")
        else:
            await update.message.reply_text(f"❌ کیف پول {wallet_address} قبلاً برای همه شبکه‌ها اضافه شده است.")
        del context.user_data['wallet_address_to_add']
        return
    else:
        await update.message.reply_text("خطا: آدرس کیف پول پیدا نشد. لطفاً دوباره از دستور `/add` استفاده کنید.")

async def remove_wallet_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if context.args:
        wallet_address = context.args[0]
        networks = ['eth', 'bsc', 'sol']
        removed_count = 0
        for network in networks:
            if remove_wallet_db(user_id, wallet_address, network):
                removed_count += 1
        if removed_count > 0:
            await update.message.reply_text(f"✅ کیف پول {wallet_address} از لیست پیگیری برای شبکه‌های Ethereum, Binance Smart Chain و Solana حذف شد.")
        else:
            await update.message.reply_text(f"❌ کیف پول {wallet_address} در هیچ یک از شبکه‌ها یافت نشد.")
    else:
        await update.message.reply_text("⚠️ لطفاً آدرس کیف پول را وارد کنید. مثال: `/remove <آدرس_کیف_پول>`")

async def list_wallets_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    wallets = get_wallets_db(user_id)
    if wallets:
        message = "📜 کیف پول‌های ذخیره شده:\n"
        for address, network, name in wallets:
            message += f"- {name}: {address} ({network})\n"
        await update.message.reply_text(message)
    else:
        await update.message.reply_text("❌ هیچ کیف پولی ذخیره نشده است.")

async def check_wallets_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    wallets = get_wallets_db(user_id)
    if not wallets:
        await update.message.reply_text("❌ هیچ کیف پولی برای بررسی وجود ندارد. ابتدا کیف پول اضافه کنید.")
        return

    message = "🔍 بررسی دارایی‌های شما:\n"
    for address, network, name in wallets:
        message += f"\n--- {name} ({network.upper()}) ---\n"
        if network == 'eth':
            tokens = get_eth_tokens(address)
        elif network == 'bsc':
            tokens = get_bsc_tokens(address)
        elif network == 'sol':
            tokens = get_sol_tokens(address)
        else:
            tokens = None

        if tokens:
            for symbol, balance in tokens.items():
                message += f"{symbol}: {balance:.4f}\n"
            save_token_balances(user_id, address, network, tokens)
        else:
            message += "خطا در دریافت اطلاعات.\n"

    await update.message.reply_text(message)

async def help_command(update: Update, context: CallbackContext):
    message = """
📃 لیست دستورات:
/start - شروع ربات
/add <آدرس_کیف_پول> - اضافه کردن یک کیف پول برای پیگیری در همه شبکه‌ها (درخواست نام بعد از آدرس)
/remove <آدرس_کیف_پول> - حذف یک کیف پول از لیست پیگیری در همه شبکه‌ها
/list - نمایش لیست کیف پول‌های ذخیره شده به همراه نام و شبکه
/check - بررسی دارایی‌های کیف پول‌های ذخیره شده به همراه نام
/help - راهنما
"""
    await update.message.reply_text(message)

async def set_commands(app: Application):
    my_commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("add", "اضافه کردن کیف پول"),
        BotCommand("remove", "حذف کیف پول"),
        BotCommand("list", "لیست کیف پول‌ها"),
        BotCommand("check", "بررسی دارایی‌ها"),
        BotCommand("help", "راهنما")
    ]
    await app.bot.set_my_commands(my_commands)
    logger.info("منوی دستورات ربات تنظیم شد.")

def main():
    logger.info("شروع تابع main برای راه‌اندازی ربات...")
    create_tables()  # ایجاد جداول پایگاه داده در صورت نیاز

    # توکن بات را وارد کنید
    application = Application.builder().token('7838145237:AAHzvq7bM-pVfBSaU5aVUhqpgyNyZVG02w4').build()
    logger.info("Application builder ساخته شد.")

    # تنظیم منوی دستورات
    application.job_queue.run_once(set_commands, when=0)
    # تنظیم بررسی خودکار هر 20 دقیقه (1200 ثانیه)
    application.job_queue.run_repeating(check_wallets_auto, interval=1200, first=10)

    # افزودن هندلرها به بات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_wallet_command))
    application.add_handler(CommandHandler("remove", remove_wallet_command))
    application.add_handler(CommandHandler("list", list_wallets_command))
    application.add_handler(CommandHandler("check", check_wallets_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, name_wallet_message))

    logger.info("هندلرها به ربات اضافه شدند.")

    # اجرای polling به صورت همزمان
    logger.info("ربات در حال polling...")
    application.run_polling()
    logger.info("Polling ربات آغاز شد.")

if __name__ == '__main__':
    main()