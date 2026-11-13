"""Build provider + governor + pipeline for a given mode."""
from __future__ import annotations

from .config import require_groq, settings
from .limits import Governor
from .pipeline import Pipeline, RetryPolicy
from .providers import MockProvider, OpenAICompatProvider, Provider
from .retrieval import SETTINGS, build_index

# Measured on our free-tier account (results/groq_calibration.json); TPD is from Groq docs (no header exposes it).
GROQ_LIMITS = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200_000}


def make_provider(mode: str, standin_url: str | None = None) -> Provider:
    s = settings()
    if mode == "groq":
        require_groq(s)
        return OpenAICompatProvider(s.groq_base_url, s.groq_api_key, s.groq_model, "real", "groq",
                                    reasoning_effort=s.reasoning_effort)
    if mode == "standin":
        return OpenAICompatProvider(standin_url or s.standin_url, "standin", "standin-gpt-oss-120b", "stand-in",
                                    "standin", max_connections=256)
    if mode == "mock":
        return MockProvider()
    raise ValueError(mode)


def make_pipeline(mode: str, setting: str = "A", limits: dict | None = None, retry: RetryPolicy | None = None,
                  max_output_tokens: int | None = None, standin_url: str | None = None,
                  governor: Governor | None = None, fallback: str | None = None,
                  degrade_after_s: float = 3.0) -> tuple[Pipeline, Governor | None]:
    prov = make_provider(mode, standin_url)
    lim = limits if limits is not None else (GROQ_LIMITS if mode in ("groq", "standin") else None)
    gov = governor or (Governor(**lim) if lim else None)   # limits={} -> no governor
    pipe = Pipeline(prov, build_index(SETTINGS[setting]), governor=gov, retry=retry,
                    max_output_tokens=max_output_tokens or settings().max_output_tokens,
                    fallback_index=build_index(SETTINGS[fallback]) if fallback and fallback != setting else None,
                    degrade_after_s=degrade_after_s)
    return pipe, gov
