import os
import csv
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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
STATUS, SPECIALIZATION, PRODUCT_CHOICE = range(3)
PARTNER_STATUSES = ["Silver", "Gold", "Platinum"]
SPECIALIZATIONS = [
    "NDR", "EDR/XDR", "SIEM", "ICS", "KasperskyOS",
    "Threat intelligence", "CWP", "SASE", "Нет специализации"
]

# Загрузка базы продуктов из CSV
def load_products():
    products = []
    try:
        with open('products.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if all(v is None or v == '' for v in row.values()):
                    continue
                cleaned = {str(k).strip(): (str(v).strip() if v is not None else '') for k, v in row.items()}
                products.append(cleaned)
        for idx, prod in enumerate(products, start=1):
            prod['code'] = str(idx)
        logger.info(f"Загружено {len(products)} продуктов")
    except FileNotFoundError:
        logger.error("Файл products.csv не найден!")
    return products

PRODUCTS_DB = load_products()

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("👋 Я калькулятор рибейтов. Размер рибейта зависит от статуса компании и специализации. Нажмите /calculate")

async def calculate_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in PARTNER_STATUSES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Шаг 1: Выберите статус партнера:", reply_markup=reply_markup)
    return STATUS

async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['status'] = query.data
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in SPECIALIZATIONS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=f"✅ Статус: {query.data}\n\nШаг 2: Выберите специализацию:",
        reply_markup=reply_markup
    )
    return SPECIALIZATION

async def specialization_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    spec = query.data
    context.user_data['specialization'] = spec

    if spec == "Нет специализации":
        filtered = [p for p in PRODUCTS_DB if p['specialization_required'] == '']
    else:
        filtered = [p for p in PRODUCTS_DB if p['specialization_required'] == spec]

    if not filtered:
        await query.edit_message_text("❌ Нет продуктов для этой специализации. /calculate")
        return ConversationHandler.END

    context.user_data['product_list'] = filtered
    msg = "Шаг 3: Введите **код** продукта (число) для расчёта. Список доступных:\n\n"
    for prod in filtered:
        code = prod.get('code', '?')
        short_name = prod['product_name'][:40] + ('...' if len(prod['product_name']) > 40 else '')
        msg += f"`{code}` – {short_name}\n"
    msg += "\nВведите код (или /cancel для отмены)"
    await query.edit_message_text(msg, parse_mode='Markdown')
    return PRODUCT_CHOICE

async def product_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    product_list = context.user_data.get('product_list', [])
    if not product_list:
        await update.message.reply_text("❌ Сначала выберите специализацию через /calculate")
        return ConversationHandler.END

    found = None
    try:
        code = int(user_input)
        found = next((p for p in product_list if int(p.get('code', 0)) == code), None)
    except ValueError:
        for prod in product_list:
            if user_input.lower() in prod['product_name'].lower():
                found = prod
                break

    if found is None:
        await update.message.reply_text("❌ Не найдено. Введите числовой код из списка или /cancel")
        return PRODUCT_CHOICE

    status = context.user_data['status']
    spec = context.user_data['specialization']

    if status == "Gold":
        base = int(found['base_gold'])
    elif status == "Platinum":
        base = int(found['base_platinum'])
    elif status == "Silver":
        if found['product_group'] == "Strategic":
            base = 10
        elif found['product_group'] == "General":
            base = 5
        else:
            base = 0
    else:
        base = 0

    total = base
    required = found['specialization_required']
    product_group = found['product_group']
    msg = [
        f"📊 **{found['product_name']}**",
        f"Группа: {product_group}",
        f"Статус: {status}",
        f"Базовая ставка: {base}%"
    ]

    if required and spec != "Нет специализации" and required.lower() in spec.lower():
        acc = int(found['accelerator'])
        total += acc
        msg.append(f"Акселератор за специализацию {required}: +{acc}%")
    elif required:
        total += 12
        msg.append("Акселератор (без специализации): +12% (для 19% нужна специализация)")
    else:
        total += 10
        msg.append("Акселератор для General: +10%")

    msg.append(f"💰 **ИТОГОВЫЙ РИБЕЙТ: {total}%**")
    await update.message.reply_text("\n".join(msg), parse_mode='Markdown')
    await update.message.reply_text("Снова /calculate")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---------- Веб-сервер для Health Check и Webhook ----------
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

# ---------- Основная функция ----------
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
            PRODUCT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_choice)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    # ✅ КРИТИЧНЫЕ ШАГИ ИНИЦИАЛИЗАЦИИ
    await application.initialize()
    await application.start()

    # Сбрасываем старый вебхук и очередь обновлений
    await application.bot.delete_webhook(drop_pending_updates=True)

    # Регистрируем новый вебхук
    webhook_path = f"/{token}"
    await application.bot.set_webhook(
        url=f"{webhook_url}{webhook_path}",
        secret_token=secret_token
    )

    # Настраиваем веб-сервер
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

    # Держим приложение активным
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
