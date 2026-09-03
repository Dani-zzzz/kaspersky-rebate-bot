import os
import csv
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Специальная библиотека для поддержки жизни на Koyeb
import staypresent

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
                # Очищаем ключи и значения от пробелов
                row = {k.strip(): v.strip() for k, v in row.items()}
                products.append(row)
        logging.info(f"Загружено {len(products)} продуктов из CSV")
    except FileNotFoundError:
        logging.error("Файл products.csv не найден!")
    return products

PRODUCTS_DB = load_products()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в калькулятор рибейтов Kaspersky!\n\n"
        "Я помогу рассчитать размер премии за продажу продуктов.\n"
        "Нажмите /calculate для начала расчета или /cancel для отмены."
    )

# Начало расчёта
async def calculate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [[s] for s in PARTNER_STATUSES]
    await update.message.reply_text(
        "Шаг 1 из 3: Выберите ваш статус партнера:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return STATUS

# Выбор статуса
async def select_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['status'] = update.message.text
    reply_keyboard = [[s] for s in SPECIALIZATIONS]
    await update.message.reply_text(
        f"✅ Статус: {update.message.text}\n\n"
        "Шаг 2 из 3: Выберите специализацию (или 'Нет специализации'):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SPECIALIZATION

# Выбор специализации
async def select_specialization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['specialization'] = update.message.text
    await update.message.reply_text(
        f"✅ Статус: {context.user_data['status']}\n"
        f"✅ Специализация: {update.message.text}\n\n"
        "Шаг 3 из 3: Введите полное наименование продукта и тип лицензии\n"
        "Пример: Kaspersky Anti Targeted Attack Platform Advanced. 100-149 Node 1 year Base License",
        reply_markup=ReplyKeyboardRemove()
    )
    return PRODUCT

# Расчёт рибейта
async def calculate_rebate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_query = update.message.text.lower()
    status = context.user_data['status']
    specialization = context.user_data['specialization']

    # Поиск продукта в базе (первое совпадение)
    found_product = None
    for product in PRODUCTS_DB:
        if product['product_name'].lower() in product_query:
            found_product = product
            break

    if not found_product:
        await update.message.reply_text(
            "❌ Продукт не найден в базе.\n\n"
            "Попробуйте ввести точное название или начните заново: /calculate"
        )
        return ConversationHandler.END

    # Базовая ставка
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

    # Акселераторы
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
    await update.message.reply_text("\n".join(explanation), parse_mode='Markdown')

    await update.message.reply_text("Хотите рассчитать еще один продукт? /calculate")
    return ConversationHandler.END

# Отмена
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Расчет отменен.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Основная функция запуска бота
def main():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise ValueError("Не задан BOT_TOKEN в переменных окружения!")

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('calculate', calculate_start)],
        states={
            STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_status)],
            SPECIALIZATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_specialization)],
            PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_rebate)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    logging.info("Бот запущен и готов к работе")
    app.run_polling()

if __name__ == '__main__':
    # Эта конструкция запускает бота и поднимает веб-сервер для Koyeb
    port = int(os.environ.get("PORT", 8080))
    staypresent.run(main, port=port)
