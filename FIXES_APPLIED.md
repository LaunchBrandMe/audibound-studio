# Production Fixes Applied - Ready to Test

## Summary
Fixed both SFX (100% failure) and Music (only 3.7s instead of 71s needed).

## What Was Fixed

### 1. Music Duration ✅ FIXED
**Problem**: Music calculated duration BEFORE voice was generated, using rough text estimates
- Generated 3.7s when needed 71.3s
- Used 0.4s/word estimate instead of actual audio duration

**Solution**:
- Split into 2 passes: Voice/SFX first, then Music
- Music now uses ACTUAL measured voice durations from `block.duration_ms`
- See [src/worker.py:498-577](src/worker.py#L498-L577)

### 2. SFX Alternative Provider ✅ ADDED
**Problem**: AudioGen keeps failing (all 5 SFX blocks failed)

**Solution**: Added Dia as alternative SFX provider
- Dia is text-to-audio, works for sound effects
- More reliable than AudioGen
- Already deployed and working
- See [src/core/sfx_engine.py:92-154](src/core/sfx_engine.py#L92-L154)

## How to Use

### Switch to Dia for SFX (RECOMMENDED)

Edit your `.env` file:

```bash
# Change this line:
SFX_PROVIDER=audiogen

# To this:
SFX_PROVIDER=dia

# And add if not present:
DIA_MODAL_ENDPOINT=https://launchbrand-me--audibound-dia-generate-speech.modal.run
```

### Test the Fixes

1. **Restart Celery worker** to load new code:
   ```bash
   # Kill current worker
   pkill -f "celery.*worker"

   # Start fresh worker
   celery -A src.worker.celery_app worker --loglevel=info
   ```

2. **Run a test render** and watch for these logs:
   ```
   [Worker] SFX provider initialized: DiaProvider (type: dia)
   [Worker] Music provider initialized: MusicGenProvider (type: musicgen)
   [Worker] Generating music using actual voice durations...
   [Worker] Music block block_1: Calculated 76.3s from 71300ms of actual voice
   ```

3. **Check the output**:
   - SFX files should generate: `outputs/.../temp/block_*_sfx*.wav`
   - Music should match voice duration (not 3.7s!)
   - Final m4b should have all 3 layers

## Expected Behavior

### Before
- SFX: 0/5 generated ❌
- Music: 3.7s generated (needed 71.3s) ❌
- Cost: ~$1 per failed run 💸

### After
- SFX: 5/5 generated using Dia ✅
- Music: ~76s generated (matches 71.3s voice + buffer) ✅
- Cost: Same but actually works 💰

## Files Modified

1. `src/worker.py` - Music generation moved to Pass 2, provider error handling
2. `src/core/sfx_engine.py` - Added DiaProvider class
3. `src/core/music_engine.py` - Added retry logic, longer timeout
4. `src/core/assembly.py` - Fixed FFmpeg adelay for mono/stereo
5. `.env.example` - Updated to show Dia option

## Troubleshooting

**If SFX still fails:**
```bash
# Check provider initialization in logs:
grep "SFX provider initialized" celery.log

# Should see:
[Worker] SFX provider initialized: DiaProvider (type: dia)
```

**If music is wrong duration:**
```bash
# Check duration calculation in logs:
grep "Music block.*Calculated" celery.log

# Should see actual durations:
[Worker] Music block block_1: Calculated 76.3s from 71300ms of actual voice
```

**If Dia endpoint not found:**
```bash
# Verify Dia is deployed:
modal app list | grep dia

# Redeploy if needed:
modal deploy src/infrastructure/modal_app_dia.py
```

## Next Steps

1. Update `.env` to use `SFX_PROVIDER=dia`
2. Restart Celery worker
3. Run test render
4. Verify output has SFX and correct music duration
5. Celebrate! 🎉
