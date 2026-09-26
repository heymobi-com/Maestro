"""
Viral Video Planner — creates a ProductionPlan for short-form viral content.

Optimizes for early hooks, rapid pacing, and platform-specific patterns
(TikTok, Reels, Shorts). Emphasizes the first 1-2 seconds.

Outputs: ProductionPlan with ShotPlan objects.
"""

from __future__ import annotations
from typing import Optional, Any

from ..schema import (
    ProductionPlan, ShotPlan, CharacterProfile, ReferenceAssets,
    AssetRef, SubjectRef, DialogueBeat, CameraPlan, AudioPlan,
)
from ..policies import build_character_rules_block, build_camera_style_block
from .base import BasePlanner


# Platform defaults
_PLATFORM_DURATIONS = {
    "tiktok": 30,
    "reels": 30,
    "shorts": 60,
    "general": 30,
}

_VIRAL_STYLES = {
    "meme": "bold text overlays, exaggerated reactions, jump cuts between setups",
    "ugc": "casual handheld, natural lighting, authentic feel, direct address",
    "cinematic": "polished visuals, dramatic lighting, fast-paced storytelling",
    "direct_address": "tight framing on speaker, eye contact with camera, punchy delivery",
    "reaction": "split screen energy, exaggerated expressions, before/after reveals",
    "tutorial": "clear demonstrations, close-ups on process, minimal distractions",
}


class ViralVideoPlanner(BasePlanner):
    skill_type = "viral_video"

    def plan(
        self,
        concept: str,
        audio_path: Optional[str] = None,
        reference_image_path: Optional[str] = None,
        characters: Optional[list[dict]] = None,
        target_duration: int = 30,
        platform: str = "general",
        style: str = "cinematic",
        clips: Optional[list[dict]] = None,
        **kwargs,
    ) -> ProductionPlan:
        """Create a ProductionPlan for a viral video.

        Args:
            concept: Video concept/idea.
            audio_path: Optional audio track.
            reference_image_path: Optional reference photo.
            characters: Character descriptions.
            target_duration: Target duration in seconds.
            platform: Target platform (tiktok, reels, shorts, general).
            style: Visual style (meme, ugc, cinematic, direct_address, reaction, tutorial).
        """
        has_reference = bool(reference_image_path)

        # Clamp duration to platform norms
        max_dur = _PLATFORM_DURATIONS.get(platform, 30)
        target_duration = min(target_duration, max_dur * 2)  # allow some flex

        char_profiles = []
        if characters:
            char_profiles = [
                CharacterProfile(
                    id=f"char_{i}",
                    display_name=c.get("name", ""),
                    physical_description=c.get("description", "person"),
                )
                for i, c in enumerate(characters)
            ]

        ref_assets = ReferenceAssets(
            start_image=AssetRef(id="ref_image", type="image", uri=reference_image_path) if has_reference else None,
            audio=AssetRef(id="audio", type="audio", uri=audio_path) if audio_path else None,
        )

        # The audio timeline decides how many shots there are. This used to come from
        # the duration alone -- a 30-second default asked for six shots -- and the rest
        # of a longer timeline was then filled with generic plans. Measured on a real
        # 16-clip project: clips 1-6 were planned and 7-16 arrived with no subjects, no
        # audio plan and no duration, which is what "from the seventh on it only walks
        # down the street" looked like, with every model, because the instruction was
        # the same for all of them.
        timeline = [clip for clip in (clips or []) if isinstance(clip, dict)]
        if timeline:
            target_scenes = len(timeline)
            timeline_seconds = max(
                (float(clip.get("end", 0) or 0) for clip in timeline), default=0.0,
            )
            if timeline_seconds > 0:
                target_duration = timeline_seconds
        else:
            target_scenes = max(3, min(12, target_duration // 5))

        style_desc = _VIRAL_STYLES.get(style, _VIRAL_STYLES["cinematic"])

        # An uploaded soundtrack drives the timing and is not something the characters
        # perform. The plan used to say generated_audio for every shot, which asks the
        # model to invent its own audio instead of following the track the user supplied:
        # that is what made characters sing or lip-sync to the music they were supposed
        # to be working over. The short-film planner already says audio_driven.
        has_soundtrack = bool(audio_path)
        soundtrack_note = (
            "The uploaded soundtrack drives the timing and it is finished audio: the "
            "characters do not perform it. Do not write singing, do not move mouths to "
            "the music, and keep lip movement to a tagged spoken line only. Its "
            "transcription is context for the story beats, not words to re-enact."
            if has_soundtrack else ""
        )

        char_rules = build_character_rules_block(has_reference, char_profiles if char_profiles else None)
        camera_block = build_camera_style_block()

        def scene_prompts(count: int, seconds: float, note: str = "") -> tuple[str, str]:
            system = f"""You are a viral video director creating content optimized for {platform}.

{f"You are given a REFERENCE PHOTO." if has_reference else ""}

Style: {style} — {style_desc}

VIRAL VIDEO RULES:
- The FIRST SHOT is critical — it must hook viewers in 1-2 seconds.
- Front-load the most interesting visual, action, or statement.
- Keep shots SHORT (3-8 seconds). Rapid pacing holds attention.
- Each shot should feel like it earns its screen time.
- End with a payoff, reveal, or callback to the hook.
- For direct-address: tight framing, eye contact, punchy delivery.
- For meme: exaggerated reactions, setup/punchline structure.
- For UGC: authentic feel, handheld energy, relatable moments.
{soundtrack_note}
{note}
{char_rules}

{camera_block}

OUTPUT FORMAT — respond with ONLY a JSON array:
[
  {{
    "scene_goal": "Hook — grab attention immediately",
    "scene_type": "hook|setup|escalation|payoff|callback",
    "duration_sec": 3,
    "subjects_on_screen": [{{"visual_description": "person in bright outfit"}}],
    "spatial_setup": "tight center frame",
    "environment": "urban street",
    "visual_style": "{style}",
    "lighting": "natural daylight",
    "mood": "energetic",
    "action_beats": ["person turns to camera with surprised expression"],
    "dialogue_beats": [{{"spoken_text": "Wait, what?", "delivery": "shocked"}}],
    "camera_plan": {{"framing": "close-up", "movement": "snap zoom", "movement_intensity": "dynamic"}},
    "audio_plan": {{"mode": "generated_audio"}},
    "ending_beat": "freeze frame on reaction"
  }}
]

Output exactly {count} shot plans totaling ~{seconds:.0f}s. Go:"""
            user = f"""Concept: {concept}
Platform: {platform}
Target duration: {seconds:.0f}s
Style: {style}

Create {count} short, punchy shots. Hook first! Go:"""
            return system, user

        def plan_scenes(count: int, seconds: float, note: str = "") -> list[dict]:
            system_prompt, user_prompt = scene_prompts(count, seconds, note)
            if kwargs.get("polish_block"):
                system_prompt = f"{system_prompt}\n\n{kwargs['polish_block']}"
            return self._call_llm_json(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=max(1024, count * 300 + 512),
                image_paths=[reference_image_path] if has_reference and reference_image_path else None,
                bounded=bool(note),
            )

        batch_size = self.long_form_batch_size()
        if len(timeline) > batch_size:
            # The whole timeline, so a batch can see what is still ahead instead of
            # continuing whatever the previous one ended with.
            timeline_overview = "\n".join(
                f"Clip {index + 1}: {float(clip.get('start', 0) or 0):.1f}-"
                f"{float(clip.get('end', 0) or 0):.1f}s"
                for index, clip in enumerate(timeline)
            )

            def call_batch(
                batch_number: int,
                start: int,
                batch_clips: list[dict],
                previous: Optional[dict],
            ) -> list[dict]:
                end = start + len(batch_clips)
                previous_ending = str((previous or {}).get("ending_beat") or "").strip()
                batch_seconds = max(
                    (float(clip.get("end", 0) or 0) for clip in batch_clips), default=0.0,
                ) - min((float(clip.get("start", 0) or 0) for clip in batch_clips), default=0.0)
                if batch_seconds <= 0:
                    # A saved timeline can arrive with its timings unwritten; the shot
                    # count still comes from the clips, which is what matters here.
                    batch_seconds = len(batch_clips) * 5.0
                note = (
                    "LONG-FORM TIMELINE CONTRACT:\n"
                    f"This is planning batch {batch_number}, covering global shots "
                    f"{start + 1}-{end} of {len(timeline)}. Continue the same video; do "
                    "not restart its premise or repeat completed shot ideas. The "
                    "soundtrack is already fixed and its transcription is immutable.\n"
                    f"Previous planned ending: "
                    f"{previous_ending or 'No prior shot; establish the opening.'}\n\n"
                    "THE WHOLE TIMELINE (context only: see where this batch sits and "
                    f"what is still ahead; plan ONLY shots {start + 1}-{end}):\n"
                    f"{timeline_overview}"
                )
                return plan_scenes(len(batch_clips), batch_seconds, note)

            shot_dicts = self._run_checkpointed_json_batches(
                items=timeline,
                batch_size=batch_size,
                checkpoint_key="viral_video_batches",
                stage="viral_video_batch",
                progress_label="viral",
                call_batch=call_batch,
                fallback_factory=lambda index, clip: {
                    "scene_goal": f"Continue the video at global shot {index + 1}",
                    "scene_type": "escalation",
                    "duration_sec": max(
                        1.0,
                        float(clip.get("end", 0) or 0) - float(clip.get("start", 0) or 0),
                    ),
                    "subjects_on_screen": [],
                    "environment": "",
                    "visual_style": "",
                    "lighting": "",
                    "mood": "",
                    "action_beats": [],
                    "dialogue_beats": [],
                    "camera_plan": {},
                    "audio_plan": {"mode": "generated_audio"},
                    "ending_beat": "",
                },
            )
        else:
            shot_dicts = plan_scenes(target_scenes, target_duration)

        shots = []
        for i, raw in enumerate(shot_dicts):
            duration = raw.get("duration_sec", raw.get("duration", 5))

            subjects = [SubjectRef.from_dict(s) if isinstance(s, dict) else SubjectRef(visual_description=str(s))
                        for s in raw.get("subjects_on_screen", [])]

            cam_raw = raw.get("camera_plan", {})
            camera = CameraPlan(
                framing=cam_raw.get("framing", "medium shot"),
                angle=cam_raw.get("angle"),
                movement=cam_raw.get("movement"),
                movement_intensity=cam_raw.get("movement_intensity", "dynamic"),
            )

            audio_raw = raw.get("audio_plan", {})
            # A supplied track wins over the mode the model echoed from the schema example:
            # the example says generated_audio, so a model copying it asked the renderer to
            # invent its own audio while the user's track sat unused. ambient_only is kept
            # because it is the deliberate "no speech, no lip movement" answer, and the two
            # soundtrack-driven modes are already what this has to be.
            requested = str(audio_raw.get("mode") or "").strip()
            if has_soundtrack and requested not in ("ambient_only", "audio_driven", "music_driven", "dialogue_driven"):
                requested = "audio_driven"
            audio = AudioPlan(
                mode=requested or ("audio_driven" if has_soundtrack else "generated_audio"),
                ambience=audio_raw.get("ambience"),
                # A supplied track is the clock; the video follows it.
                timing_anchor="audio" if has_soundtrack else "video",
            )

            dialogue_beats = [DialogueBeat.from_dict(db) for db in raw.get("dialogue_beats", [])] if raw.get("dialogue_beats") else None

            shot = ShotPlan(
                shot_id=self._make_shot_id(i, "vv"),
                index=i,
                duration_sec=duration,
                skill_type="viral_video",
                scene_goal=raw.get("scene_goal", f"Shot {i + 1}"),
                scene_type=raw.get("scene_type", "escalation"),
                source_mode_preference="i2v" if has_reference else "t2v",
                image_strategy="reference_edit" if has_reference else "fresh_generation",
                continuity_strategy="independent",
                subjects_on_screen=subjects,
                spatial_setup=raw.get("spatial_setup", ""),
                environment=raw.get("environment", ""),
                visual_style=raw.get("visual_style", style),
                lighting=raw.get("lighting", ""),
                mood=raw.get("mood", "energetic"),
                action_beats=raw.get("action_beats", []),
                dialogue_beats=dialogue_beats,
                camera_plan=camera,
                audio_plan=audio,
                ending_beat=raw.get("ending_beat", ""),
                metadata={
                    "platform": platform,
                    "style": style,
                    "is_hook": i == 0,
                },
            )
            shots.append(shot)

        total_dur = sum(s.duration_sec for s in shots)

        return ProductionPlan(
            skill_type="viral_video",
            title=None,
            global_style=f"{style} — {concept}",
            total_duration_sec=total_dur,
            reference_assets=ref_assets,
            characters=char_profiles if char_profiles else None,
            shots=shots,
            continuity_notes=[
                f"Viral video for {platform} — hook in first 1-2 seconds",
                "Short shots, rapid pacing, clear payoff",
                f"Style: {style}",
            ],
        )
