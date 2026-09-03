import os
import csv
import logging
from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, Filters
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

STATUS, SPECIALIZATION, PRODUCT = range(3)
PARTNER_STATUSES = ["Silver", "Gold", "Platinum"]  # <-- добавлен Silver
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
    query.edit_message_text(text=f"✅ Статус: {query.data}\n\nШаг 2: Выберите специализацию:", reply_markup=reply_markup)
    return SPECIALIZATION

def specialization_callback(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['specialization'] = query.data
    query.edit_message_text(
        text=f"✅ Статус: {context.user_data['status']}\n✅ Специализация: {query.data}\n\n"
             "Шаг 3: Введите полное название продукта и тип лицензии\n"
             "Пример: Kaspersky Anti Targeted Attack Platform Advanced. 100-149 Node 1 year Base License",
        reply_markup=None
    )
    return PRODUCT

def product_input(update, context):
    query = update.message.text.lower()
    status = context.user_data['status']
    spec = context.user_data['specialization']
    found = next((p for p in PRODUCTS_DB if p['product_name'].lower() in query), None)
    if not found:
        update.message.reply_text("❌ Продукт не найден. Попробуйте ещё раз или /calculate")
        return PRODUCT

    # Определяем базовую ставку в зависимости от статуса
    if status == "Gold":
        base = int(found['base_gold'])
    elif status == "Platinum":
        base = int(found['base_platinum'])
    elif status == "Silver":
        # Для Silver базовая ставка для Strategic продуктов – 10%
        # (для General было бы 5%, но у нас только Strategic)
        base = 10
    else:
        base = 0

    total = base
    required = found['specialization_required']
    product_group = found['product_group']
    msg = [f"📊 **{found['product_name']}**", f"Группа: {product_group}", f"Статус: {status}", f"Базовая ставка: {base}%"]

    # Акселераторы
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

# Веб-сервер для Render
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

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('calculate', calculate_start)],
        states={
            STATUS: [CallbackQueryHandler(status_callback)],
            SPECIALIZATION: [CallbackQueryHandler(specialization_callback)],
            PRODUCT: [MessageHandler(Filters.text & ~Filters.command, product_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    dp.add_handler(conv_handler)

    logging.info("Бот запущен")
    threading.Thread(target=run_web, daemon=True).start()
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
