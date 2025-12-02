"""Modal deployment for Sesame CSM 1B using Hugging Face transformers."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Dict, Any, Optional

import modal

SESAME_APP_NAME = "audibound-sesame"
MODEL_ID = "sesame/csm-1b"
ASR_MODEL_ID = "openai/whisper-base"
CACHE_ROOT = Path("/cache/sesame")
HF_CACHE = CACHE_ROOT / "huggingface"

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg")
    .pip_install(
        "numpy<2",  # Fix NumPy 2.x compatibility issue
        "torch==2.4.0",
        "torchaudio==2.4.0",
        extra_options="--extra-index-url https://download.pytorch.org/whl/cu121"
    )
    .pip_install(
        "transformers>=4.46.0",  # Upgraded for CSM support (requires Torch 2.4+)
        "datasets",
        "accelerate",
        "soundfile",
        "huggingface_hub",
        "fastapi",
        "einops",
        "tqdm"
    )
)

app = modal.App(SESAME_APP_NAME, image=image)
model_volume = modal.Volume.from_name("sesame-models", create_if_missing=True)


def _ensure_dirs() -> None:
    HF_CACHE.mkdir(parents=True, exist_ok=True)


@app.cls(
    gpu="T4",
    timeout=600,
    memory=16384,
    volumes={"/cache": model_volume},
    secrets=[modal.Secret.from_name("huggingface-secret")],
)
class SesameWorker:
    processor = None
    model = None
    asr = None
    device: str = "cpu"
    sample_rate: int = 24000

    @modal.enter()
    def setup(self) -> None:
        import os
        import torch
        from huggingface_hub import login

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available! Aborting to save credits.")
            
        # Try different import strategies for CSM model
        try:
            from transformers import AutoProcessor, AutoModelForTextToWaveform, pipeline
            model_class = AutoModelForTextToWaveform
            print("[Sesame] Using AutoModelForTextToWaveform")
        except ImportError:
            try:
                from transformers import AutoProcessor, AutoModel, pipeline
                model_class = AutoModel
                print("[Sesame] Using AutoModel")
            except ImportError:
                raise ImportError("Could not import required transformers classes for Sesame CSM")

        _ensure_dirs()
        os.environ["HF_HOME"] = str(HF_CACHE)

        hf_token = os.environ.get("HF_TOKEN")
        if hf_token:
            print("[Sesame] Logging into Hugging Face ...")
            login(token=hf_token)
        else:
            print("[Sesame] WARNING: HF_TOKEN missing; gated downloads may fail.")

        self.device = "cuda"
        print(f"[Sesame] Loading model on {self.device}")
        print(f"[Sesame] Loading processor from {MODEL_ID}...")
        self.processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
        print(f"[Sesame] Loading model from {MODEL_ID}...")
        self.model = model_class.from_pretrained(
            MODEL_ID,
            device_map="auto" if self.device == "cuda" else None,
            trust_remote_code=True
        )
        self.sample_rate = getattr(self.processor.feature_extractor, 'sampling_rate', 24000)

        print("[Sesame] Loading ASR model for context transcription ...")
        self.asr = pipeline(
            "automatic-speech-recognition",
            model=ASR_MODEL_ID,
            device=0 if self.device == "cuda" else -1
        )
        print("[Sesame] Setup complete")

    def _prepare_context(self, voice_sample_bytes: Optional[str]) -> Optional[dict]:
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio

        if not voice_sample_bytes:
            return None
        try:
            print(f"[Sesame] Decoding reference audio (b64 length: {len(voice_sample_bytes)})")
            decoded = base64.b64decode(voice_sample_bytes)
            print(f"[Sesame] Decoded audio bytes: {len(decoded)}")

            audio_np, sr = sf.read(io.BytesIO(decoded), dtype="float32")
            print(f"[Sesame] Audio loaded: shape={audio_np.shape}, sr={sr}")

            if audio_np.ndim > 1:
                audio_np = audio_np.mean(axis=1)
                print(f"[Sesame] Converted stereo to mono: shape={audio_np.shape}")

            audio_tensor = torch.from_numpy(audio_np).unsqueeze(0)
            if sr != self.sample_rate:
                print(f"[Sesame] Resampling reference {sr} -> {self.sample_rate}")
                resampler = torchaudio.transforms.Resample(sr, self.sample_rate)
                audio_tensor = resampler(audio_tensor)

            audio_np = audio_tensor.squeeze(0).numpy()
            print(f"[Sesame] Running ASR on reference audio...")

            transcription = self.asr({
                "array": audio_np,
                "sampling_rate": self.sample_rate
            })["text"].strip()
            print(f"[Sesame] Context transcription: '{transcription}'")

            return {
                "role": "0",
                "content": [
                    {"type": "text", "text": transcription or "context"},
                    {"type": "audio", "audio": audio_np}
                ]
            }
        except Exception as exc:
            import traceback
            print(f"[Sesame] Context processing failed: {exc}")
            print(f"[Sesame] Traceback: {traceback.format_exc()}")
            raise  # Re-raise to get full error in Modal logs

    @modal.method()
    def generate(self, text: str, voice_sample_bytes: Optional[str] = None) -> bytes:
        import soundfile as sf
        import torch

        if not text:
            raise ValueError("Text is required")
        conversation = []
        ctx = self._prepare_context(voice_sample_bytes)
        if ctx:
            conversation.append(ctx)
        conversation.append({
            "role": "0",
            "content": [{"type": "text", "text": text}]
        })

        inputs = self.processor.apply_chat_template(
            conversation,
            tokenize=True,
            return_dict=True,
        ).to(self.model.device)

        with torch.no_grad():
            audio_outputs = self.model.generate(**inputs, output_audio=True)
        audio_np = audio_outputs[0]
        buffer = io.BytesIO()
        sf.write(buffer, audio_np, self.sample_rate, format="WAV")
        buffer.seek(0)
        return buffer.read()


worker = SesameWorker()


@app.function()
@modal.fastapi_endpoint(method="POST")
def generate_speech(item: Dict[str, Any]):
    from fastapi import HTTPException
    from fastapi.responses import Response

    text = (item or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    voice_sample_bytes = item.get("voice_sample_bytes")
    try:
        audio_bytes = worker.generate.remote(text=text, voice_sample_bytes=voice_sample_bytes)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=sesame.wav"}
        )
    except Exception as exc:
        print(f"[Sesame] Generation error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
