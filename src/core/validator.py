"""
ABML Quality Validator

Validates ABML output from LLMs to ensure:
- Clean dialogue extraction (no stage directions)
- All text blocks have content
- Proper structure
- Quality scoring
"""

from typing import List, Tuple, Dict
from src.core.abml import Scene, AudioBlock

class ValidationResult:
    def __init__(self):
        self.score = 100
        self.issues = []
        self.warnings = []
        self.clarifications = []
    
    def add_error(self, message: str, severity: int = 20):
        """Add an error (deducts from score)"""
        self.issues.append(f"❌ {message}")
        self.score = max(0, self.score - severity)
    
    def add_warning(self, message: str, severity: int = 10):
        """Add a warning (minor score deduction)"""
        self.warnings.append(f"⚠️  {message}")
        self.score = max(0, self.score - severity)

    def add_clarification(self, block_index: int, speaker: str, text: str, reason: str):
        """Track lines that may need human clarification/editing."""
        snippet = text.strip()
        if len(snippet) > 140:
            snippet = snippet[:137] + "..."
        self.clarifications.append({
            "block_index": block_index,
            "speaker": speaker,
            "text": snippet,
            "reason": reason
        })
    
    def is_passing(self, threshold: int = 70) -> bool:
        """Check if quality meets threshold"""
        return self.score >= threshold
    
    def __str__(self):
        status = "✅ PASS" if self.is_passing() else "❌ FAIL"
        return f"{status} - Quality Score: {self.score}/100"


class ABMLValidator:
    """Validates ABML scenes for quality issues"""
    
    # Words that shouldn't appear in dialogue/narration text
    STAGE_DIRECTION_WORDS = [
        'said', 'says', 'saying',
        'shouted', 'shouts', 'shouting',
        'whispered', 'whispers', 'whispering',
        'exclaimed', 'exclaims', 'exclaiming',
        'asked', 'asks', 'asking',
        'replied', 'replies', 'replying',
        'murmured', 'murmurs', 'murmuring',
        'yelled', 'yells', 'yelling',
        'screamed', 'screams', 'screaming',
        'muttered', 'mutters', 'muttering',
        'called', 'calls', 'calling',
        'cried', 'cries', 'crying'
    ]
    
    # Adverbs that suggest stage directions
    EMOTION_ADVERBS = [
        'angrily', 'sadly', 'happily', 'cheerfully',
        'excitedly', 'nervously', 'quietly', 'loudly',
        'softly', 'harshly', 'gently', 'urgently'
    ]
    
    def validate_scene(self, scene: Scene) -> ValidationResult:
        """
        Validate entire scene for quality issues.
        Returns ValidationResult with score and issues.
        """
        result = ValidationResult()
        
        if not scene.blocks:
            result.add_error("Scene has no blocks", severity=50)
            return result
        
        for i, block in enumerate(scene.blocks):
            self._validate_block(block, i, result)
        
        # Summary warnings
        total_blocks = len(scene.blocks)
        narration_blocks = sum(1 for b in scene.blocks if b.narration)
        
        if narration_blocks == 0:
            result.add_warning("No narration/dialogue blocks found")
        
        if narration_blocks < total_blocks * 0.5:
            result.add_warning(f"Only {narration_blocks}/{total_blocks} blocks have narration")
        
        return result
    
    def _validate_block(self, block: AudioBlock, index: int, result: ValidationResult):
        """Validate a single audio block"""
        
        if not block.narration:
            return  # SFX/Music blocks don't need text validation
        
        text = block.narration.text
        speaker = block.narration.speaker
        
        # Check 1: Empty text
        if not text or not text.strip():
            result.add_error(f"Block {index} ({speaker}): Empty text", severity=30)
            return
        
        # Check 2: Stage directions in text
        text_lower = text.lower()
        found_directions = []
        
        for word in self.STAGE_DIRECTION_WORDS:
            if f" {word} " in f" {text_lower} ":
                found_directions.append(word)
        
        if found_directions:
            result.add_error(
                f"Block {index} ({speaker}): Stage directions found: {', '.join(found_directions)}",
                severity=20
            )
            # Add clarification entry so UI can surface it for rewrite
            result.add_clarification(index, speaker, text, f"Contains: {', '.join(found_directions)}")
        
        # Check 3: Emotion adverbs (less severe)
        found_adverbs = []
        for adverb in self.EMOTION_ADVERBS:
            if adverb in text_lower:
                found_adverbs.append(adverb)
        
        if found_adverbs:
            result.add_warning(
                f"Block {index} ({speaker}): Emotion words in text: {', '.join(found_adverbs)}",
                severity=5
            )
        
        # Check 4: Very long blocks (might be unparsed stage directions)
        if len(text.split()) > 100:
            result.add_warning(
                f"Block {index} ({speaker}): Very long block ({len(text.split())} words)",
                severity=5
            )
        
        # Check 5: Quoted text when it shouldn't be (narration with quotes)
        if speaker == "Narrator" and text.startswith('"') and text.endswith('"'):
            result.add_warning(
                f"Block {index}: Narrator has quoted text (might be dialogue)",
                severity=5
            )


def validate_and_log(scene: Scene, scene_id: str = "unknown") -> ValidationResult:
    """
    Validate scene and log results.
    Convenience function for use in director.
    """
    validator = ABMLValidator()
    result = validator.validate_scene(scene)
    
    print(f"\n[Validation] Scene '{scene_id}' - {result}")
    
    if result.issues:
        print(f"[Validation] Issues found:")
        for issue in result.issues:
            print(f"  {issue}")
    
    if result.warnings:
        print(f"[Validation] Warnings:")
        for warning in result.warnings:
            print(f"  {warning}")
    
    if not result.is_passing():
        print(f"[Validation] ⚠️  Quality below threshold!")
    
    return result
