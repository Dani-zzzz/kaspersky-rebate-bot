import os
import re
import logging
import asyncio
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
STATUS, SPECIALIZATION, DEAL_TYPE, SUPPLIED, PRODUCT_NUMBER = range(5)

PARTNER_STATUSES = ["Silver", "Gold", "Platinum"]
SPECIALIZATIONS = [
    "NDR", "EDR/XDR", "SIEM", "ICS", "KasperskyOS",
    "Threat intelligence", "CWP", "SASE", "Нет специализации"
]

# Базовые ставки (Таблицы 1 и 2)
BASE_RATES = {
    "Strategic": {"Silver": 10, "Gold": 15, "Platinum": 18},
    "General":   {"Silver": 5,  "Gold": 10, "Platinum": 12},
}

# Акселераторы (Таблицы 4 и 5)
ACC_NEW_GENERAL = 10
ACC_NEW_STRATEGIC = 12
ACC_NEW_STRATEGIC_SPEC = 19
ACC_RENEWAL_GENERAL = 4
ACC_RENEWAL_STRATEGIC = 6


def load_products():
    """Загружает Strategic-продукты из products.csv (tab-separated)."""
    products = {}
    try:
        with open('products.csv', 'r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.rstrip('\n').rstrip('\r')
                if not line.strip():
                    continue
                line = line.strip()
                if line.startswith('"'):
                    line = line[1:]
                if line.endswith('"'):
                    line = line[:-1]

                # Ищем 4-значный код в конце
                match = re.search(r'(\d{4})\s*$', line)
                if not match:
                    continue
                code = match.group(1)

                rest = line[:match.start()].rstrip()
                rest = rest.strip().strip('"').strip()

                parts = rest.split('\t')
                if len(parts) < 2:
                    continue

                spec = parts[0].strip().strip('"').strip()
                name = parts[1].strip().strip('"').strip()

                # Пропускаем строку-заголовок
                if 'Product code' in name or 'Сейчас' in name:
                    continue

                products[code] = {
                    'specialization': spec,
                    'name': name,
                }
        logger.info(f"Загружено {len(products)} Strategic продуктов")
    except FileNotFoundError:
        logger.error("Файл products.csv не найден!")
    return products


PRODUCTS_DB = load_products()


# ---------- Обработчики ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Я калькулятор рибейтов. Размер рибейта зависит от статуса компании "
        "и специализации. Нажмите /calculate"
    )


async def calculate_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in PARTNER_STATUSES]
    await update.message.reply_text(
        "Шаг 1: Выберите статус партнера:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return STATUS


async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['status'] = query.data
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in SPECIALIZATIONS]
    await query.edit_message_text(
        text=f"✅ Статус: {query.data}\n\nШаг 2: Выберите специализацию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SPECIALIZATION


async def specialization_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['specialization'] = query.data
    keyboard = [
        [InlineKeyboardButton("Новая продажа", callback_data="new")],
        [InlineKeyboardButton("Продление", callback_data="renewal")],
    ]
    await query.edit_message_text(
        text=f"✅ Статус: {context.user_data['status']}\n"
             f"✅ Специализация: {query.data}\n\n"
             "Шаг 3: Тип сделки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DEAL_TYPE


async def deal_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['deal_type'] = query.data

    if query.data == "new":
        context.user_data['supplied'] = None
        await query.edit_message_text(
            text=f"✅ Статус: {context.user_data['status']}\n"
                 f"✅ Специализация: {context.user_data['specialization']}\n"
                 f"✅ Тип сделки: Новая продажа\n\n"
                 "Шаг 4: Введите 4-значный номер продукта:"
        )
        return PRODUCT_NUMBER
    else:
        keyboard = [
            [InlineKeyboardButton("Да", callback_data="yes")],
            [InlineKeyboardButton("Нет", callback_data="no")],
        ]
        await query.edit_message_text(
            text=f"✅ Статус: {context.user_data['status']}\n"
                 f"✅ Специализация: {context.user_data['specialization']}\n"
                 f"✅ Тип сделки: Продление\n\n"
                 "Шаг 4: Продукт поставлялся вашей компанией в прошлый период?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SUPPLIED


async def supplied_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['supplied'] = (query.data == "yes")
    await query.edit_message_text(
        text=f"✅ Статус: {context.user_data['status']}\n"
             f"✅ Специализация: {context.user_data['specialization']}\n"
             f"✅ Тип сделки: Продление\n"
             f"✅ Поставлен ранее: {'Да' if context.user_data['supplied'] else 'Нет'}\n\n"
             "Шаг 5: Введите 4-значный номер продукта:"
    )
    return PRODUCT_NUMBER


async def product_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()

    if not user_input.isdigit() or len(user_input) < 3 or len(user_input) > 4:
        await update.message.reply_text("❌ Введите 4-значный номер продукта (только цифры).")
        return PRODUCT_NUMBER

    status = context.user_data['status']
    user_spec = context.user_data['specialization']
    deal_type = context.user_data['deal_type']
    supplied = context.user_data.get('supplied')

    product = PRODUCTS_DB.get(user_input)

    if product:
        product_group = "Strategic"
        spec_match = (user_spec == product['specialization'])
    else:
        product_group = "General"
        spec_match = False

    base = BASE_RATES[product_group][status]
    accelerator = 0

    if deal_type == "new":
        if product_group == "Strategic":
            accelerator = ACC_NEW_STRATEGIC_SPEC if spec_match else ACC_NEW_STRATEGIC
        else:
            accelerator = ACC_NEW_GENERAL
    else:  # Продление
        if supplied:
            accelerator = ACC_RENEWAL_STRATEGIC if product_group == "Strategic" else ACC_RENEWAL_GENERAL
        else:
            accelerator = 0

    total = base + accelerator

    await update.message.reply_text(
        f"Рибейт при продаже этого продукта составит {total}%."
    )
    await update.message.reply_text("Снова /calculate")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------- Веб-сервер ----------

async def healthcheck(request):
    return web.Response(text="OK")


async def webhook(request):
    application = request.app['telegram_app']
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
    return web.Response(text="OK")


async def main():
    token = os.environ.get('BOT_TOKEN')
    webhook_url = os.environ.get('WEBHOOK_URL')
    secret_token = os.environ.get('WEBHOOK_SECRET')

    if not token or not webhook_url:
        logger.error("Не заданы переменные BOT_TOKEN или WEBHOOK_URL!")
        return

    application = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('calculate', calculate_start)],
        states={
            STATUS: [CallbackQueryHandler(status_callback)],
            SPECIALIZATION: [CallbackQueryHandler(specialization_callback)],
            DEAL_TYPE: [CallbackQueryHandler(deal_type_callback)],
            SUPPLIED: [CallbackQueryHandler(supplied_callback)],
            PRODUCT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_number_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    await application.initialize()
    await application.start()
    await application.bot.delete_webhook(drop_pending_updates=True)

    webhook_path = f"/{token}"
    await application.bot.set_webhook(
        url=f"{webhook_url}{webhook_path}",
        secret_token=secret_token
    )

    app = web.Application()
    app['telegram_app'] = application
    app.router.add_get('/healthcheck', healthcheck)
    app.router.add_post(webhook_path, webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    logger.info(f"Веб-сервер запущен на порту {port}")
    logger.info(f"Вебхук зарегистрирован: {webhook_url}{webhook_path}")

    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())