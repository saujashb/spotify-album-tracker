from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    album_url: str = ""
    artist_id: str = ""
    sp_dc: str = ""
    poll_interval_seconds: int = 120
    dashboard_title: str = ""
    database_path: str = "data/streams.db"


settings = Settings()
