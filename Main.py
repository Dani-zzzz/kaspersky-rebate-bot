import os
import csv
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, MessageHandler, ConversationHandler
from telegram.ext import filters
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Состояния диалога
STATUS, SPECIALIZATION, PRODUCT = range(3)

# Списки для выбора
PARTNER_STATUSES = ["Gold", "Platinum"]
SPECIALIZATIONS = [
    "NDR", "EDR/XDR", "SIEM", "ICS", "KasperskyOS",
    "Threat intelligence", "CWP", "SASE", "Нет специализации"
]

# Загрузка базы продуктов из CSV
def load_products():
    products = []
    try:
        with open('products.csv', 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if all(v is None or v == '' for v in row.values()):
                    continue
                cleaned_row = {}
                for k, v in row.items():
                    key = str(k).strip()
                    value = str(v).strip() if v is not None else ''
                    cleaned_row[key] = value
                products.append(cleaned_row)
        logging.info(f"Загружено {len(products)} продуктов из CSV")
    except FileNotFoundError:
        logging.error("Файл products.csv не найден!")
    return products

PRODUCTS_DB = load_products()

# Команда /start
def start(update: Update, context):
    update.message.reply_text(
        "👋 Добро пожаловать в калькулятор рибейтов Kaspersky!\n\n"
        "Я помогу рассчитать размер премии за продажу продуктов.\n"
        "Нажмите /calculate для начала расчета или /cancel для отмены."
    )

# Начало расчёта
def calculate_start(update: Update, context):
    reply_keyboard = [[s] for s in PARTNER_STATUSES]
    update.message.reply_text(
        "Шаг 1 из 3: Выберите ваш статус партнера:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return STATUS

# Выбор статуса
def select_status(update: Update, context):
    context.user_data['status'] = update.message.text
    reply_keyboard = [[s] for s in SPECIALIZATIONS]
    update.message.reply_text(
        f"✅ Статус: {update.message.text}\n\n"
        "Шаг 2 из 3: Выберите специализацию (или 'Нет специализации'):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SPECIALIZATION

# Выбор специализации
def select_specialization(update: Update, context):
    context.user_data['specialization'] = update.message.text
    update.message.reply_text(
        f"✅ Статус: {context.user_data['status']}\n"
        f"✅ Специализация: {update.message.text}\n\n"
        "Шаг 3 из 3: Введите полное наименование продукта и тип лицензии\n"
        "Пример: Kaspersky Anti Targeted Attack Platform Advanced. 100-149 Node 1 year Base License",
        reply_markup=ReplyKeyboardRemove()
    )
    return PRODUCT

# Расчёт рибейта
def calculate_rebate(update: Update, context):
    product_query = update.message.text.lower()
    status = context.user_data['status']
    specialization = context.user_data['specialization']

    found_product = None
    for product in PRODUCTS_DB:
        if product['product_name'].lower() in product_query:
            found_product = product
            break

    if not found_product:
        update.message.reply_text(
            "❌ Продукт не найден в базе.\n\n"
            "Попробуйте ввести точное название или начните заново: /calculate"
        )
        return ConversationHandler.END

    base_rate = int(found_product['base_gold'] if status == "Gold" else found_product['base_platinum'])
    product_group = found_product['product_group']
    required_spec = found_product['specialization_required']

    total = base_rate
    explanation = []
    explanation.append(f"📊 **Результат расчета**\n")
    explanation.append(f"Продукт: {found_product['product_name']}")
    explanation.append(f"Группа: {product_group}")
    explanation.append(f"Статус: {status}")
    explanation.append(f"\n1️⃣ Базовая квартальная премия:")
    explanation.append(f"   • Для {status} партнера ({product_group}): {base_rate}%")

    if required_spec and specialization != "Нет специализации" and required_spec.lower() in specialization.lower():
        accelerator = int(found_product['accelerator'])
        explanation.append(f"\n2️⃣ Акселератор за специализацию {required_spec}:")
        explanation.append(f"   • +{accelerator}% (продукт соответствует специализации)")
        total += accelerator
    elif required_spec:
        explanation.append(f"\n2️⃣ Акселератор за продажу новых продуктов (без специализации):")
        explanation.append(f"   • Стандартный акселератор для {product_group}: 12%")
        explanation.append(f"   • (Для получения 19% требуется специализация {required_spec})")
        total += 12
    else:
        explanation.append(f"\n2️⃣ Акселератор за новые продажи:")
        explanation.append(f"   • Для {product_group} продуктов: 10%")
        total += 10

    explanation.append(f"\n💰 **ИТОГОВЫЙ РИБЕЙТ: {total}%**")
    update.message.reply_text("\n".join(explanation), parse_mode='Markdown')
    update.message.reply_text("Хотите рассчитать еще один продукт? /calculate")
    return ConversationHandler.END

# Отмена
def cancel(update: Update, context):
    update.message.reply_text("Расчет отменен.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ============ Веб-сервер для проверки здоровья ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_web():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# ============ Основная функция ============
def main():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise ValueError("Не задан BOT_TOKEN в переменных окружения!")

    updater = Updater(token=token, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('calculate', calculate_start)],
        states={
            STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_status)],
            SPECIALIZATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_specialization)],
            PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_rebate)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    dp.add_handler(conv_handler)

    logging.info("Бот запущен и готов к работе")

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
