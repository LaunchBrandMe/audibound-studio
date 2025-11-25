"""Modal deployment for StyleTTS2 using the official repository layout.

This image installs the upstream StyleTTS2 wheel with all of its declared
runtime dependencies, pins Torch/Torchaudio to CUDA 11.8 builds, and caches all
HuggingFace / cached_path downloads inside a Modal volume so that cold starts do
not re-download multi-gigabyte checkpoints.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, Any

import modal

STYLE_TTS_APP_NAME = "audibound-styletts2"
CACHE_ROOT = Path("/cache/styletts2")
HF_CACHE = CACHE_ROOT / "huggingface"
NLP_CACHE = CACHE_ROOT / "nltk"
CACHED_PATH_ROOT = CACHE_ROOT / "cached_path"
XDG_CACHE = CACHE_ROOT / ".xdg"
BOOTSTRAP_NLTK = Path("/nltk_bootstrap")

_BASE_PY_REQS = [
    "PyYAML==6.0.1",
    "accelerate==0.25.0",
    "cached-path==1.8.0",
    "einops==0.7.0",
    "einops-exts==0.0.4",
    "filelock==3.12.4",
    "gruut==2.4.0",
    "gruut-ipa==0.13.0",
    "gruut-lang-en==2.0.1",
    "huggingface-hub==0.19.4",
    "langchain==0.1.16",
    "librosa==0.10.1",
    "matplotlib==3.8.2",
    "munch==4.0.0",
    "networkx==2.8.8",
    "nltk==3.8.1",
    "numpy==1.26.4",
    "pydub==0.25.1",
    "scipy==1.11.4",
    "soundfile==0.12.1",
    "tqdm==4.66.1",
    "transformers==4.36.2",
    "typing-extensions==4.9.0",
    "fastapi==0.115.5",
]

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install(
        "git",
        "ffmpeg",
        "libsndfile1",
        "libgl1",
        "libglib2.0-0",
        "libsm6",
        "libxrender1",
        "libxext6",
        "espeak-ng",
    )
    .pip_install(
        "torch==2.1.2",
        "torchaudio==2.1.2",
        extra_options="--index-url https://download.pytorch.org/whl/cu118",
    )
    .pip_install(*_BASE_PY_REQS)
    .pip_install("styletts2==0.1.6")
    .run_commands(
        "mkdir -p /nltk_bootstrap",
        "python -c \"import nltk; nltk.download('punkt', download_dir='/nltk_bootstrap'); nltk.download('punkt_tab', download_dir='/nltk_bootstrap')\"",
    )
    .env({"NLTK_DATA": "/nltk_bootstrap"})
)

app = modal.App(STYLE_TTS_APP_NAME, image=image)
model_volume = modal.Volume.from_name("styletts2-models", create_if_missing=True)


def _ensure_dirs() -> None:
    for path in (CACHE_ROOT, HF_CACHE, NLP_CACHE, CACHED_PATH_ROOT, XDG_CACHE):
        path.mkdir(parents=True, exist_ok=True)


@app.cls(
    gpu="T4",
    timeout=600,
    memory=16384,
    volumes={"/cache": model_volume},
)
class StyleTTS2Worker:

    @modal.enter()
    def setup(self) -> None:
        import os

        _ensure_dirs()
        os.environ["HF_HOME"] = str(HF_CACHE)
        os.environ["XDG_CACHE_HOME"] = str(XDG_CACHE)
        os.environ["CACHED_PATH_CACHE_ROOT"] = str(CACHED_PATH_ROOT)
        os.environ["NLTK_DATA"] = str(NLP_CACHE)

        import nltk

        nltk_paths = [str(NLP_CACHE)]
        if BOOTSTRAP_NLTK.exists():
            nltk_paths.append(str(BOOTSTRAP_NLTK))
        for existing in list(nltk.data.path):
            if existing not in nltk_paths:
                nltk_paths.append(existing)
        nltk.data.path = nltk_paths

        for corpus in ("punkt", "punkt_tab"):
            try:
                nltk.data.find(f"tokenizers/{corpus}")
            except (LookupError, OSError):
                nltk.download(corpus, download_dir=str(NLP_CACHE))

        from styletts2 import tts

        print("[StyleTTS2] Initializing model ...")
        self._model = tts.StyleTTS2()
        print("[StyleTTS2] Model ready")

    @modal.method()
    def generate(
        self,
        text: str,
        *,
        alpha: float = 0.3,
        beta: float = 0.7,
        diffusion_steps: int = 10,
        embedding_scale: float = 1.0,
    ) -> bytes:
        import numpy as np
        import scipy.io.wavfile

        if not text or not text.strip():
            raise ValueError("Text is required for StyleTTS2 synthesis")

        wav = self._model.inference(
            text=text.strip(),
            target_voice_path=None,
            alpha=alpha,
            beta=beta,
            diffusion_steps=diffusion_steps,
            embedding_scale=embedding_scale,
        )

        if not isinstance(wav, np.ndarray):
            raise RuntimeError("StyleTTS2 inference did not return a numpy array")

        buffer = io.BytesIO()
        audio = np.clip(wav, -1.0, 1.0)
        scipy.io.wavfile.write(buffer, 24000, (audio * 32767).astype(np.int16))
        payload = buffer.getvalue()
        print(f"[StyleTTS2] Generated {len(payload)} bytes")
        return payload


worker = StyleTTS2Worker()


@app.function()
@modal.fastapi_endpoint(method="POST")
def generate_speech(item: Dict[str, Any]):
    from fastapi import HTTPException
    from fastapi.responses import Response

    text = (item or {}).get("text", "").strip()
    alpha = float((item or {}).get("alpha", 0.3))
    beta = float((item or {}).get("beta", 0.7))
    diffusion_steps = int((item or {}).get("diffusion_steps", 10))
    embedding_scale = float((item or {}).get("embedding_scale", 1.0))

    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    audio_bytes = worker.generate.remote(
        text,
        alpha=alpha,
        beta=beta,
        diffusion_steps=diffusion_steps,
        embedding_scale=embedding_scale,
    )

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=styletts2.wav"},
    )
