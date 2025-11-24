import os
import json
import google.generativeai as genai
from typing import List, Optional
from src.core.abml import SeriesBible, Scene, ScriptManifest, CharacterProfile
from src.core.validator import validate_and_log
from dotenv import load_dotenv

load_dotenv()

class ScriptDirector:
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash-preview-09-2025"):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY is not set")
        
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={"response_mime_type": "application/json"}
        )

    def create_series_bible(self, text_chunk: str, project_title: str) -> SeriesBible:
        """
        Analyzes the text to extract characters and global style notes.
        """
        prompt = f"""
        You are an expert Audio Drama Director. 
        Analyze the following text from the story "{project_title}".
        
        Your goal is to create a "Series Bible" that lists all characters found in the text.
        For each character, provide:
        1. Name
        2. Physical/Personality Description
        3. Voice Reference (e.g., "Deep, raspy, British accent, similar to Alan Rickman")
        4. Gender - IMPORTANT: Explicitly determine the character's gender:
           - "male" for male characters (he/him, father, brother, son, man, boy, etc.)
           - "female" for female characters (she/her, mother, sister, daughter, woman, girl, etc.)
           - "neutral" for non-binary or gender-neutral narrators
           - "unknown" if genuinely unclear from the text
        
        Also provide global notes on the tone/atmosphere.
        
        Output valid JSON matching the SeriesBible schema.
        
        Text:
        {text_chunk[:50000]} 
        """
        
        response = self.model.generate_content(prompt)
        
        try:
            # Parse JSON and validate with Pydantic
            # Parse JSON
            data = json.loads(response.text)

            # 0. Handle list output (sometimes returns [bible])
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    data = data[0]
                else:
                    raise ValueError(f"Unexpected list format from LLM: {data}")
            
            # Normalization for Gemini 2.5 quirks (camelCase vs snake_case vs verbose names)
            characters = data.get('characters') or data.get('Characters') or []
            for char in characters:
                # Normalize name (Name -> name)
                if 'Name' in char:
                    char['name'] = char.pop('Name')
                    
                # Normalize voice_ref
                if 'voiceReference' in char:
                    char['voice_ref'] = char.pop('voiceReference')
                elif 'voice_reference' in char:
                    char['voice_ref'] = char.pop('voice_reference')
                elif 'VoiceReference' in char:
                    char['voice_ref'] = char.pop('VoiceReference')
                
                # Normalize description
                if 'physical_personality_description' in char:
                    char['description'] = char.pop('physical_personality_description')
                elif 'physicalDescription' in char:
                    char['description'] = char.pop('physicalDescription')
                elif 'PhysicalDescription' in char:
                    char['description'] = char.pop('PhysicalDescription')
                elif 'Description' in char:
                    char['description'] = char.pop('Description')
                
                # Normalize gender
                if 'Gender' in char:
                    char['gender'] = char.pop('Gender').lower()
                elif 'gender' not in char:
                    char['gender'] = None  # Will be inferred by VoiceMapper
            
            # Ensure characters list is set correctly in data
            data['characters'] = characters

            # Handle global notes variations
            if 'globalNotes' in data:
                data['global_notes'] = data.pop('globalNotes')
            elif 'GlobalNotes' in data:
                data['global_notes'] = data.pop('GlobalNotes')
            elif 'globalAtmosphereNotes' in data:
                 data['global_notes'] = data.pop('globalAtmosphereNotes')

            # Ensure project title
            data['project_title'] = project_title 
            
            print(f"DEBUG: Normalized Data: {json.dumps(data, indent=2)}")
            
            return SeriesBible(**data)
        except Exception as e:
            print(f"Error parsing Series Bible: {e}")
            print(f"Raw response: {response.text}")
            raise

    def direct_scene(self, scene_text: str, bible: SeriesBible, scene_id: str = "1") -> Scene:
        """
        Directs a single scene: segments text into blocks, assigns voices, and adds SFX/Music.
        """
        bible_context = bible.model_dump_json()
        
        prompt = f"""
        You are an expert Audio Drama Director extracting clean dialogue and narration.
        
        Context (Series Bible):
        {bible_context}
        
        Task:
        Convert the following scene text into structured Audio Script (ABML).
        
        **CRITICAL RULES - READ CAREFULLY**:
        
        1. DIALOGUE EXTRACTION:
           ❌ WRONG: {{"text": "Sarah shouted excitedly, 'I got the callback!'"}}
           ✅ RIGHT: {{"text": "I got the callback!", "style": "excited"}}
           
           ❌ WRONG: {{"text": "she said quietly"}}
           ✅ RIGHT: {{"text": "[actual dialogue]", "style": "quiet"}}
           
           Rules:
           - Extract ONLY words inside quotation marks
           - NEVER include: "said", "shouted", "whispered", "exclaimed", "asked", "replied"
           - Move emotion/delivery to "style" field
        
        2. NARRATION EXTRACTION:
           ❌ WRONG: {{"text": "Maya sighed heavily, sinking into the couch"}}
           ✅ RIGHT: {{"text": "Maya sank into the couch.", "style": "weary"}}
           
           Rules:
           - Remove emotion adverbs (heavily, quietly, angrily)
           - Move emotions to "style" field
           - Keep only clean action descriptions
        
        3. STYLE FIELD:
           Use these exact words when appropriate:
           - "excited", "cheerful", "happy", "joyful"
           - "sad", "somber", "melancholy", "weary"  
           - "angry", "furious", "harsh"
           - "whispering", "quiet", "soft"
           - "urgent", "rushed", "hurried"
           - Leave empty if neutral tone
        
        4. OUTPUT FORMAT:
           For dialogue: {{"type": "dialogue", "speaker": "Name", "text": "clean dialogue only", "style": "emotion"}}
           For narration: {{"type": "narration", "speaker": "Narrator", "text": "clean description", "style": "emotion"}}
           For SFX: {{"type": "sfx", "effect": "door slams"}}
           For music: {{"type": "music", "cue": "suspenseful strings"}}
        
        **EXAMPLES**:
        
        Input: 'Sarah burst through the door. "I got it!" she shouted excitedly.'
        Output:
        [
          {{"type": "narration", "speaker": "Narrator", "text": "Sarah burst through the door."}},
          {{"type": "dialogue", "speaker": "Sarah", "text": "I got it!", "style": "excited"}}
        ]
        
        Input: 'Tom sighed heavily. "This is terrible," he whispered.'  
        Output:
        [
          {{"type": "narration", "speaker": "Narrator", "text": "Tom sighed.", "style": "weary"}},
          {{"type": "dialogue", "speaker": "Tom", "text": "This is terrible.", "style": "whispering"}}
        ]
        
        **REMEMBER**: 
        - NO "said/shouted/whispered" in text!
        - Clean dialogue ONLY!
        - Emotions go in "style" field!
        
        Output valid JSON matching Scene schema.
        
        Scene Text:
        {scene_text}
        """
        
        response = self.model.generate_content(prompt)
        
        print(f"[Director] Raw Gemini scene response: {response.text[:500]}...")
        
        try:
            data = json.loads(response.text)
        
            # Normalization for Gemini 2.5 quirks
            
            # 0a. Handle bare array (Gemini returned just the blocks, not a Scene object)
            if isinstance(data, list):
                print(f"[Director] Gemini returned bare array of blocks, wrapping in Scene structure")
                data = {
                    "setting": "Scene",
                    "blocks": data
                }
            
            # 0b. Handle nested list output (sometimes returns [scene])
            elif isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    data = data[0]
                else:
                    raise ValueError(f"Unexpected list format from LLM: {data}")
            
            # 1. Handle missing setting
            if 'setting' not in data:
                data['setting'] = data.get('scene_title') or data.get('sceneTitle') or "Unknown Setting"

            # 2. Handle missing ambience_description
            if 'ambience_description' not in data:
                data['ambience_description'] = data.get('ambienceDescription') or data.get('setting', "General Ambience")

            # 3. Handle block_id -> id AND generate missing IDs AND map content to ABML layers
            new_blocks = []
            for i, block in enumerate(data.get('blocks', [])):
                # ID Handling
                if 'block_id' in block:
                    b_id = str(block.pop('block_id'))
                elif 'blockId' in block:
                    b_id = str(block.pop('blockId'))
                elif 'id' in block:
                    b_id = str(block['id'])
                else:
                    b_id = f"block_{i+1}"
                
                # Create base block
                abml_block = {"id": b_id}
                
                # Map Content based on 'type'
                b_type = block.get('type', '').lower()
                
                # FALLBACK: If no type, infer from content
                if not b_type:
                    if block.get('line') or block.get('text') or block.get('voice_id') or block.get('voiceId') or block.get('speaker'):
                        b_type = 'dialogue'
                    elif block.get('effect') or block.get('action'):
                        b_type = 'sfx'
                    elif block.get('cue') or block.get('styleDescription'):
                        b_type = 'music'
                
                if b_type in ['dialogue', 'narration']:
                    text_content = block.get('line') or block.get('text') or ''
                    abml_block['narration'] = {
                        "speaker": block.get('speaker') or block.get('voice_id') or block.get('voiceId') or 'Narrator',
                        "text": text_content,
                        "style": block.get('style'),
                        "enabled": True
                    }
                    if text_content:
                        print(f"[Director] Block {b_id}: Added narration text (first 50 chars): {text_content[:50]}...")
                    else:
                        print(f"[Director] WARNING: Block {b_id} has NO TEXT!")
                elif b_type == 'sfx':
                    abml_block['sfx'] = {
                        "description": block.get('action') or block.get('effect') or block.get('description') or "SFX",
                        "category": "sfx",
                        "enabled": True
                    }
                elif b_type == 'music':
                    abml_block['music'] = {
                        "style_description": block.get('action') or block.get('cue') or block.get('styleDescription') or "Music",
                        "action": "start", # Default to start/sustain
                        "enabled": True
                    }
                
                new_blocks.append(abml_block)
            
            data['blocks'] = new_blocks
            
            # Inject the scene_id if the LLM generated a random one or to enforce consistency
            data['scene_id'] = scene_id
            
            # Create scene object
            scene = Scene(**data)
            
            # Validate ABML quality
            validation_result = validate_and_log(scene, scene_id)
            
            # Log warning if quality is poor (but still return scene)
            if not validation_result.is_passing():
                print(f"[Director] ⚠️  Scene quality below threshold but proceeding anyway")
            
            return scene
        except Exception as e:
            print(f"Error parsing Scene: {e}")
            print(f"Raw response: {response.text}")
            raise
