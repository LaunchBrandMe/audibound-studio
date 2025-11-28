"""Modal deployment for IndexTTS2 (expressive zero-shot TTS)."""

from pathlib import Path
import base64
import shutil
import subprocess
import sys
from typing import List, Optional

import modal

MODEL_DIR = Path("/cache/checkpoints")
HF_REPO = "https://huggingface.co/IndexTeam/IndexTTS-2"
OUTPUT_PATH = Path("/tmp/indextts2_output.wav")
DEFAULT_PROMPT_PATH = Path("/assets/default_indextts2_prompt.wav")

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "git-lfs")
    .run_commands("git lfs install --system")
    .pip_install(
        "torch==2.1.0",
        "torchaudio==2.1.0",
        extra_options="--index-url https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "transformers",
        "accelerate",
        "scipy",
        "numpy",
        "soundfile",
        "fastapi",
    )
    .run_commands(
        "git clone https://github.com/index-tts/index-tts /root/index-tts",
        "cd /root/index-tts && pip install -e .",
    )
)

app = modal.App("audibound-indextts2", image=image)
model_volume = modal.Volume.from_name("indextts2-models", create_if_missing=True)


@app.cls(
    gpu="T4",
    volumes={"/cache": model_volume},
    timeout=600,
    memory=16384,
)
class IndexTTS2Worker:
    def __init__(self) -> None:
        self._tts = None

    def _ensure_models(self) -> None:
        """Download checkpoints into the shared volume if missing."""
        cfg_path = MODEL_DIR / "config.yaml"
        if cfg_path.exists():
            print("[IndexTTS2] Using cached checkpoints")
            return

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path("/tmp/indextts2_download")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        print("[IndexTTS2] Downloading model files from Hugging Face…")
        subprocess.run(["git", "clone", HF_REPO, str(tmp_dir)], check=True)
        subprocess.run(["git", "-C", str(tmp_dir), "lfs", "install", "--local"], check=True)
        subprocess.run(["git", "-C", str(tmp_dir), "lfs", "pull"], check=True)

        if MODEL_DIR.exists():
            shutil.rmtree(MODEL_DIR)
        shutil.move(str(tmp_dir), str(MODEL_DIR))
        print(f"[IndexTTS2] Models cached at {MODEL_DIR}")

    def _ensure_default_prompt(self) -> None:
        """Create a tiny fallback prompt if no real reference audio provided."""
        if DEFAULT_PROMPT_PATH.exists():
            return
        DEFAULT_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        print("[IndexTTS2] Creating fallback prompt audio…")
        import numpy as np
        import soundfile as sf

        sr = 24000
        duration = 1.5
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        audio = 0.02 * np.sin(2 * np.pi * 220 * t)
        sf.write(DEFAULT_PROMPT_PATH, audio.astype(np.float32), sr)
        print(f"[IndexTTS2] Fallback prompt written to {DEFAULT_PROMPT_PATH}")

    @modal.enter()
    def setup(self) -> None:
        """Load IndexTTS2 once per container for fast warm requests."""
        sys.path.insert(0, "/root/index-tts")
        self._ensure_models()
        self._ensure_default_prompt()

        from indextts.infer_v2 import IndexTTS2

        print("[IndexTTS2] Initializing model…")
        self._tts = IndexTTS2(
            cfg_path=str(MODEL_DIR / "config.yaml"),
            model_dir=str(MODEL_DIR),
            use_fp16=True,
            use_cuda_kernel=True,
            use_deepspeed=False,
        )
        print("[IndexTTS2] Model ready")

    def _sanitize_vector(self, vector: Optional[List[float]]) -> List[float]:
        default_vector = [0.2, 0, 0, 0, 0, 0, 0, 0.6]
        if not vector:
            return default_vector
        try:
            cleaned = [float(v) for v in vector][:8]
            if len(cleaned) != 8:
                return default_vector
            return cleaned
        except (TypeError, ValueError):
            return default_vector

    @modal.method()
    def generate(
        self,
        text: str,
        emo_vector: Optional[List[float]] = None,
        emo_alpha: float = 0.7,
        voice_sample_b64: Optional[str] = None,
        use_random: bool = False,
    ) -> bytes:
        if not text or not text.strip():
            raise ValueError("Text is required")
        if self._tts is None:
            raise RuntimeError("IndexTTS2 model is not initialized")

        emo_vector = self._sanitize_vector(emo_vector)
        prompt_path = DEFAULT_PROMPT_PATH
        temp_prompt = None
        if voice_sample_b64:
            prompt_bytes = base64.b64decode(voice_sample_b64)
            temp_prompt = Path("/tmp/custom_voice_prompt.wav")
            temp_prompt.write_bytes(prompt_bytes)
            prompt_path = temp_prompt

        if OUTPUT_PATH.exists():
            OUTPUT_PATH.unlink()

        print("[IndexTTS2] Generating speech…")
        self._tts.infer(
            spk_audio_prompt=str(prompt_path),
            text=text,
            output_path=str(OUTPUT_PATH),
            emo_vector=emo_vector,
            emo_alpha=float(emo_alpha),
            use_random=bool(use_random),
            verbose=True,
        )

        audio_bytes = OUTPUT_PATH.read_bytes()
        print(f"[IndexTTS2] Generated {len(audio_bytes)} bytes")
        if temp_prompt and temp_prompt.exists():
            temp_prompt.unlink()
        return audio_bytes


worker = IndexTTS2Worker()


@app.function()
@modal.fastapi_endpoint(method="POST")
def generate_speech(item: dict):
    from fastapi import HTTPException
    from fastapi.responses import Response

    text = (item or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    emo_vector = item.get("emo_vector")
    emo_alpha = float(item.get("emo_alpha", 0.7))
    voice_sample_b64 = item.get("voice_sample_b64")
    use_random = bool(item.get("use_random", False))

    audio_bytes = worker.generate.remote(
        text=text,
        emo_vector=emo_vector,
        emo_alpha=emo_alpha,
        voice_sample_b64=voice_sample_b64,
        use_random=use_random,
    )

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=indextts2.wav"},
    )
