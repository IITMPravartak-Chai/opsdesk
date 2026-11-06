"""Pluggable AI providers.

Embeddings
  hashing  - local, zero-dependency feature-hashing embedder. Works offline and
             is good enough for near-duplicate / similar-ticket retrieval on
             IT vocabulary. Default.
  openai   - any OpenAI-compatible /embeddings endpoint (OpenAI, Azure OpenAI v1,
             vLLM, Ollama, LiteLLM gateway). Must support `dimensions=384`.
LLM (optional; enables summaries, draft replies, richer triage)
  none | anthropic | openai (OpenAI-compatible /chat/completions)
"""
import hashlib
import json
import math
import re

import requests
from ..settings import settings

DIM = 384
STOP = set("""a an the and or but if then else to of in on at by for with from is are was were be been being
this that these those it its as not no can could would should will just please hi hello thanks thank regards dear
we our you your i me my they them their he she his her there here have has had do does did so very also""".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9_.+-]*")


SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "ies", "ied", "ed", "es", "ly", "er", "s")


def _stem(w):
    if len(w) <= 4 or not w.isalpha():
        return w
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def _tokens(text):
    words = [w.strip(".-") for w in _WORD.findall((text or "").lower())]
    words = [_stem(w) for w in words if w and w not in STOP and len(w) > 1]
    feats = list(words)
    feats += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for w in words:
        if len(w) > 4:
            feats += [f"#{w[i:i + 4]}" for i in range(len(w) - 3)]
    return feats


class HashingEmbedder:
    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * DIM
            counts = {}
            for f in _tokens(text):
                counts[f] = counts.get(f, 0) + 1
            for f, c in counts.items():
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                weight = (1 + math.log(c)) * (0.35 if f.startswith("#") else 1.0)
                vec[h % DIM] += weight if (h >> 63) & 1 else -weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class OpenAICompatibleEmbedder:
    def embed(self, texts):
        r = requests.post(
            f"{settings.ai_embedding_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.ai_embedding_api_key}"},
            json={"model": settings.ai_embedding_model, "input": [t[:8000] for t in texts], "dimensions": DIM},
            timeout=30,
        )
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


def get_embedder():
    return OpenAICompatibleEmbedder() if settings.ai_embedding_provider == "openai" else HashingEmbedder()


def embed_text(text):
    return get_embedder().embed([text or ""])[0]


class LLMUnavailable(Exception):
    pass


def llm_enabled():
    if settings.ai_llm_provider == "anthropic":
        return bool(settings.ai_llm_api_key)
    if settings.ai_llm_provider == "openai":
        return bool(settings.ai_llm_api_key or settings.ai_llm_base_url)
    return False


def complete(system, prompt, max_tokens=800):
    provider = settings.ai_llm_provider
    if not llm_enabled():
        raise LLMUnavailable("No LLM provider configured")
    if provider == "anthropic":
        r = requests.post(
            (settings.ai_llm_base_url or "https://api.anthropic.com") + "/v1/messages",
            headers={"x-api-key": settings.ai_llm_api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": settings.ai_llm_model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    api_key = settings.ai_llm_api_key or "ollama"
    r = requests.post(
        (settings.ai_llm_base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": settings.ai_llm_model, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def complete_json(system, prompt, max_tokens=600):
    raw = complete(system + "\nRespond with a single JSON object only, no prose, no code fences.", prompt, max_tokens)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])
