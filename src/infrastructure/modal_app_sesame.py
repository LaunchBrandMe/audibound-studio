"""Modal deployment for Sesame CSM 1B.

This image installs the Sesame CSM model and its dependencies.
It requires a Hugging Face token to be available in the 'huggingface-secret' secret.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, Any, Optional

import modal

SESAME_APP_NAME = "audibound-sesame"
CACHE_ROOT = Path("/cache/sesame")
HF_CACHE = CACHE_ROOT / "huggingface"

# Define the image
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg")
    .pip_install(
        "torch==2.1.2",
        "torchaudio==2.1.2",
        "transformers==4.39.3", # Pinned based on recent compatibility
        "huggingface_hub",
        "scipy",
        "numpy",
        "einops",
        "tqdm",
        "librosa",
        "soundfile",
        "fastapi"
    )
    .run_commands(
        "git clone https://github.com/SesameAILabs/csm.git /csm",
        "cd /csm && pip install -r requirements.txt"
    )
    .env({"PYTHONPATH": "/csm"})
)

app = modal.App(SESAME_APP_NAME, image=image)
model_volume = modal.Volume.from_name("sesame-models", create_if_missing=True)

def _ensure_dirs() -> None:
    for path in (CACHE_ROOT, HF_CACHE):
        path.mkdir(parents=True, exist_ok=True)

@app.cls(
    gpu="T4", # Sesame runs on T4, though A10G is better if available. T4 is cheaper/more available.
    timeout=600,
    memory=16384,
    volumes={"/cache": model_volume},
    secrets=[modal.Secret.from_name("huggingface-secret")],
)
class SesameWorker:
    @modal.enter()
    def setup(self) -> None:
        import os
        from huggingface_hub import login
        
        _ensure_dirs()
        os.environ["HF_HOME"] = str(HF_CACHE)
        
        # Authenticate with Hugging Face
        hf_token = os.environ.get("HF_TOKEN")
        if hf_token:
            print("[Sesame] Logging in to Hugging Face...")
            login(token=hf_token)
        else:
            print("[Sesame] WARNING: HF_TOKEN not found in environment. Model download may fail if gated.")

        # Import here to ensure PYTHONPATH is set and deps are ready
        try:
            from generator import load_csm_1b, Segment
        except ImportError:
            import sys
            sys.path.append("/csm")
            from generator import load_csm_1b, Segment
        
        # Store Segment class for use in generate
        self.Segment = Segment

        import torch
        from transformers import pipeline
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Sesame] Loading model on {device}...")
        self._generator = load_csm_1b(device=device)
        
        # Load Whisper for transcription (needed for voice cloning context)
        print("[Sesame] Loading Whisper for transcription...")
        self.transcriber = pipeline(
            "automatic-speech-recognition", 
            model="openai/whisper-tiny.en", 
            device=device
        )
        print("[Sesame] Models ready")

    @modal.method()
    def generate(
        self,
        text: str,
        voice_sample_url: Optional[str] = None,
        voice_sample_bytes: Optional[str] = None,  # Base64 encoded
        speaker_id: int = 0,
    ) -> bytes:
        import torch
        import torchaudio
        import numpy as np
        import base64
        import tempfile
        import os
        
        if not text:
            raise ValueError("Text is required")

        print(f"[Sesame] Generating text: '{text}'")
        
        # Handle voice cloning
        context = []
        if voice_sample_bytes or voice_sample_url:
            print("[Sesame] Voice cloning mode enabled")
            
            try:
                # Load reference audio
                tmp_path = None
                if voice_sample_bytes:
                    print("[Sesame] Decoding base64 reference audio...")
                    audio_data = base64.b64decode(voice_sample_bytes)
                    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                        tmp.write(audio_data)
                        tmp_path = tmp.name
                elif voice_sample_url:
                    print(f"[Sesame] Downloading reference from URL...")
                    import requests
                    response = requests.get(voice_sample_url)
                    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                        tmp.write(response.content)
                        tmp_path = tmp.name
                
                if tmp_path:
                    # 1. Transcribe audio to get text (required for Segment)
                    print("[Sesame] Transcribing reference audio...")
                    transcription = self.transcriber(tmp_path)["text"].strip()
                    print(f"[Sesame] Transcription: '{transcription}'")
                    
                    # 2. Load audio for context
                    reference_audio, sr = torchaudio.load(tmp_path)
                    
                    # Resample to 24kHz (required by CSM)
                    target_sr = 24000
                    if sr != target_sr:
                        print(f"[Sesame] Resampling from {sr}Hz to {target_sr}Hz...")
                        resampler = torchaudio.transforms.Resample(sr, target_sr)
                        reference_audio = resampler(reference_audio)
                    
                    # Convert to mono if stereo
                    if reference_audio.shape[0] > 1:
                        reference_audio = reference_audio.mean(dim=0, keepdim=True)
                    
                    # Move to correct device
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                    reference_audio = reference_audio.to(device)
                    
                    # 3. Create Segment object
                    # Audio expects 1D tensor (remove channel dim)
                    audio_tensor = reference_audio.squeeze(0)
                    
                    segment = self.Segment(
                        speaker=speaker_id,
                        text=transcription,
                        audio=audio_tensor
                    )
                    context = [segment]
                    print(f"[Sesame] Created context Segment with transcription")
                    
                    # Cleanup
                    os.unlink(tmp_path)
                
            except Exception as e:
                print(f"[Sesame] WARNING: Failed to process reference audio: {e}")
                print("[Sesame] Falling back to default voice")
                context = []
        
        # Generate audio with cloning context or default voice
        print(f"[Sesame] Calling generate with text='{text}', speaker={speaker_id}, context_len={len(context)}")
        
        audio = self._generator.generate(
            text=text,
            speaker=speaker_id,
            context=context,
            max_audio_length_ms=30_000,
        )
        
        # Convert to WAV bytes
        buffer = io.BytesIO()
        audio_cpu = audio.unsqueeze(0).cpu()
        
        torchaudio.save(buffer, audio_cpu, self._generator.sample_rate, format="wav")
        payload = buffer.getvalue()
        
        print(f"[Sesame] Generated {len(payload)} bytes")
        return payload

worker = SesameWorker()

@app.function()
@modal.fastapi_endpoint(method="POST")
def generate_speech(item: Dict[str, Any]):
    from fastapi import HTTPException
    from fastapi.responses import Response

    text = (item or {}).get("text", "").strip()
    voice_sample_url = (item or {}).get("voice_sample_url")
    voice_sample_bytes = (item or {}).get("voice_sample_bytes")
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    try:
        audio_bytes = worker.generate.remote(
            text,
            voice_sample_url=voice_sample_url,
            voice_sample_bytes=voice_sample_bytes
        )
        
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=sesame.wav"},
        )
    except Exception as e:
        print(f"Error generating speech: {e}")
        raise HTTPException(status_code=500, detail=str(e))
