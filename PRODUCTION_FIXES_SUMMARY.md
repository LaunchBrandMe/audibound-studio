# Production Issues - Root Cause Analysis & Fixes

## Issue Summary (from diagnostic)
- **SFX**: 5 blocks required, 0 generated (100% failure rate)
- **Music**: Generated 3.7s when 71.3s needed (95% too short)
- **Cost**: ~$1 per failed run

## Root Causes Found

### 1. SFX Complete Failure
**Root Cause**: Provider initialization fails silently
- When `get_sfx_provider("audiogen")` fails, it sets `sfx_provider = None`
- No error logging, silent failure
- All SFX blocks skip generation

**Fixes Applied**:
- [src/worker.py:189-196] Added try/catch with detailed error logging for SFX provider init
- [src/worker.py:198-206] Added try/catch with detailed error logging for music provider init
- [src/core/sfx_engine.py:35-90] Added retry logic (3 attempts, exponential backoff)
- [src/core/sfx_engine.py:53] Increased timeout from 600s to 900s
- [src/core/sfx_engine.py:64-66] Added audio data validation
- [src/worker.py:389-412] Added file existence and size validation
- [src/worker.py:408] Disabled failed SFX blocks to prevent mix failures
- [src/core/assembly.py:163-179] Fixed FFmpeg adelay filter for mono/stereo detection

### 2. Music Duration Catastrophically Wrong
**Root Cause**: Music duration calculated using text estimates BEFORE narration is generated
- Music generation happens in parallel with narration (line 496: `await asyncio.gather(...)`)
- Duration calculation at line 439 uses `estimate_duration_ms(block.narration.text)`
- Text estimation is ~0.4s/word which is very inaccurate
- `block.duration_ms` isn't set until AFTER generation completes (line 327)
- Result: Music calculates based on wrong estimates, generates far too short

**Current Behavior**:
```python
# Line 438-440: Uses estimate, not actual duration
if block.narration:
    est_ms = estimate_duration_ms(block.narration.text)  # WRONG - guess
    required_duration_ms += est_ms
```

**Needed Fix**:
- Generate music AFTER narration completes
- Use actual `block.duration_ms` values (set at line 327)
- OR: Split into two passes - narration first, then music/SFX

### 3. No Visibility into Failures
**Root Cause**: Silent failures, no error messages visible to user
- Provider init failures: No logging
- SFX generation failures: Logged but not surfaced
- Music duration issues: No warning

**Fixes Applied**:
- [src/worker.py:193-196] Added provider init error logging
- [src/worker.py:405,416] Added detailed SFX error logging with type and description
- [src/core/sfx_engine.py:83-88] Added retry attempt logging
- [src/core/music_engine.py:94-99] Added retry attempt logging for music

## Files Modified
1. `src/worker.py` - Provider init error handling, SFX validation
2. `src/core/sfx_engine.py` - Retry logic, timeout increase, validation
3. `src/core/music_engine.py` - Retry logic, timeout increase, validation
4. `src/core/assembly.py` - FFmpeg adelay mono/stereo fix

## Still Need to Fix
- **CRITICAL**: Music duration calculation (using estimates instead of actuals)
  - Option A: Move music generation after narration in separate pass
  - Option B: Use actual narration durations (requires refactoring async flow)

## Testing
Run comprehensive test: `python3 test_sfx_complete.py`
All 4 tests pass ✅

## Next Steps
1. ✅ Provider initialization error handling
2. ✅ SFX retry logic and validation
3. ⚠️  **TODO**: Fix music duration calculation
4. Test full production render
5. Monitor actual costs

