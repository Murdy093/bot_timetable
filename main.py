import logging
import asyncio
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

import time
from datetime import datetime, timedelta
from threading import Lock

# settings
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7606814984:AAEbAYrOmC9mN0TPhwBZNuLv1ykpGQHoXSs"
URL = "https://dekanat.nung.edu.ua/cgi-bin/timetable.cgi?n=700"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# FSM (control status Mashine)
class Form(StatesGroup):
    waiting_for_group = State()
    group_set = State()


# In this code I'm tried to save cahce :(
cache = {}
cache_lock = Lock()
CACHE_TTL = 3 * 86400


# Enable Selenium
class SeleniumManager:
    def __init__(self):
        self.options = Options()
        self.options.add_argument("--headless")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.page_load_strategy = 'eager'

        self.service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=self.service, options=self.options)
        self.lock = Lock()

    # fetch for 1 time enable selenium
    def fetch(self, group: str) -> str:
        with self.lock:
            try:
                self.driver.get(URL)
                WebDriverWait(self.driver, 3).until(EC.presence_of_element_located((By.ID, "group")))

                input_box = self.driver.find_element(By.ID, "group")
                input_box.clear()
                clean_group = ''.join(c for c in group if c.isalnum() or c in "-_ ")
                input_box.send_keys(clean_group + Keys.RETURN)

                WebDriverWait(self.driver, 3).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                return self.driver.page_source
            except Exception as e:
                logger.error(f"Selenium error: {e}")
                return ""

    def close(self):
        self.driver.quit()


selenium = SeleniumManager()


# check day of week
def get_next_day_of_week(target_weekday: int) -> str:
    today = datetime.today()
    days_ahead = target_weekday - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%d.%m.%Y")

# generate keys for buttons
def generate_days_keyboard():
    days = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця"]
    buttons = [
        InlineKeyboardButton(
            text=f"{day} {get_next_day_of_week(i)}",
            callback_data=f"day_{i}"
        ) for i, day in enumerate(days)
    ]

    return InlineKeyboardMarkup(inline_keyboard=[
        buttons[:2],
        buttons[2:4],
        [buttons[4]],
        [InlineKeyboardButton(text="Змінити групу", callback_data="change_group")],
        [InlineKeyboardButton(text="💛 Підтримати розробника", url="https://send.monobank.ua/jar/3NramzAvJK")]
    ])


# normalize Friday
def normalize(text: str) -> str:
    return re.sub(r"[’'`ʻʹʽ]", "", text.lower())

# pizdec
def get_cached_html(group: str, day_name: str) -> str | None:
    with cache_lock:
        if group in cache and day_name in cache[group]:
            timestamp, html = cache[group][day_name]
            if time.time() - timestamp < CACHE_TTL:
                return html
        return None

# also pizdec
def set_cache(group: str, day_name: str, html: str):
    with cache_lock:
        if group not in cache:
            cache[group] = {}
        cache[group][day_name] = (time.time(), html)

# parse site
def parse_timetable(html: str, day_idx: int) -> str:
    day_names = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця"]
    target_day = day_names[day_idx]

    soup = BeautifulSoup(html, "html.parser")
    for block in soup.find_all("h4"):
        if normalize(block.get_text()).find(normalize(target_day)) != -1:
            table = block.find_next("table")
            if not table:
                return f"❌ Розклад на {target_day} відсутній"

            heading_raw = block.get_text(strip=True)
            heading_fixed = re.sub(r"(\d{2}\.\d{2}\.\d{4})(?=\D)", r"\1 ", heading_raw)
            result = [f"<b>{heading_fixed}</b>"]

            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                time_parts = cells[1].get_text(" ", strip=True).split()
                time = f"{time_parts[0]}-{time_parts[1]}" if len(time_parts) >= 2 else ""

                content = [line.strip() for line in cells[2].get_text("\n").split("\n") if line.strip()]
                if not content:
                    result.append(f"<b>{cells[0].get_text()} пара {time}</b>\n🔹 Пари немає")
                    continue

                lesson_type = "📚"
                if "(Лаб)" in content[0]:
                    lesson_type = "🔬 Лабораторне"
                    content[0] = content[0].replace("(Лаб)", "").strip()
                elif "(Л)" in content[0]:
                    lesson_type = "📖 Лекція"
                    content[0] = content[0].replace("(Л)", "").strip()
                elif "(Пр)" in content[0]:
                    lesson_type = "✏️ Практика"
                    content[0] = content[0].replace("(Пр)", "").strip()

                pair = (
                    f"<b>{cells[0].get_text()} пара {time}</b>\n"
                    f"{lesson_type} {content[0]}\n"
                    f"{'👥 ' + content[1] + '\n' if len(content) > 1 else ''}"
                    f"{'👨‍🏫 ' + ' '.join(content[2].split()) + '\n' if len(content) > 2 else ''}"
                    f"{'🏫 ' + content[3] + '\n' if len(content) > 3 and 'ауд.' in content[3] else ''}").strip()

                result.append(pair)

            return "\n\n➖➖➖➖➖➖\n\n".join(result)

    return f"❌ Розклад на {target_day} не знайдено"



# message handler all
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "👋 Введіть назву вашої групи (наприклад, КІ-22-1):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(Form.waiting_for_group)


@dp.message(Form.waiting_for_group)
async def set_group(message: types.Message, state: FSMContext):
    group = message.text.strip()
    if not group:
        await message.answer("❌ Будь ласка, введіть коректну назву групи")
        return

    await state.update_data(group=group, message_id=message.message_id)
    await message.answer(
        f"✅ Обрана група: <b>{group}</b>\nОберіть день:",
        reply_markup=generate_days_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(Form.group_set)


@dp.callback_query(Form.group_set)
async def process_day_selection(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "change_group":
        await callback.message.edit_text("Введіть нову назву групи:")
        await state.set_state(Form.waiting_for_group)
        return

    if callback.data.startswith("day_"):
        day_idx = int(callback.data.split("_")[1])
        user_data = await state.get_data()
        group = user_data.get("group")

        await callback.answer("⏳ Отримую розклад...")

        html = get_cached_html(group, str(day_idx))
        if not html:
            html = await asyncio.to_thread(selenium.fetch, group)
            if not html:
                await callback.message.edit_text("⚠️ Помилка при отриманні розкладу")
                return
            set_cache(group, str(day_idx), html)

        timetable = parse_timetable(html, day_idx)
        await callback.message.edit_text(
            f"📅 <b>Розклад для {group}</b>\n\n{timetable}",
            reply_markup=generate_days_keyboard(),
            parse_mode="HTML"
        )


async def main():
    try:
        await dp.start_polling(bot)
    finally:
        selenium.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())