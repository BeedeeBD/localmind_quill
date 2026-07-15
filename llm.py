"""The single module that talks to the model.

Everything model-related goes through here by design, so the rest of the app
never knows which runtime is live. Two backends are supported, chosen by
`config.LLM_BACKEND`:

  * "ollama"   — the local Ollama server (localhost:11434).
  * "llamacpp" — a local GGUF model run in-process via llama-cpp-python, with no
                 Ollama at all. Weights are loaded lazily on first use, so
                 importing this module is cheap and never requires the model to
                 be present until you actually chat.

Either way there is no internet access here — both backends are entirely local,
so prompts and documents never leave the machine. The public API is identical
across backends: chat(), chat_stream(), ask(), embed().
"""
import re
from typing import List, Dict

import config

# Thinking models (Qwen3, some Llama fine-tunes) narrate reasoning inside
# <think>...</think> before the real answer. Great for debugging, noise for
# everyone else. Handled below, backend-agnostically.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    """Remove any <think>...</think> reasoning blocks and tidy the edges."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower():
        text = text[text.lower().rindex("<think>") + len("<think>"):]
    return text.strip()


class _ThinkFilter:
    """Strip <think>...</think> spans from a *stream*, across chunk boundaries."""
    _OPEN, _CLOSE = "<think>", "</think>"
    _MAXTAG = len("</think>")

    def __init__(self):
        self._buf = ""
        self._in_think = False

    def _safe_tail(self) -> int:
        for k in range(min(self._MAXTAG - 1, len(self._buf)), 0, -1):
            frag = self._buf[-k:].lower()
            if self._OPEN.startswith(frag) or self._CLOSE.startswith(frag):
                return k
        return 0

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            if not self._in_think:
                i = self._buf.lower().find(self._OPEN)
                if i == -1:
                    keep = self._safe_tail()
                    out.append(self._buf[: len(self._buf) - keep])
                    self._buf = self._buf[len(self._buf) - keep:]
                    break
                out.append(self._buf[:i])
                self._buf = self._buf[i + len(self._OPEN):]
                self._in_think = True
            else:
                j = self._buf.lower().find(self._CLOSE)
                if j == -1:
                    keep = self._safe_tail()
                    self._buf = self._buf[len(self._buf) - keep:]
                    break
                self._buf = self._buf[j + len(self._CLOSE):]
                self._in_think = False
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            return ""
        out, self._buf = self._buf, ""
        return out


# ===========================================================================
# Backend: Ollama
# ===========================================================================

_ollama_client = None


def _ollama():
    global _ollama_client
    if _ollama_client is None:
        import ollama
        _ollama_client = ollama.Client(host=config.OLLAMA_HOST)
    return _ollama_client


def _ollama_chat(messages, model):
    resp = _ollama().chat(
        model=model or config.CHAT_MODEL,
        messages=messages,
        options={"num_ctx": config.NUM_CTX},
    )
    return resp["message"]["content"]


def _ollama_chat_stream(messages, model):
    stream = _ollama().chat(
        model=model or config.CHAT_MODEL,
        messages=messages,
        options={"num_ctx": config.NUM_CTX},
        stream=True,
    )
    for part in stream:
        piece = part["message"]["content"]
        if piece:
            yield piece


def _ollama_embed(text):
    return _ollama().embeddings(model=config.EMBED_MODEL, prompt=text)["embedding"]


# ===========================================================================
# Backend: llama.cpp (in-process, via llama-cpp-python)
# ===========================================================================

_llama_chat_model = None
_llama_embed_model = None


def _require_llama():
    try:
        from llama_cpp import Llama
    except ImportError as e:
        raise RuntimeError(
            "LLM_BACKEND=llamacpp needs llama-cpp-python. Install a prebuilt CPU "
            "wheel:\n  pip install llama-cpp-python "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
        ) from e
    return Llama


def _chat_model():
    global _llama_chat_model
    if _llama_chat_model is None:
        Llama = _require_llama()
        import os
        if not os.path.exists(config.LLAMA_MODEL_PATH):
            raise RuntimeError(
                f"Chat model not found: {config.LLAMA_MODEL_PATH}. Set "
                "LLAMA_MODEL_PATH to your Llama .gguf, or run "
                "scripts/get_model.py to download one.")
        kw = dict(model_path=config.LLAMA_MODEL_PATH, n_ctx=config.LLAMA_N_CTX,
                  n_gpu_layers=config.LLAMA_GPU_LAYERS, verbose=False)
        if config.LLAMA_THREADS:
            kw["n_threads"] = config.LLAMA_THREADS
        _llama_chat_model = Llama(**kw)
    return _llama_chat_model


def _embed_model():
    global _llama_embed_model
    if _llama_embed_model is None:
        Llama = _require_llama()
        import os
        if not os.path.exists(config.LLAMA_EMBED_PATH):
            raise RuntimeError(
                f"Embedding model not found: {config.LLAMA_EMBED_PATH}. Set "
                "LLAMA_EMBED_PATH to an embedding .gguf, or run "
                "scripts/get_model.py.")
        # Embedding models have a small trained context (nomic-embed = 2048).
        # Loading beyond it wastes memory and warns, so cap it — a chunk to embed
        # is always short anyway.
        kw = dict(model_path=config.LLAMA_EMBED_PATH,
                  n_ctx=min(config.LLAMA_N_CTX, 2048),
                  embedding=True, verbose=False)
        if config.LLAMA_THREADS:
            kw["n_threads"] = config.LLAMA_THREADS
        _llama_embed_model = Llama(**kw)
    return _llama_embed_model


def _llama_chat(messages, model):
    resp = _chat_model().create_chat_completion(messages=messages, stream=False)
    return resp["choices"][0]["message"]["content"]


def _llama_chat_stream(messages, model):
    for chunk in _chat_model().create_chat_completion(messages=messages,
                                                       stream=True):
        piece = chunk["choices"][0].get("delta", {}).get("content", "")
        if piece:
            yield piece


def _llama_embed(text):
    out = _embed_model().embed(text)
    # llama_cpp returns a flat vector for one string, or a list of vectors if it
    # decided to batch; normalise to a single flat vector.
    if out and isinstance(out[0], list):
        return out[0]
    return out


# ===========================================================================
# Public API — identical whichever backend is configured
# ===========================================================================

def _is_llamacpp() -> bool:
    return config.LLM_BACKEND == "llamacpp"


def chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    """Hand the model a full conversation, get back what it says."""
    content = (_llama_chat(messages, model) if _is_llamacpp()
               else _ollama_chat(messages, model))
    return _strip_thinking(content) if config.STRIP_THINK else content


def chat_stream(messages: List[Dict[str, str]], model: str | None = None):
    """Same as chat(), but yields the reply piece by piece as it's generated."""
    gen = (_llama_chat_stream(messages, model) if _is_llamacpp()
           else _ollama_chat_stream(messages, model))
    filt = _ThinkFilter() if config.STRIP_THINK else None
    for piece in gen:
        yield filt.feed(piece) if filt else piece
    if filt:
        tail = filt.flush()
        if tail:
            yield tail


def ask(prompt: str, system: str | None = None, model: str | None = None) -> str:
    """Shortcut for a one-off question, with an optional system instruction."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, model=model)


def embed(text: str) -> List[float]:
    """Return an embedding vector for a piece of text (used by RAG + memory)."""
    return _llama_embed(text) if _is_llamacpp() else _ollama_embed(text)
