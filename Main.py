import os
import csv
import logging
import sys
import time
import traceback
from multiprocessing import Process
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, Filters
from telegram.error import Conflict

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Состояния диалога
STATUS, SPECIALIZATION, PRODUCT_CHOICE = range(3)
PARTNER_STATUSES = ["Silver", "Gold", "Platinum"]
SPECIALIZATIONS = ["NDR", "EDR/XDR", "SIEM", "ICS", "KasperskyOS", "Threat intelligence", "CWP", "SASE", "Нет специализации"]

# Загрузка CSV
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
    except Exception as e:
        logger.error(f"Ошибка загрузки products.csv: {e}")
        traceback.print_exc()
    return products

PRODUCTS_DB = load_products()

# ------------- Обработчики -------------
def start(update, context):
    update.message.reply_text("👋 Я калькулятор рибейтов. Нажмите /calculate")

def calculate_start(update, context):
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in PARTNER_STATUSES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Шаг 1: Выберите статус партнера:", reply_markup=reply_markup)
    return STATUS

def status_callback(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['status'] = query.data
    keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in SPECIALIZATIONS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(
        text=f"✅ Статус: {query.data}\n\nШаг 2: Выберите специализацию:",
        reply_markup=reply_markup
    )
    return SPECIALIZATION

def specialization_callback(update, context):
    query = update.callback_query
    query.answer()
    spec = query.data
    context.user_data['specialization'] = spec

    if spec == "Нет специализации":
        filtered = [p for p in PRODUCTS_DB if p['specialization_required'] == '']
    else:
        filtered = [p for p in PRODUCTS_DB if p['specialization_required'] == spec]

    if not filtered:
        query.edit_message_text("❌ Нет продуктов для этой специализации. /calculate")
        return ConversationHandler.END

    context.user_data['product_list'] = filtered
    msg = "Шаг 3: Введите **код** продукта (число) для расчёта. Список доступных:\n\n"
    for prod in filtered:
        code = prod.get('code', '?')
        short_name = prod['product_name'][:40] + ('...' if len(prod['product_name']) > 40 else '')
        msg += f"`{code}` – {short_name}\n"
    msg += "\nВведите код (или /cancel для отмены)"
    query.edit_message_text(msg, parse_mode='Markdown')
    return PRODUCT_CHOICE

def product_choice(update, context):
    user_input = update.message.text.strip()
    product_list = context.user_data.get('product_list', [])
    if not product_list:
        update.message.reply_text("❌ Сначала выберите специализацию через /calculate")
        return ConversationHandler.END

    # Поиск по коду
    found = None
    try:
        code = int(user_input)
        found = next((p for p in product_list if int(p.get('code', 0)) == code), None)
    except ValueError:
        # Если не число, ищем по вхождению в название
        for prod in product_list:
            if user_input.lower() in prod['product_name'].lower():
                found = prod
                break

    if found is None:
        update.message.reply_text("❌ Не найдено. Введите числовой код из списка или /cancel")
        return PRODUCT_CHOICE

    status = context.user_data['status']
    spec = context.user_data['specialization']

    # Базовая ставка
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
    update.message.reply_text("\n".join(msg), parse_mode='Markdown')
    update.message.reply_text("Снова /calculate")
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ------------- Веб-сервер для Render -------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_web():
    try:
        port = int(os.environ.get('PORT', 8080))
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        logger.info(f"Веб-сервер запущен на порту {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка веб-сервера: {e}")
        traceback.print_exc()

# ------------- Основная логика с обработкой конфликта -------------
def start_bot():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        logger.error("BOT_TOKEN не задан")
        return

    # Пытаемся запустить бота с обработкой конфликта
    while True:
        try:
            logger.info("Запуск бота...")
            updater = Updater(token, use_context=True)
            dp = updater.dispatcher

            # Добавляем обработчик ошибок, чтобы логировать исключения
            def error_handler(update, context):
                logger.error(f"Обновление {update} вызвало ошибку: {context.error}")
                traceback.print_exc()
            dp.add_error_handler(error_handler)

            dp.add_handler(CommandHandler("start", start))
            conv_handler = ConversationHandler(
                entry_points=[CommandHandler('calculate', calculate_start)],
                states={
                    STATUS: [CallbackQueryHandler(status_callback)],
                    SPECIALIZATION: [CallbackQueryHandler(specialization_callback)],
                    PRODUCT_CHOICE: [MessageHandler(Filters.text & ~Filters.command, product_choice)],
                },
                fallbacks=[CommandHandler('cancel', cancel)],
            )
            dp.add_handler(conv_handler)

            # Запускаем polling с clean=True, чтобы сбросить старые обновления
            updater.start_polling(clean=True)
            logger.info("Бот запущен и готов к работе")
            updater.idle()
            # Если вышли из idle (т.е. бот остановлен), завершаем цикл
            break
        except Conflict as e:
            logger.warning(f"Конфликт: {e}. Ждём 5 секунд и пробуем снова...")
            time.sleep(5)
            # Попытка перезапустить, предварительно остановив старый инстанс
            try:
                updater.stop()
                logger.info("Старый инстанс остановлен")
            except:
                pass
            continue
        except Exception as e:
            logger.critical(f"Критическая ошибка: {e}")
            traceback.print_exc()
            break

if __name__ == '__main__':
    # Запускаем веб-сервер в отдельном процессе
    web_process = Process(target=run_web, daemon=False)
    web_process.start()
    logger.info("Веб-сервер запущен в отдельном процессе")

    # Запускаем бота
    start_bot()

    # Если бот завершился, завершаем веб-процесс
    if web_process.is_alive():
        web_process.terminate()
        web_process.join()