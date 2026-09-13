import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from bot import bot, dp
from config import settings

logger = logging.getLogger(__name__)

WEBHOOK_PATH = f"/webhook/{settings.WEBHOOK_SECRET}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await dp.emit_startup(bot)
    yield
    await dp.emit_shutdown(bot)


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health() -> dict:
    """Проверка, что функция запущена"""
    return {"status": "ok", "service": "voice_transcription_bot"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    """Приём апдейтов от Telegram"""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if settings.WEBHOOK_SECRET and secret != settings.WEBHOOK_SECRET:
        return Response(status_code=403)

    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Ошибка обработки апдейта")
        return Response(status_code=200)

    return Response(status_code=200)


@app.get("/set_webhook")
async def set_webhook(request: Request, secret: str = "") -> Response:
    """Устанавливает webhook на текущий домен (вызывается один раз после деплоя)"""
    if secret != settings.WEBHOOK_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}{WEBHOOK_PATH}"

    await bot.set_webhook(
        webhook_url,
        secret_token=settings.WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )
    info = await bot.get_webhook_info()
    return JSONResponse({
        "webhook_url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_message": info.last_error_message,
    })
