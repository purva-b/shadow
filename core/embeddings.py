"""Embeddings via Bedrock Titan — the semantic recall signal.

embed(text) -> vector (1024-dim, Titan v2). Vectors are cached on disk keyed by
a content hash so re-ranking the same corpus costs nothing after the first pass.
cosine(a, b) scores similarity. retrieve.recall uses this to rank candidates by
meaning, with the keyword lane as the fallback when Bedrock is unavailable.

Production swap: the same embed() feeds a pgvector / OpenSearch index instead of
an in-process scan; the interface does not change.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODEL = os.environ.get("SHADOW_EMBED_MODEL", "amazon.titan-embed-text-v2:0")


def _cache_path() -> Path:
    return Path(os.environ.get("SHADOW_EMBED_CACHE", str(ROOT / "web" / "embed_cache.json")))

_mem: dict[str, list[float]] | None = None


def available() -> bool:
    try:
        import boto3  # noqa: F401
        return bool(os.environ.get("BEDROCK_MODEL_ID") or os.environ.get("AWS_REGION"))
    except Exception:
        return False


@lru_cache(maxsize=1)
def _client():
    import boto3
    return boto3.client("bedrock-runtime",
                        region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _load_cache() -> dict:
    global _mem
    if _mem is None:
        try:
            _mem = json.loads(_cache_path().read_text()) if _cache_path().exists() else {}
        except Exception:
            _mem = {}
    return _mem or {}


def _save_cache() -> None:
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(_mem or {}))
    except Exception:
        pass


def _key(text: str) -> str:
    return hashlib.sha1(f"{_MODEL}:{text}".encode()).hexdigest()


def embed(text: str) -> list[float] | None:
    """Return the embedding for text, or None if Bedrock is unavailable."""
    text = (text or "").strip()
    if not text:
        return None
    cache = _load_cache()
    k = _key(text)
    if k in cache:
        return cache[k]
    try:
        r = _client().invoke_model(
            modelId=_MODEL, contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text[:8000]}),
        )
        vec = json.loads(r["body"].read())["embedding"]
    except Exception:
        return None
    cache[k] = vec
    _save_cache()
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
