"""Modal deployment for Dia 1.6B zero-shot TTS (Nari Labs)."""

from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

import modal

APP_NAME = "audibound-dia"
MODEL_REPO = "nari-labs/Dia-1.6B"
MODEL_FILENAME = "dia-v0_1.pth"
CONFIG_FILENAME = "config.json"
CACHE_ROOT = Path("/cache/dia")
MODEL_DIR = CACHE_ROOT / MODEL_REPO.replace("/", "_")
SAMPLE_RATE = 44100

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "git")
    .pip_install(
        "torch==2.1.2",
        "torchaudio==2.1.2",
        extra_options="--extra-index-url https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "numpy",
        "soundfile",
        "structlog",
        "huggingface_hub",
        "pydub",
        "fastapi",
        "descript-audio-codec",
        "git+https://github.com/nari-labs/dia.git"
    )
)

app = modal.App(APP_NAME, image=image)
model_volume = modal.Volume.from_name("dia-models", create_if_missing=True)


def _ensure_dirs() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


@app.cls(
    gpu="A10G",
    timeout=600,
    memory=32768,
    volumes={"/cache": model_volume},
    secrets=[modal.Secret.from_name("huggingface-secret")],
)
class DiaWorker:
    def __init__(self) -> None:
        self.model = None
        self.device = "cpu"

    @modal.enter()
    def setup(self) -> None:
        import torch
        from huggingface_hub import login, hf_hub_download
        from dia.model import Dia

        _ensure_dirs()
        os.environ["HF_HOME"] = str(CACHE_ROOT)
        token = os.environ.get("HF_TOKEN")
        if token:
            login(token=token)
        else:
            print("[Dia] WARNING: HF_TOKEN not provided; gated downloads may fail")

        ckpt = hf_hub_download(MODEL_REPO, MODEL_FILENAME, local_dir=MODEL_DIR)
        cfg = hf_hub_download(MODEL_REPO, CONFIG_FILENAME, local_dir=MODEL_DIR)
        model_volume.commit()

        if torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        print(f"[Dia] Loading model on {self.device}")
        
        # Manually load and fix config structure to avoid validation errors
        import json
        from dia.config import DiaConfig, EncoderConfig, DecoderConfig
        
        with open(cfg, 'r') as f:
            config_dict = json.load(f)
        
        # Map old nested config format to new flat config
        encoder_data = config_dict["model"]["encoder"]
        decoder_data = config_dict["model"]["decoder"]
        
        # Create encoder config with proper field names
        encoder_config = EncoderConfig(
            head_dim=encoder_data.get("head_dim", 128),
            hidden_size=encoder_data.get("n_embd", 1024),
            intermediate_size=encoder_data.get("n_hidden", 4096),
            num_hidden_layers=encoder_data.get("n_layer", 12),
            num_attention_heads=encoder_data.get("n_head", 16),
            num_key_value_heads=encoder_data.get("n_head", 16),
        )
        
        # Create decoder config with proper field names
        decoder_config = DecoderConfig(
            cross_head_dim=decoder_data.get("cross_head_dim", 128),
            cross_num_attention_heads=decoder_data.get("cross_query_heads", 16),
            head_dim=decoder_data.get("gqa_head_dim", 128),
            hidden_size=decoder_data.get("n_embd", 2048),
            intermediate_size=decoder_data.get("n_hidden", 8192),
            num_hidden_layers=decoder_data.get("n_layer", 18),
            num_attention_heads=decoder_data.get("gqa_query_heads", 16),
            num_key_value_heads=decoder_data.get("kv_heads", 4),
        )
        
        # Create master config
        dia_config = DiaConfig(
            encoder_config=encoder_config,
            decoder_config=decoder_config,
            bos_token_id=config_dict["data"]["audio_bos_value"],
            eos_token_id=config_dict["data"]["audio_eos_value"],
            pad_token_id=config_dict["data"]["audio_pad_value"],
            delay_pattern=config_dict["data"]["delay_pattern"],
        )
        
        # Now initialize model with proper config
        from dia.model import Dia
        compute_dtype_str = "bfloat16" if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else "float32"
        
        # Load the Dia model directly from config and checkpoint
        self.model = Dia(dia_config, device=self.device, compute_dtype=compute_dtype_str)
        
        # Load checkpoint into the underlying DiaModel
        state_dict = torch.load(ckpt, map_location=self.device)
        self.model.model.load_state_dict(state_dict)
        self.model.model = self.model.model.to(self.device)
        self.model.model.eval()
        
        # Explicitly load DAC model since we are manually initializing
        if self.model.load_dac:
            print("[Dia] Loading DAC model...")
            self.model._load_dac_model()
            
        print("[Dia] Model ready")

    def _prepare_prompt(self, voice_sample_b64: Optional[str]) -> Optional[str]:
        if not voice_sample_b64:
            return None
        import numpy as np
        import soundfile as sf

        decoded = base64.b64decode(voice_sample_b64)
        audio, sr = sf.read(io.BytesIO(decoded), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=-1)
        if sr != SAMPLE_RATE:
            import torchaudio
            import torch

            tensor = torch.from_numpy(audio).unsqueeze(0)
            resampler = torchaudio.transforms.Resample(sr, SAMPLE_RATE)
            audio = resampler(tensor).squeeze(0).numpy()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        sf.write(tmp.name, audio, SAMPLE_RATE)
        tmp.flush()
        return tmp.name

    @modal.method()
    def generate(self, text: str, voice_sample_b64: Optional[str] = None, hyperparams: Optional[Dict[str, Any]] = None) -> bytes:
        import numpy as np
        import soundfile as sf

        if not text:
            raise ValueError("Text is required")

        print(f"[Dia] Generating audio for text: '{text[:100]}...'")

        hyperparams = hyperparams or {}
        defaults = {
            "max_new_tokens": 3000,  # Reduced to stay within audio_length=3072 limit
            "cfg_scale": 3.0,
            "temperature": 1.0,  # Lowered from 1.3 for better stability
            "top_p": 0.95,
            "cfg_filter_top_k": 30,
            "speed_factor": 0.94,
        }
        params = {**defaults, **hyperparams}
        print(f"[Dia] Hyperparams: {params}")

        prompt_path = self._prepare_prompt(voice_sample_b64)
        try:
            print(f"[Dia] Calling model.generate()...")
            audio = self.model.generate(
                text=text,
                max_tokens=params["max_new_tokens"],
                cfg_scale=params["cfg_scale"],
                temperature=params["temperature"],
                top_p=params["top_p"],
                use_cfg_filter=True,
                cfg_filter_top_k=params["cfg_filter_top_k"],
                audio_prompt_path=prompt_path,
            )
            print(f"[Dia] Generation complete, audio type: {type(audio)}")
        except Exception as e:
            import traceback
            print(f"[Dia] Generation failed: {e}")
            print(f"[Dia] Traceback: {traceback.format_exc()}")
            raise
        finally:
            if prompt_path and os.path.exists(prompt_path):
                os.unlink(prompt_path)

        if audio is None:
            raise RuntimeError("Dia returned no audio")

        audio_np = np.asarray(audio, dtype=np.float32)
        if params["speed_factor"] != 1.0:
            orig = len(audio_np)
            target = int(orig / params["speed_factor"])
            if target > 0 and target != orig:
                x_orig = np.arange(orig)
                x_target = np.linspace(0, orig - 1, target)
                audio_np = np.interp(x_target, x_orig, audio_np).astype(np.float32)

        buffer = io.BytesIO()
        sf.write(buffer, audio_np, SAMPLE_RATE, format="WAV")
        return buffer.getvalue()


worker = DiaWorker()


@app.function()
@modal.fastapi_endpoint(method="POST")
def generate_speech(item: Dict[str, Any]):
    from fastapi import HTTPException
    from fastapi.responses import Response

    text = (item or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    voice_sample_b64 = item.get("voice_sample_bytes")
    hyper = item.get("hyperparameters")
    try:
        audio_bytes = worker.generate.remote(text=text, voice_sample_b64=voice_sample_b64, hyperparams=hyper)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=dia.wav"}
        )
    except Exception as exc:
        print(f"[Dia] Generation error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
