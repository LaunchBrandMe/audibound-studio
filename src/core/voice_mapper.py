from typing import Dict, Optional
from src.core.abml import SeriesBible, CharacterProfile


class VoiceMapper:
    """
    Maps characters to appropriate Kokoro voice IDs based on their descriptions.
    Supports multiple TTS engines with different voice models.
    """
    
    # Available Kokoro voices (from Kokoro-ONNX documentation)
    KOKORO_VOICES = {
        # American Female voices
        "af_sarah": "Young, energetic American female",
        "af_bella": "Warm, mature American female",
        "af_nicole": "Neutral, professional American female",
        "af_sky": "Bright, enthusiastic American female",
        
        # American Male voices
        "am_adam": "Mature, authoritative American male",
        "am_michael": "Strong, confident American male",
        
        # British voices
        "bf_emma": "Refined British female",
        "bf_isabella": "Elegant British female",
        "bm_george": "Distinguished British male",
        "bm_lewis": "Warm British male",
    }
    
    def __init__(self, bible: SeriesBible):
        """Initialize with a Series Bible containing character profiles."""
        self.bible = bible
        self.character_voice_map: Dict[str, str] = {}
        self._assign_voices()
    
    def _assign_voices(self):
        """
        Analyze character descriptions and assign appropriate Kokoro voices.
        Uses keywords from voice_ref and description to make intelligent choices.
        """
        # Reserve neutral voice for Narrator
        self.character_voice_map["Narrator"] = "af_nicole"
        
        # Track used voices to provide variety
        used_female_voices = set()
        used_male_voices = set()
        
        for character in self.bible.characters:
            voice_id = self._select_voice_for_character(
                character, 
                used_female_voices, 
                used_male_voices
            )
            self.character_voice_map[character.name] = voice_id
            
            # Track usage
            if voice_id.startswith(('af', 'bf')):
                used_female_voices.add(voice_id)
            else:
                used_male_voices.add(voice_id)
        
        print(f"[VoiceMapper] Character voice assignments:")
        for char_name, voice_id in self.character_voice_map.items():
            print(f"  {char_name} → {voice_id}")
    
    def _select_voice_for_character(
        self, 
        character: CharacterProfile,
        used_female_voices: set,
        used_male_voices: set
    ) -> str:
        """
        Select appropriate voice based on character gender (explicit or inferred).
        Returns a Kokoro voice ID.
        """
        # Combine description and voice_ref for analysis
        text = f"{character.description} {character.voice_ref}".lower()
        
        # 1. Use explicit gender field if available (preferred!)
        if character.gender:
            gender_explicit = character.gender.lower()
            if gender_explicit == 'male':
                is_male = True
                is_female = False
            elif gender_explicit == 'female':
                is_male = False
                is_female = True
            elif gender_explicit == 'neutral':
                # Default to neutral narrator voice
                return "af_nicole"
            else:  # 'unknown'
                # Fall through to keyword inference
                is_male = None
                is_female = None
        else:
            # 2. Fallback: Infer gender from keywords (for backward compatibility)
            is_male = any(word in text for word in [
                'male', 'man', 'boy', 'father', 'brother', 'son', 'he ', 'his ', 'him '
            ])
            is_female = any(word in text for word in [
                'female', 'woman', 'girl', 'mother', 'sister', 'daughter', 'she ', 'her '
            ])
        
        # Check for British accent
        is_british = any(word in text for word in ['british', 'uk', 'london', 'english accent'])
        
        # Determine age/energy
        is_young = any(word in text for word in [
            'young', 'teen', 'child', 'youthful', 'energetic', 'enthusiastic'
        ])
        is_mature = any(word in text for word in [
            'mature', 'adult', 'middle-aged', 'elderly', 'warm', 'nurturing'
        ])
        
        # Select voice based on characteristics
        if is_male:
            if is_british:
                if 'warm' in text or 'nurturing' in text:
                    return "bm_lewis"
                return "bm_george"
            else:  # American male
                if is_mature or 'authority' in text or 'confident' in text:
                    return "am_adam"
                return "am_michael"
        
        else:  # Female (default if gender unclear)
            if is_british:
                if 'elegant' in text or 'refined' in text:
                    return "bf_isabella"
                return "bf_emma"
            else:  # American female
                if is_young and 'energetic' in text:
                    return "af_sarah" if "af_sarah" not in used_female_voices else "af_sky"
                elif is_mature or 'warm' in text or 'mother' in text or 'nurturing' in text:
                    return "af_bella"
                elif 'professional' in text or 'neutral' in text:
                    return "af_nicole"
                else:
                    # Default: young energetic
                    return "af_sarah" if "af_sarah" not in used_female_voices else "af_sky"
        
    def get_voice_for_speaker(self, speaker_name: str) -> str:
        """
        Get the assigned voice ID for a speaker.
        Falls back to neutral narrator voice if not found.
        
        Args:
            speaker_name: Name of the character/speaker
            
        Returns:
            Kokoro voice ID (e.g., "af_sarah")
        """
        return self.character_voice_map.get(speaker_name, "af_nicole")
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Return all character → voice mappings."""
        return self.character_voice_map.copy()
