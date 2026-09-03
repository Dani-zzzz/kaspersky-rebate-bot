import os
import csv
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, MessageHandler, ConversationHandler, Filters
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

STATUS, SPECIALIZATION, PRODUCT = range(3)
PARTNER_STATUSES = ["Gold", "Platinum"]
SPECIALIZATIONS = ["NDR", "EDR/XDR", "SIEM", "ICS", "KasperskyOS", "Threat intelligence", "CWP", "SASE", "Нет специализации"]

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
        logging.info(f"Загружено {len(products)} продуктов")
    except FileNotFoundError:
        logging.error("products.csv не найден")
    return products

PRODUCTS_DB = load_products()

def start(update, context):
    update.message.reply_text("👋 Я калькулятор рибейтов. Нажмите /calculate")

def calculate_start(update, context):
    keyboard = [[s] for s in PARTNER_STATUSES]
    update.message.reply_text("Выберите статус:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return STATUS

def select_status(update, context):
    context.user_data['status'] = update.message.text
    keyboard = [[s] for s in SPECIALIZATIONS]
    update.message.reply_text("Выберите специализацию:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return SPECIALIZATION

def select_specialization(update, context):
    context.user_data['specialization'] = update.message.text
    update.message.reply_text("Введите название продукта и тип лицензии:", reply_markup=ReplyKeyboardRemove())
    return PRODUCT

def calculate_rebate(update, context):
    query = update.message.text.lower()
    status = context.user_data['status']
    spec = context.user_data['specialization']
    found = next((p for p in PRODUCTS_DB if p['product_name'].lower() in query), None)
    if not found:
        update.message.reply_text("Продукт не найден. /calculate")
        return ConversationHandler.END
    base = int(found['base_gold' if status == 'Gold' else 'base_platinum'])
    total = base
    required = found['specialization_required']
    msg = [f"📊 **{found['product_name']}**", f"Группа: {found['product_group']}", f"Статус: {status}", f"Базовая ставка: {base}%"]
    if required and spec != "Нет специализации" and required.lower() in spec.lower():
        acc = int(found['accelerator'])
        total += acc
        msg.append(f"Акселератор за специализацию: +{acc}%")
    elif required:
        total += 12
        msg.append("Акселератор (без специализации): +12% (для 19% нужна специализация)")
    else:
        total += 10
        msg.append("Акселератор для General: +10%")
    msg.append(f"💰 **ИТОГ: {total}%**")
    update.message.reply_text("\n".join(msg), parse_mode='Markdown')
    update.message.reply_text("Снова /calculate")
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Веб-сервер
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_web():
    port = int(os.environ.get('PORT', 8080))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()

def main():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise ValueError("BOT_TOKEN не задан")
    updater = Updater(token, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('calculate', calculate_start)],
        states={
            STATUS: [MessageHandler(Filters.text & ~Filters.command, select_status)],
            SPECIALIZATION: [MessageHandler(Filters.text & ~Filters.command, select_specialization)],
            PRODUCT: [MessageHandler(Filters.text & ~Filters.command, calculate_rebate)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    ))
    logging.info("Бот запущен")
    threading.Thread(target=run_web, daemon=True).start()
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
