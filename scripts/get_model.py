"""Download a local Llama (and embedding) model for the llama.cpp backend.

This is a one-off setup helper, separate from the running assistant — the app's
only *live* internet path is still the guarded web-search gate. It fetches GGUF
weights from Hugging Face into config.MODELS_DIR (E: by default, since C: is
nearly full) and saves them under the exact names the llama.cpp backend expects
(chat.gguf / embed.gguf). Downloads are resumable: re-run it and it continues
from where it stopped.

Usage:
    python scripts/get_model.py llama3.2-3b     # ~2.1 GB total (recommended, CPU)
    python scripts/get_model.py llama3.1-8b     # ~5.0 GB total (higher quality)
    python scripts/get_model.py llama3.2-3b --chat-only
"""
import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

# Ungated re-uploads (bartowski / nomic-ai) so no HF token or license click is
# needed for a personal local setup.
_EMBED = ("https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/"
          "resolve/main/nomic-embed-text-v1.5.f16.gguf")

PRESETS = {
    "llama3.2-3b": {
        "chat": ("https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/"
                 "resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"),
        "embed": _EMBED,
    },
    "llama3.1-8b": {
        "chat": ("https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/"
                 "resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"),
        "embed": _EMBED,
    },
}


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(url: str, dest: str) -> None:
    """Stream a file to dest with a resume-capable, progress-printing GET."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.Client(follow_redirects=True, timeout=None) as c:
        with c.stream("GET", url, headers=headers) as r:
            if r.status_code not in (200, 206):
                raise SystemExit(f"  ! {url}\n    HTTP {r.status_code} — "
                                 "the file may have moved; check the URL.")
            total = int(r.headers.get("content-length", 0)) + have
            mode = "ab" if r.status_code == 206 else "wb"
            if r.status_code != 206:
                have = 0
            done = have
            with open(part, mode) as f:
                for chunk in r.iter_bytes(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    pct = f"{done / total * 100:5.1f}%" if total else "  ?  "
                    print(f"\r    {pct}  {_human(done)}"
                          + (f" / {_human(total)}" if total else ""), end="")
    print()
    os.replace(part, dest)
    print(f"    saved -> {dest}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Download a local Llama model.")
    ap.add_argument("preset", choices=sorted(PRESETS),
                    help="which model set to fetch")
    ap.add_argument("--chat-only", action="store_true")
    ap.add_argument("--embed-only", action="store_true")
    args = ap.parse_args()

    urls = PRESETS[args.preset]
    jobs = []
    if not args.embed_only:
        jobs.append(("chat", urls["chat"], config.LLAMA_MODEL_PATH))
    if not args.chat_only:
        jobs.append(("embed", urls["embed"], config.LLAMA_EMBED_PATH))

    print(f"Downloading '{args.preset}' into {config.MODELS_DIR}\n")
    for name, url, dest in jobs:
        if os.path.exists(dest):
            print(f"  {name}: already present ({_human(os.path.getsize(dest))}) — "
                  "skipping")
            continue
        print(f"  {name}: {url.rsplit('/', 1)[-1]}")
        download(url, dest)

    print("\nDone. Switch the app to this backend with LLM_BACKEND=llamacpp "
          "(in .env), then start the server.")


if __name__ == "__main__":
    main()
