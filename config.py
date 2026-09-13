from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    POLZA_API_KEY: str
    POLZA_BASE_URL: str = "https://polza.ai/api/v1"
    WHISPER_MODEL: str = "openai/whisper-large-v3-turbo"
    PROXY_URL: str | None = None
    WEBHOOK_SECRET: str = "change-me"

    class Config:
        env_file = ".env"


settings = Settings()