# Maestro AI Error Analysis: MiniMax H3 Incomplete Shot Plan

## Error Summary
```
RuntimeError: MiniMax H3 returned an incomplete shot plan after its automatic repair 
(returned 16 shots; expected 24-46; shot 16 is missing spatial_setup, environment, 
visual_style, lighting, mood, action_beats, dialogue_beats, camera_plan, audio_plan, 
ending_beat, closing_blocking, image_source, image_prompt, visual_changes, video_prompt, 
multishot, window_prompts). No video jobs were queued.
```

**Location:** `app/services/director/planners/short_film.py` line 10506  
**Function:** `_plan_story_h3_native()`

---

## Root Cause Analysis

### 1. **Primary Issue: Truncated LLM Output**
The MiniMax H3 LLM is returning an incomplete JSON response where:
- **Symptom:** Only 16 shots returned instead of expected 24-46 shots
- **Problem:** The final shot (shot 16) is missing critical fields entirely
- **Cause:** Token limit exceeded before LLM could complete the full structured output

### 2. **Secondary Issue: Failed Repair Attempt**
Maestro automatically attempts to repair truncated LLM output:
- **First attempt:** Use `_complete_h3_truncated_tail()` to fill missing suffix fields
- **Recovery threshold:** Only fields like `camera_plan`, `audio_plan`, `ending_beat`, etc. can be auto-recovered
- **Failure:** Shot 16 is missing **semantic core fields** that cannot be auto-recovered:
  - `spatial_setup`, `environment`, `visual_style`, `lighting`, `mood`
  - `action_beats`, `dialogue_beats`
  
These fields contain creative content that the repair function cannot generate.

### 3. **Validation Chain Breakdown**
```
LLM Call
    ↓
JSON Parse & Structure Check (_h3_native_structure_issues)
    ├─ Check count: 16 shots vs expected 24-46 ❌ FAIL
    └─ Check required fields on shot 16 ❌ FAIL (16+ missing)
    ↓
Repair Attempt (_complete_h3_truncated_tail)
    ├─ Detects shot 16 missing semantic fields → Cannot recover
    └─ Returns unmodified
    ↓
Post-Repair Validation (_h3_native_structure_issues)
    ├─ Still shows 16 shots ❌ FAIL
    └─ Still missing required fields on shot 16 ❌ FAIL
    ↓
RuntimeError: Repair failed → No video jobs queued
```

---

## Technical Details

### Validation Function (`_h3_native_structure_issues`)
**Lines 2635-2656 in short_film.py**

```python
def _h3_native_structure_issues(
    items: list[dict],
    required: list[str],
    *,
    minimum_items: int,
    maximum_items: int,
) -> list[str]:
    """Detect truncated json_repair output before normalization masks it."""
    issues: list[str] = []
    
    # Check shot count
    if not minimum_items <= len(items or []) <= maximum_items:
        issues.append(
            f"returned {len(items or [])} shots; expected {minimum_items}-{maximum_items}"
        )
    
    # Check each shot's structure
    for index, raw in enumerate(items or [], start=1):
        if not isinstance(raw, dict):
            issues.append(f"shot {index} is not an object")
        missing = [field for field in required if field not in raw]
        if missing:
            issues.append(f"shot {index} is missing {', '.join(missing)}")
    
    return issues
```

### Required Fields Definition
**Lines 10070-10081**

```python
required = [
    "title", "duration_sec", "scene_goal", "narrative_role",
    "scene_type", "continuity_strategy", "continuity_group",
    "subjects_on_screen", "spatial_setup", "environment",
    "visual_style", "lighting", "mood", "action_beats",
    "dialogue_beats", "camera_plan", "audio_plan", "ending_beat",
    "closing_blocking",
    *image_requirements,  # image_source, image_prompt, visual_changes, keyframe_prompts
    "video_prompt", "multishot", "window_prompts",
]
```

### Repair Recovery Logic
**Lines 2832-2909 (`_complete_h3_truncated_tail` function)**

Only these suffix fields can be auto-recovered:
- ✅ `camera_plan`, `audio_plan`, `ending_beat`, `closing_blocking`
- ✅ `image_source`, `image_prompt`, `visual_changes`, `video_prompt`
- ✅ `multishot`, `window_prompts`

Cannot recover (semantic content required):
- ❌ `spatial_setup`, `environment`, `visual_style`, `lighting`, `mood`
- ❌ `action_beats`, `dialogue_beats`
- ❌ `title`, `scene_goal`, `narrative_role`, `scene_type`

---

## Why This Happens

### Common Triggers:

1. **Token Budget Exhaustion**
   - MiniMax H3 is generating very detailed shot descriptions
   - `planner_token_budget` limit is reached mid-output
   - LLM outputs truncates at shot 16 instead of completing 24-46 shots

2. **Context Window Pressure**
   - Long screenplay text in the prompt
   - Detailed character profiles and scene descriptions
   - Complex dialogue manifest requirements
   - Forces LLM to cut corners on shot output

3. **Complex Story Requirements**
   - Stories requiring many shots naturally need more tokens
   - Dialogue-heavy scripts require longer `dialogue_beats` fields
   - Detailed visual direction in system prompt

### Why Shot 16 Gets Truncated:
- LLM stops mid-way through shot 16's JSON object
- JSON parser (`json_repair`) tries to fix it but can only close unclosed brackets
- Shot 16 ends up as empty object `{}` or partially-filled object missing all semantic fields
- Repair function detects this is beyond its recovery capabilities

---

## Solutions

### **Option 1: Reduce Token Demand (Recommended for immediate use)**
Set tighter constraints in the prompt to force more concise shot plans:
- Use fewer action beats per shot (currently unlimited)
- Limit dialogue beat descriptions length  
- Reduce visual style prose verbosity
- Request "concise one-line" descriptions instead of detailed prose

### **Option 2: Increase Token Budget**
Modify `planner_token_budget` in the config to give H3 more room:
- Currently: ~8000-12000 tokens (estimated)
- Increase to: 16000-20000 tokens (if context window allows)
- **Risk:** Slower generation, higher API costs

### **Option 3: Lower Shot Count Expectations**
Reduce `shot_count_low` and `shot_count_high` ranges:
- Current expected range often requires 24-46 shots
- Reduce to 12-30 shots for shorter videos
- **Trade-off:** Less granular shot breakdowns

### **Option 4: Improve Prompt Clarity**
Rewrite the Pass 2 system prompt to:
- Add explicit instruction: "Never output partial/incomplete shots"
- Include hard cutoff guidance: "If you run out of tokens, end with the last complete shot"
- Add example of properly-terminated JSON array
- Reduce system prompt length to make room for output

### **Option 5: Implement Fallback Recovery**
Extend `_complete_h3_truncated_tail()` to:
- Auto-generate missing semantic fields for truncated shots
- Use the previous shot or template defaults
- Assign `spatial_setup` and `environment` from scene context
- Fill `mood` and `visual_style` from established tone

---

## Recommended Actions

### **Immediate Fix** (Quick Implementation)
Edit `app/launch.py` or config to reduce shot count range or token budget targeting smaller plans.

### **Medium-term Fix** (Prompt Engineering)
Review and optimize the Pass 2 system prompt (`minimax_h3_shot_breakdown.md`) to be more concise while maintaining quality.

### **Long-term Fix** (Architecture Change)
Implement multi-pass shot planning:
- Pass 2a: Generate detailed shots for act 1 only
- Pass 2b: Regenerate acts 2-3 separately
- Merge results, avoiding single LLM call with high token risk

---

## Related Code Locations

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Validation | `short_film.py` | 2635-2656 | Detect incomplete output |
| Recovery | `short_film.py` | 2832-2909 | Attempt auto-repair |
| Main Error | `short_film.py` | 10505-10511 | Raise RuntimeError |
| Config | `short_film.py` | 10070-10081 | Define required fields |
| Error Messages | `short_film.py` | Lines 6750, 7781, 10510, 10516, 10529 | All "No video jobs" errors |

---

## Next Steps

Would you like me to:
1. ✅ Implement one of the solution options above?
2. ✅ Modify the prompt to be more concise?
3. ✅ Reduce the token budget or shot count parameters?
4. ✅ Add enhanced error recovery logic?
