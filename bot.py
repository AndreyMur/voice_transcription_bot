import asyncio
import html
import io
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
import httpx

from config import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
session = AiohttpSession(proxy=settings.PROXY_URL) if settings.PROXY_URL else AiohttpSession()
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)
dp = Dispatcher()

# Кэш последних транскрибаций: message_id сообщения с результатом -> текст
transcriptions: dict[int, str] = {}
MAX_TRANSCRIPTIONS = 1000


def result_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с действиями над результатом транскрибации"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Скопировать", callback_data="copy_text"),
            InlineKeyboardButton(text="📤 Переслать", callback_data="share_text"),
        ],
        [InlineKeyboardButton(text="🎤 Записать ещё", callback_data="record_voice")],
    ])


async def send_result(message: Message, text: str, language: str, duration: float) -> None:
    """Отправляет результат транскрибации и сохраняет текст для кнопок"""
    sent = await message.answer(
        f"✅ **Транскрибация готова!**\n\n"
        f"📝 **Текст:**\n{text}\n\n"
        f"🌐 **Язык:** {language}\n"
        f"⏱ **Длительность:** {duration:.1f} сек",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=result_keyboard(),
    )
    if len(transcriptions) >= MAX_TRANSCRIPTIONS:
        transcriptions.pop(next(iter(transcriptions)))
    transcriptions[sent.message_id] = text


async def transcribe_audio(audio_file_bytes: bytes, filename: str = "voice.ogg") -> dict:
    """
    Отправляет аудиофайл в Whisper API для транскрибации
    """
    url = f"{settings.POLZA_BASE_URL}/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {settings.POLZA_API_KEY}",
    }
    
    # Определяем MIME-тип в зависимости от расширения
    if filename.endswith(".ogg"):
        mime_type = "audio/ogg"
    elif filename.endswith(".mp3"):
        mime_type = "audio/mpeg"
    elif filename.endswith(".wav"):
        mime_type = "audio/wav"
    elif filename.endswith(".m4a"):
        mime_type = "audio/m4a"
    else:
        mime_type = "audio/ogg"  # По умолчанию для голосовых сообщений Telegram
    
    files = {
        "file": (filename, io.BytesIO(audio_file_bytes), mime_type)
    }
    
    data = {
        "model": settings.WHISPER_MODEL,
        "response_format": "json",
        "language": "auto",
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                url,
                headers=headers,
                files=files,
                data=data
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP ошибка: {e.response.status_code} - {e.response.text}")
            raise Exception(f"Ошибка API: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Ошибка запроса: {e}")
            raise Exception(f"Ошибка сети: {str(e)}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎤 Записать голосовое", callback_data="record_voice")]
    ])
    
    await message.answer(
        "👋 Привет! Я бот для транскрибации голосовых сообщений.\n\n"
        "Нажмите кнопку ниже, чтобы начать запись.",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "record_voice")
async def callback_record_voice(callback: CallbackQuery):
    """Обработчик нажатия на кнопку записи"""
    await callback.message.answer("🎤 Теперь отправьте мне голосовое сообщение или аудиофайл.")
    await callback.answer()


@dp.callback_query(F.data == "copy_text")
async def callback_copy_text(callback: CallbackQuery):
    """Отправляет текст отдельным сообщением для копирования"""
    text = transcriptions.get(callback.message.message_id)
    if not text:
        await callback.answer("Текст не найден, отправьте аудио заново.", show_alert=True)
        return

    await callback.message.answer(
        f"<code>{html.escape(text)}</code>",
        parse_mode="HTML",
    )
    await callback.answer("Нажмите на текст, чтобы скопировать")


@dp.callback_query(F.data == "share_text")
async def callback_share_text(callback: CallbackQuery):
    """Отправляет текст сообщением, которое можно переслать"""
    text = transcriptions.get(callback.message.message_id)
    if not text:
        await callback.answer("Текст не найден, отправьте аудио заново.", show_alert=True)
        return

    await callback.message.answer(f"📤 Перешлите это сообщение:\n\n{text}")
    await callback.answer("Перешлите сообщение выше")


@dp.message(F.voice)
async def handle_voice(message: Message):
    """Обработчик голосовых сообщений (OGG)"""
    processing_msg = await message.answer("⏳ Обрабатываю голосовое сообщение...")
    
    try:
        # Скачиваем голосовое сообщение
        file = await bot.get_file(message.voice.file_id)
        file_bytes = await bot.download_file(file.file_path)
        
        # Транскрибируем
        result = await transcribe_audio(file_bytes.read(), "voice.ogg")
        
        transcribed_text = result.get("text", "Текст не распознан.")
        language = result.get("language", "unknown")
        duration = result.get("duration", 0)
        
        await processing_msg.delete()
        await send_result(message, transcribed_text, language, duration)
        
    except Exception as e:
        logger.exception("Ошибка при обработке голосового сообщения")
        await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


@dp.message(F.audio)
async def handle_audio(message: Message):
    """Обработчик аудиофайлов"""
    processing_msg = await message.answer("⏳ Обрабатываю аудиофайл...")
    
    try:
        # Скачиваем аудиофайл
        file = await bot.get_file(message.audio.file_id)
        file_bytes = await bot.download_file(file.file_path)
        
        # Определяем имя файла
        filename = message.audio.file_name or "audio.mp3"
        
        # Транскрибируем
        result = await transcribe_audio(file_bytes.read(), filename)
        
        transcribed_text = result.get("text", "Текст не распознан.")
        language = result.get("language", "unknown")
        duration = result.get("duration", 0)
        
        await processing_msg.delete()
        await send_result(message, transcribed_text, language, duration)
        
    except Exception as e:
        logger.exception("Ошибка при обработке аудиофайла")
        await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


@dp.message(F.document)
async def handle_document(message: Message):
    """Обработчик документов (аудиофайлы)"""
    processing_msg = await message.answer("⏳ Обрабатываю документ...")
    
    try:
        file = await bot.get_file(message.document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        
        filename = message.document.file_name or "document.ogg"
        
        # Проверяем, является ли файл аудио
        audio_extensions = ('.ogg', '.mp3', '.wav', '.m4a', '.flac', '.webm')
        if not filename.lower().endswith(audio_extensions):
            await processing_msg.edit_text("❌ Пожалуйста, отправьте аудиофайл (ogg, mp3, wav, m4a).")
            return
        
        # Транскрибируем
        result = await transcribe_audio(file_bytes.read(), filename)
        
        transcribed_text = result.get("text", "Текст не распознан.")
        language = result.get("language", "unknown")
        duration = result.get("duration", 0)
        
        await processing_msg.delete()
        await send_result(message, transcribed_text, language, duration)
        
    except Exception as e:
        logger.exception("Ошибка при обработке документа")
        await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


@dp.message(F.text)
async def handle_text(message: Message):
    """Обработчик текстовых сообщений"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎤 Записать голосовое", callback_data="record_voice")]
    ])
    
    await message.answer(
        "👋 Я бот для транскрибации голосовых сообщений.\n\n"
        "Нажмите кнопку ниже или просто отправьте мне голосовое сообщение.",
        reply_markup=keyboard
    )


async def main():
    """Запуск бота"""
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())