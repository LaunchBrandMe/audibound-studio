"""Modal deployment for Meta's AudioGen.

This image installs AudioCraft (AudioGen) from Meta for sound effects generation.
It supports text-to-audio generation for SFX based on descriptions.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, Any, Optional

import modal

AUDIOGEN_APP_NAME = "audibound-audiogen"
CACHE_ROOT = Path("/cache/audiogen")
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

app = modal.App(AUDIOGEN_APP_NAME, image=image)
model_volume = modal.Volume.from_name("audiogen-models", create_if_missing=True)

def _ensure_dirs() -> None:
    for path in (CACHE_ROOT, MODEL_CACHE):
        path.mkdir(parents=True, exist_ok=True)

@app.cls(
    gpu="T4",  # T4 is sufficient for AudioGen, A10G is faster but more expensive
    timeout=600,
    memory=16384,
    volumes={"/cache": model_volume},
)
class AudioGenWorker:
    @modal.enter()
    def setup(self) -> None:
        import torch
        from audiocraft.models import AudioGen
        
        _ensure_dirs()
        os.environ["AUDIOCRAFT_CACHE_DIR"] = str(MODEL_CACHE)
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[AudioGen] Loading model on {device}...")
        
        # Load AudioGen model - using small size for efficiency
        self.model = AudioGen.get_pretrained('facebook/audiogen-small')
        self.model.set_generation_params(duration=5.0)  # Default 5 seconds
        
        print("[AudioGen] Model ready")

    @modal.method()
    def generate(
        self,
        description: str,
        duration: float = 5.0,
    ) -> bytes:
        """
        Generate sound effect from text description.
        
        Args:
            description: Text description of the sound effect (e.g., "door slams shut")
            duration: Duration in seconds (default 5.0, max 30.0)
            
        Returns:
            WAV audio bytes
        """
        import torch
        import torchaudio
        
        if not description:
            raise ValueError("Description is required")
        
        # Clamp duration to reasonable limits
        duration = max(0.5, min(60.0, duration))
        
        print(f"[AudioGen] Generating SFX: '{description}' ({duration}s)")
        
        # Set generation duration
        self.model.set_generation_params(duration=duration)
        
        # Generate audio
        # AudioGen expects a list of descriptions
        wav = self.model.generate([description])
        
        # wav is a tensor of shape [batch, channels, samples]
        # We only have 1 item in batch
        audio = wav[0]
        
        # Convert to WAV bytes
        buffer = io.BytesIO()
        
        # Save as WAV format
        # AudioGen outputs at 16kHz sample rate
        torchaudio.save(
            buffer,
            audio.cpu(),
            self.model.sample_rate,
            format="wav"
        )
        
        payload = buffer.getvalue()
        
        print(f"[AudioGen] Generated {len(payload)} bytes")
        return payload

worker = AudioGenWorker()

@app.function()
@modal.fastapi_endpoint(method="POST")
def generate(item: Dict[str, Any]):
    """FastAPI endpoint for SFX generation."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    description = (item or {}).get("description", "").strip()
    duration = (item or {}).get("duration", 5.0)
    
    if not description:
        raise HTTPException(status_code=400, detail="Description is required")

    try:
        audio_bytes = worker.generate.remote(
            description=description,
            duration=duration
        )
        
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=sfx.wav"},
        )
    except Exception as e:
        print(f"Error generating SFX: {e}")
        raise HTTPException(status_code=500, detail=str(e))
