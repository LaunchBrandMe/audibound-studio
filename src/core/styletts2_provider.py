from abc import ABC, abstractmethod
import httpx
import os
from typing import Optional


class VoiceProvider(ABC):
    @abstractmethod
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        pass


class StyleTTS2Provider(VoiceProvider):
    """
    Provider for StyleTTS2 with style diffusion control.
    
    StyleTTS2 uses alpha and beta parameters for style control:
    - alpha: How much to use style from reference (0.0-1.0)
    - beta: How much to use diffusion (0.0-1.0)
    """
    
    def __init__(self, modal_url: str):
        self.modal_url = modal_url
    
    def _style_to_params(self, style: Optional[str]) -> dict:
        """
        Map ABML style to StyleTTS2 alpha/beta parameters.
        
        Alpha controls reference style influence
        Beta controls diffusion amount (more beta = more expressive)
        
        Returns: dict with 'alpha' and 'beta'
        """
        if not style:
            # Neutral: moderate diffusion, low style
            return {'alpha': 0.2, 'beta': 0.5}
        
        style_lower = style.lower()
        
        # High energy emotions - more diffusion
        if any(word in style_lower for word in ['excited', 'happy', 'cheerful', 'joyful']):
            return {'alpha': 0.4, 'beta': 0.8}  # High diffusion for expressiveness
        
        # Angry/Shouting - max diffusion
        elif any(word in style_lower for word in ['angry', 'furious', 'shout', 'yell']):
            return {'alpha': 0.5, 'beta': 0.9}
        
        # Sad/Tired - moderate diffusion
        elif any(word in style_lower for word in ['sad', 'melancholy', 'tired', 'weary']):
            return {'alpha': 0.3, 'beta': 0.6}
        
        # Calm/Whisper - low diffusion
        elif any(word in style_lower for word in ['calm', 'quiet', 'whisper', 'soft']):
            return {'alpha': 0.2, 'beta': 0.3}
        
        # Surprised - high diffusion
        elif any(word in style_lower for word in ['surprised', 'shocked', 'astonished']):
            return {'alpha': 0.4, 'beta': 0.7}
        
        # Default: moderate
        return {'alpha': 0.3, 'beta': 0.5}
    
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        """
        Generate audio using StyleTTS2 via Modal endpoint.
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier (not used for StyleTTS2 single-speaker model)
            speed: Speech speed multiplier (StyleTTS2 doesn't directly support this)
            style: ABML style (converted to alpha/beta)
        
        Returns:
            WAV audio bytes
        """
        # Convert style to alpha/beta
        params = self._style_to_params(style)
        
        async with httpx.AsyncClient(follow_redirects=True) as client:
            payload = {
                "text": text,
                "alpha": params['alpha'],
                "beta": params['beta']
            }
            
            if style:
                print(f"[StyleTTS2] Generating with style '{style}': alpha={params['alpha']}, beta={params['beta']}")
            else:
                print(f"[StyleTTS2] Generating neutral speech")
            
            response = await client.post(self.modal_url, json=payload, timeout=180.0)  # 3 minutes
            print(f"[StyleTTS2] Response Status: {response.status_code}")
            response.raise_for_status()
            content = response.content
            
            # Validate audio data
            if len(content) < 100:
                print(f"[StyleTTS2] Response too small: {len(content)} bytes")
                raise ValueError(f"Audio response too small ({len(content)} bytes)")
            
            # Check WAV format
            if not content.startswith(b'RIFF'):
                print(f"[StyleTTS2] WARNING: Response doesn't look like a WAV file")
                raise ValueError("Invalid audio format received from StyleTTS2")
            
            print(f"[StyleTTS2] Received {len(content)} bytes")
            return content
