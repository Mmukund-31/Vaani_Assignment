"""Runtime configuration. Secrets come only from the environment / .env (never committed)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    standin_url: str = os.getenv("STANDIN_URL", "http://127.0.0.1:8001/openai/v1")
    max_output_tokens: int = int(os.getenv("RELAY_MAX_OUTPUT_TOKENS", "400"))
    reasoning_effort: str = os.getenv("RELAY_REASONING_EFFORT", "low")  # gpt-oss models only


def settings() -> Settings:
    return Settings()


def require_groq(s: Settings) -> None:
    if not s.groq_api_key:
        raise SystemExit("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    if not s.groq_model:
        raise SystemExit("GROQ_MODEL is not set in .env.")
