"""Modal deployment for Meta's MusicGen.

This image installs AudioCraft (MusicGen) from Meta for music generation.
It supports text-to-music generation for background music based on style descriptions.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, Any, Optional

import modal

MUSICGEN_APP_NAME = "audibound-musicgen"
CACHE_ROOT = Path("/cache/musicgen")
MODEL_CACHE = CACHE_ROOT / "models"

# Define the image
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install(
        "git",
        "ffmpeg",
        "pkg-config",
        "libavformat-dev",
        "libavcodec-dev",
        "libavdevice-dev",
        "libavutil-dev",
        "libswscale-dev",
        "libswresample-dev",
        "libavfilter-dev"
    )
    .pip_install(
        "numpy<2",
        "torch==2.1.2",
        "torchaudio==2.1.2",
        extra_options="--extra-index-url https://download.pytorch.org/whl/cu121"
    )
    .pip_install(
        "audiocraft==1.3.0",
        "transformers==4.38.2",
        "scipy",
        "xformers",
        "fastapi"
    )
)

app = modal.App(MUSICGEN_APP_NAME, image=image)
model_volume = modal.Volume.from_name("musicgen-models", create_if_missing=True)

def _ensure_dirs() -> None:
    for path in (CACHE_ROOT, MODEL_CACHE):
        path.mkdir(parents=True, exist_ok=True)

@app.cls(
    gpu="A10G",  # MusicGen benefits from A10G for faster generation
    timeout=600,
    memory=24576,  # 24GB for larger music model
    volumes={"/cache": model_volume},
)
class MusicGenWorker:
    @modal.enter()
    def setup(self) -> None:
        import torch
        from audiocraft.models import MusicGen
        
        _ensure_dirs()
        os.environ["AUDIOCRAFT_CACHE_DIR"] = str(MODEL_CACHE)
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[MusicGen] Loading model on {device}...")
        
        # Load MusicGen model - using small for efficiency (Kokoro-like speed)
        # Options: small (300M), medium (1.5B), large (3.3B)
        self.model = MusicGen.get_pretrained('facebook/musicgen-small')
        self.model.set_generation_params(duration=30.0)  # Default 30 seconds
        
        print("[MusicGen] Model ready")

    @modal.method()
    def generate(
        self,
        style_description: str,
        duration: float = 10.0,
        melody_audio: Optional[bytes] = None,
    ) -> bytes:
        """
        Generate background music from style description.
        
        Args:
            style_description: Text description of music style 
                              (e.g., "suspenseful orchestral strings")
            duration: Duration in seconds (default 10.0, max 60.0)
            melody_audio: Optional melody conditioning audio (WAV bytes)
            
        Returns:
            WAV audio bytes
        """
        import torch
        import torchaudio
        
        if not style_description:
            raise ValueError("Style description is required")
        
        # Clamp duration to reasonable limits
        duration = max(1.0, min(300.0, duration))
        
        print(f"[MusicGen] Generating music: '{style_description}' ({duration}s)")
        
        # Set generation duration
        self.model.set_generation_params(duration=duration)
        
        # TODO: Add melody conditioning support if melody_audio is provided
        # For now, just do text-to-music
        
        # Generate music
        # MusicGen expects a list of descriptions
        wav = self.model.generate([style_description])
        
        # wav is a tensor of shape [batch, channels, samples]
        # We only have 1 item in batch
        audio = wav[0]
        
        # Convert to WAV bytes
        buffer = io.BytesIO()
        
        # Save as WAV format
        # MusicGen outputs at 32kHz sample rate
        torchaudio.save(
            buffer,
            audio.cpu(),
            self.model.sample_rate,
            format="wav"
        )
        
        payload = buffer.getvalue()
        
        print(f"[MusicGen] Generated {len(payload)} bytes")
        return payload

worker = MusicGenWorker()

@app.function()
@modal.fastapi_endpoint(method="POST")
def generate(item: Dict[str, Any]):
    """FastAPI endpoint for music generation."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    style_description = (item or {}).get("style_description", "").strip()
    duration = (item or {}).get("duration", 10.0)
    melody_audio = (item or {}).get("melody_audio")  # Optional
    
    if not style_description:
        raise HTTPException(status_code=400, detail="Style description is required")

    try:
        audio_bytes = worker.generate.remote(
            style_description=style_description,
            duration=duration,
            melody_audio=melody_audio
        )
        
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=music.wav"},
        )
    except Exception as e:
        print(f"Error generating music: {e}")
        raise HTTPException(status_code=500, detail=str(e))
