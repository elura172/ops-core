from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    tts_app_key: str
    tts_app_secret: str
    tts_access_token: str
    tts_shop_id: str
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"missing required secret: {name}")
            return value

        return cls(
            required("TTS_APP_KEY"),
            required("TTS_APP_SECRET"),
            required("TTS_ACCESS_TOKEN"),
            required("TTS_SHOP_ID"),
            os.getenv("DRY_RUN", "true").lower() not in {"0", "false", "no"},
        )
