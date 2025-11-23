from typing import List, Optional, Dict, Literal, Any, Union
from pydantic import BaseModel, Field

# --- Series Bible ---
class CharacterProfile(BaseModel):
    name: str = Field(..., description="Name of the character")
    description: str = Field(..., description="Physical and personality description")
    voice_ref: str = Field(..., description="Description of the voice (e.g., 'Gruff, Scottish, Deep')")
    voice_provider_id: Optional[str] = Field(None, description="ID of the specific voice model to use")

    class Config:
        populate_by_name = True

class SeriesBible(BaseModel):
    project_title: str = Field(default="Untitled Project")
    characters: List[CharacterProfile]
    global_notes: Optional[Union[str, Dict[str, Any]]] = None

# --- ABML Structure ---

class AudioLayer(BaseModel):
    enabled: bool = True
    file_path: Optional[str] = None
    volume: float = 1.0

class VoiceLayer(AudioLayer):
    speaker: str
    voice_id: Optional[str] = None
    style: Optional[str] = None # e.g., "whispering", "shouting"
    text: str

class SfxLayer(AudioLayer):
    description: str
    category: Optional[str] = None # e.g., "ambience", "impact", "foley"

class MusicLayer(AudioLayer):
    track_id: Optional[str] = None
    style_description: str
    action: Literal["start", "stop", "fade_in", "fade_out", "sustain"] = "sustain"

class VisualLayer(BaseModel):
    prompt: str
    image_url: Optional[str] = None

class AudioBlock(BaseModel):
    """
    The atomic unit of the audiobook. 
    Usually corresponds to a sentence or a specific sound event.
    """
    id: str
    start_time_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    
    # The Layers
    narration: Optional[VoiceLayer] = None
    sfx: Optional[SfxLayer] = None
    music: Optional[MusicLayer] = None
    visual: Optional[VisualLayer] = None

class Scene(BaseModel):
    scene_id: str
    setting: str
    ambience_description: str
    blocks: List[AudioBlock]

class ScriptManifest(BaseModel):
    """
    The Master ABML File.
    """
    project_id: str
    title: str
    bible: SeriesBible
    scenes: List[Scene]
