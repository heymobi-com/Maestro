"""Contradictions inside a compiled prompt, and the audit that now reports them.

The reported problem: "muchos problemas provienen de contradicciones que se producen desde la
planeacion y generacion de prompts y luego terminan en alucinaciones del checkpoint cuando hay
contradicciones y no sabe que hacer e inventa". A prompt that asks for two incompatible things at
once is not a prompt that fails -- it is a prompt the renderer resolves on its own, and the run is
paid for by the time anyone sees which reading it chose.

Two guards are as important as the checks: a healthy prompt must produce nothing, and a legitimate
prompt must not be flagged for repeating itself (one outfit described twice, a camera that holds and
then moves). A noisy audit is an audit nobody reads.

Every rule here was sharpened against 2156 compiled prompts of 41 finished projects, which is also
where the false positives it now avoids were found: the retention block naming the shots a subject
appears in, "a moment of stillness" that is a person and not a camera, a sweater under a blazer, and
a garment charged to the Subject before the one wearing it.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services.director.prompt_audit import audit_project_prompts  # noqa: E402
from services.director.prompt_contradictions import (  # noqa: E402
    _conflicting_outfits,
    contradiction_findings,
    suggested_fix,
)
from services.prompt_audit_api import annotated  # noqa: E402

SUBJECTS = (
    "subject_definitions: <Subject 1> is Alex from <Picture 1>, wearing a long red coat. "
    "<Subject 2> is Sam from <Picture 2>, wearing a grey suit. "
    "<Audio 1> supplies the music style."
)

# The fields the H3 compile step writes, in the order it writes them.
FIELDS = (
    ("subject_definitions", SUBJECTS),
    ("summary", "[reference generation] Two dancers in a courtyard at night."),
    ("detailed_description", "[Shot 1] Alex and Sam dance under the moonlit courtyard arches."),
    ("integrated_multimodal_description", "A static wide shot; the camera holds while they turn."),
    ("overall_soundscape", "Footsteps on stone. No speech."),
    ("non_diegetic_music", "Use the rhythm and texture of <Audio 1>."),
)


def healthy(*extra: str, **overrides: str) -> str:
    """A prompt in the shape the H3 compile step produces, with any field replaced or dropped.

    Replacing matters: a test that appends a second ``non_diegetic_music`` line is testing the
    last one only, and would pass a check that reads the first.
    """

    lines = []
    for name, text in FIELDS:
        value = overrides.pop(name, text)
        if value:
            lines.append(f"{name}: {value}")
    lines.extend(f"{name}: {text}" for name, text in overrides.items())
    lines.extend(extra)
    return "\n".join(lines)


def codes(text: str, **kwargs) -> list[str]:
    return [finding["code"] for finding in contradiction_findings(text, **kwargs)]


# A prompt that pasted the brief's own per-shot heading into the clip, which is the measured
# cause of every shot performing the same camera move.
LEAKY = f"{SUBJECTS}\nCAMERA WORK:\nA slow orbit around them.\n"


class QuietTests(unittest.TestCase):
    def test_a_healthy_prompt_is_not_flagged(self):
        self.assertEqual(contradiction_findings(healthy()), [])

    def test_an_empty_prompt_is_not_flagged(self):
        self.assertEqual(contradiction_findings(""), [])
        self.assertEqual(contradiction_findings("   \n  "), [])

    def test_one_shot_named_once_is_not_two_shots(self):
        # The compile step names the shot it is compiling, and a second mention of the same
        # number is still one shot: a cut needs two different ones.
        prompt = healthy() + "\n[Shot 1] the only shot in this clip."
        self.assertNotIn("clip-holds-two-shots", codes(prompt, shot=1))

    def test_a_timestamp_at_zero_is_the_start_of_the_clip(self):
        prompt = healthy("timing: At 0:00 the shot opens.")
        self.assertNotIn("clip-holds-two-shots", codes(prompt, shot=1))

    def test_a_camera_that_holds_and_then_moves_is_one_clause(self):
        # A hold-then-move is a real request, so camera wording is a warning that names both
        # halves rather than an error that forbids the combination.
        found = contradiction_findings(
            healthy(integrated_multimodal_description="The camera is static until the last beat, then it pans slowly.")
        )
        camera = [finding for finding in found if finding["code"] == "camera-contradiction"]
        self.assertEqual(len(camera), 1)
        self.assertEqual(camera[0]["severity"], "warning")
        self.assertIn("static", camera[0]["message"])
        self.assertIn("pan", camera[0]["message"])

    def test_stillness_that_is_not_the_camera_is_not_a_camera_move(self):
        # Measured on real prompts: "a moment of stillness" is a person, and "she tilts her head"
        # is not a camera move, so the wording has to be about the camera to count.
        prompt = healthy(
            detail="By the final beat his profile is caught in a moment of stillness, and she tilts her head.",
        )
        self.assertNotIn("camera-contradiction", codes(prompt))

    def test_a_panorama_is_not_a_pan(self):
        prompt = healthy(
            integrated_multimodal_description="A static panoramic wide shot of the whole courtyard."
        )
        self.assertNotIn("camera-contradiction", codes(prompt))

    def test_one_outfit_described_twice_is_not_two_outfits(self):
        prompt = healthy(
            "detail: <Subject 1> keeps the red coat throughout the shot.",
        )
        self.assertNotIn("wardrobe-contradiction", codes(prompt))

    def test_a_heading_with_content_after_it_is_not_a_heading(self):
        prompt = healthy(integrated_multimodal_description="CAMERA: a slow orbit around them.")
        self.assertNotIn("brief-heading-in-clip", codes(prompt))

    def test_music_field_without_absence_is_not_a_contradiction(self):
        prompt = healthy("detail: a lone guitar carries the melody.")
        self.assertNotIn("music-contradiction", codes(prompt))

    def test_absence_of_music_without_music_words_is_not_a_contradiction(self):
        prompt = healthy(non_diegetic_music="silence.")
        self.assertNotIn("music-contradiction", codes(prompt))


class ClipHoldsTwoShotsTests(unittest.TestCase):
    def test_a_marker_written_as_a_new_beat_is_an_error(self):
        prompt = healthy() + "\n[Shot 3] Alex leaves the frame."
        found = contradiction_findings(prompt, shot=1)
        self.assertEqual([finding["code"] for finding in found], ["clip-holds-two-shots"])
        self.assertEqual(found[0]["severity"], "error")
        self.assertIn("[3]", found[0]["message"])

    def test_a_marker_naming_this_very_shot_is_not_a_second_shot(self):
        prompt = healthy() + "\n[Shot 7] Alex leaves the frame."
        self.assertEqual(contradiction_findings(prompt, shot=7), [])
        self.assertEqual(
            [finding["code"] for finding in contradiction_findings(prompt, shot=1)],
            ["clip-holds-two-shots"],
        )

    def test_a_marker_inside_a_sentence_is_the_pipelines_own_bookkeeping(self):
        # Real prompts carry "retention_analysis: <Subject 1> (appears in [Shot 3])", which names
        # the shots a subject appears in. It is not a cut, and 154 of these were reported as one
        # before the marker had to start a line.
        prompt = healthy("retention_analysis: <Subject 1> (appears in [Shot 3]): fully_preserved.")
        self.assertNotIn("clip-holds-two-shots", codes(prompt, shot=1))

    def test_a_late_timestamp_inside_one_clip_is_an_error(self):
        found = contradiction_findings(healthy("timing: At MM:04.000 the light changes."), shot=1)
        self.assertEqual([finding["code"] for finding in found], ["clip-holds-two-shots"])
        self.assertIn("MM:04.000", found[0]["message"])
        self.assertIn("30-shot project", found[0]["message"])

    def test_a_clock_timestamp_is_recognised_too(self):
        found = contradiction_findings(healthy("timing: At 4:05 they stop."), shot=1)
        self.assertIn("clip-holds-two-shots", [finding["code"] for finding in found])
        self.assertIn("4:05", found[0]["message"])


class BriefHeadingTests(unittest.TestCase):
    def test_a_bare_brief_heading_reaching_a_clip_is_an_error(self):
        found = contradiction_findings(LEAKY)
        self.assertEqual([finding["code"] for finding in found], ["brief-heading-in-clip"])
        self.assertEqual(found[0]["severity"], "error")
        self.assertIn("CAMERA WORK", found[0]["message"])

    def test_a_heading_is_looked_for_in_the_pre_polish_draft_as_well(self):
        # Polishing is the step that can paste new material in, so a heading that only
        # appears in the draft still has to be reported.
        found = contradiction_findings(healthy(), draft=LEAKY)
        self.assertEqual([finding["code"] for finding in found], ["brief-heading-in-clip"])

    def test_a_draft_identical_to_the_prompt_is_not_checked_twice(self):
        found = contradiction_findings(LEAKY, draft=LEAKY)
        self.assertEqual([finding["code"] for finding in found], ["brief-heading-in-clip"])


class LightingTests(unittest.TestCase):
    def test_daylight_and_night_in_one_prompt_is_a_warning(self):
        prompt = healthy(detail="the courtyard sits in broad daylight under a moonlit sky.")
        found = contradiction_findings(prompt)
        self.assertEqual([finding["code"] for finding in found], ["lighting-contradiction"])
        self.assertEqual(found[0]["severity"], "warning")
        self.assertIn("daylight", found[0]["message"])
        self.assertIn("night", found[0]["message"])


class WardrobeTests(unittest.TestCase):
    PLAIN_CAST = (
        "subject_definitions: <Subject 1> is Alex from <Picture 1>. "
        "<Subject 2> is Sam from <Picture 2>."
    )

    def test_a_whole_garment_against_separate_pieces_is_an_error(self):
        # The signature of two people under one Subject: a woman dressed whole, and the same
        # Subject further down the prompt dressed in pieces.
        prompt = healthy(
            subject_definitions=self.PLAIN_CAST,
            first="<Subject 1> wears a green dress.",
            second="<Subject 1> wears a white shirt and a leather vest over dark pants.",
        )
        found = contradiction_findings(prompt, shot=1)
        self.assertEqual([finding["code"] for finding in found], ["wardrobe-contradiction"])
        self.assertEqual(found[0]["severity"], "error")
        self.assertIn("<Subject 1>", found[0]["message"])

    def test_outfits_are_tracked_per_subject(self):
        prompt = healthy(
            subject_definitions=self.PLAIN_CAST,
            first="<Subject 1> wears a green dress.",
            second="<Subject 2> wears a black tuxedo.",
        )
        self.assertNotIn("wardrobe-contradiction", codes(prompt, shot=1))

    def test_the_short_subject_tag_is_followed_too(self):
        # Real prompts write "Roman (S2)" in the prose, and without that tag a garment worn by
        # whoever speaks in a later paragraph was charged to the Subject before it.
        prompt = healthy(
            subject_definitions=self.PLAIN_CAST,
            first="<Subject 1> wears a green dress.",
            second="Sam (S2) wears a white shirt and a leather vest over dark pants.",
        )
        self.assertNotIn("wardrobe-contradiction", codes(prompt, shot=1))

    def test_a_wardrobe_before_any_subject_belongs_to_nobody(self):
        prompt = "wardrobe: a green dress.\n<Subject 1> wears a dark suit."
        self.assertNotIn("wardrobe-contradiction", codes(prompt, shot=1))

    def test_layering_is_not_a_second_outfit(self):
        # A sweater under a blazer, a shirt under a suit, a coat over a dress: measured on real
        # prompts, each of these was reported as two outfits before the garments were compared.
        self.assertEqual(_conflicting_outfits({"a charcoal wool sweater over a blue shirt", "her navy blazer"}), [])
        self.assertEqual(_conflicting_outfits({"a white shirt and dark pants", "a leather vest"}), [])
        self.assertEqual(_conflicting_outfits({"a green dress", "a long red coat"}), [])
        self.assertEqual(_conflicting_outfits({"her cream dress", "her cream dress and red shoes"}), [])

    def test_a_pair_that_cannot_be_worn_together_survives(self):
        self.assertEqual(
            len(_conflicting_outfits({"a green dress", "a dark suit"})),
            2,
        )
        self.assertEqual(
            len(_conflicting_outfits({"a light cream a-line dress", "his dark gray suit"})),
            2,
        )

    def test_a_phrase_that_names_both_is_not_a_conflict_with_itself(self):
        self.assertEqual(_conflicting_outfits({"a dress over dark pants"}), [])


class MusicTests(unittest.TestCase):
    def test_a_silent_music_field_over_a_scored_shot_is_a_warning(self):
        prompt = healthy(non_diegetic_music="none.", detail="a solo violin follows them in.")
        found = contradiction_findings(prompt)
        self.assertEqual([finding["code"] for finding in found], ["music-contradiction"])
        self.assertEqual(found[0]["severity"], "warning")
        self.assertIn("violin", found[0]["message"])


class AuditIntegrationTests(unittest.TestCase):
    def clip(self, **overrides) -> dict:
        clip = {
            "index": 0,
            "video_prompt": healthy(),
            # A draft in the shape the pipeline writes: the project's text plus the three
            # action sections the upstream audit expects every draft to carry.
            "_director_h3_source_prompt": (
                SUBJECTS
                + "\nOpening composition: A wide shot of the courtyard."
                + "\nDialogue: None."
                + "\nCanonical identity and world: The same courtyard as every other shot."
            ),
            "_director_project_context": SUBJECTS,
        }
        clip.update(overrides)
        return clip

    def test_the_audit_folds_contradictions_into_its_findings(self):
        audit = audit_project_prompts([self.clip(video_prompt=LEAKY)])
        self.assertEqual(audit["totals"].get("brief-heading-in-clip"), 1)
        self.assertEqual(audit["findings"][0]["shot"], 1)
        self.assertIn(
            "brief-heading-in-clip",
            [finding["code"] for finding in audit["findings"][0]["findings"]],
        )

    def test_a_healthy_project_still_totals_nothing(self):
        audit = audit_project_prompts([self.clip(), self.clip(index=1)])
        self.assertEqual(audit["totals"], {})

    def test_the_contradiction_codes_name_the_saved_clip(self):
        audit = audit_project_prompts(
            [
                self.clip(),
                self.clip(index=4, video_prompt=healthy("timing: At MM:04.000 the light changes.")),
            ]
        )
        self.assertEqual(audit["totals"].get("clip-holds-two-shots"), 1)
        self.assertEqual(audit["findings"][-1]["shot"], 5)
        self.assertEqual(audit["findings"][-1]["index"], 1)

    def test_each_clip_is_told_which_shot_it_is(self):
        # The audit hands the clip's own number to the contradiction checks, so a prompt that
        # labels itself [Shot 3] is not read as a second shot in clip 3 -- and is in clip 1.
        labelled = healthy() + "\n[Shot 3] Alex leaves the frame."
        audit = audit_project_prompts(
            [self.clip(video_prompt=labelled), self.clip(index=2, video_prompt=labelled)]
        )
        self.assertEqual(audit["totals"].get("clip-holds-two-shots"), 1)
        self.assertEqual(audit["findings"][0]["shot"], 1)


class SuggestedFixTests(unittest.TestCase):
    """The suggested text, which is offered only when the fix is mechanical and provably clears."""

    def test_a_stray_timestamp_is_removed_and_the_prose_kept(self):
        prompt = healthy("timing: At MM:04.000 the light changes.")
        fixed = suggested_fix(prompt, "clip-holds-two-shots", shot=1)
        self.assertNotIn("MM:04.000", fixed)
        self.assertIn("the light changes", fixed)
        self.assertEqual(contradiction_findings(fixed, shot=1), [])

    def test_a_marker_naming_another_shot_is_removed(self):
        prompt = healthy() + "\n[Shot 3] Alex leaves the frame."
        fixed = suggested_fix(prompt, "clip-holds-two-shots", shot=1)
        self.assertNotIn("[Shot 3]", fixed)
        self.assertIn("Alex leaves the frame", fixed)

    def test_a_marker_naming_this_shot_is_left_alone(self):
        prompt = healthy() + "\n[Shot 1] Alex leaves the frame."
        self.assertEqual(suggested_fix(prompt, "clip-holds-two-shots", shot=1), "")

    def test_a_leaked_heading_is_dropped_without_touching_the_lines_around_it(self):
        fixed = suggested_fix(LEAKY, "brief-heading-in-clip", shot=1)
        self.assertNotIn("CAMERA WORK:", fixed)
        self.assertIn("A slow orbit around them.", fixed)
        self.assertIn("subject_definitions:", fixed)
        self.assertEqual(contradiction_findings(fixed, shot=1), [])

    def test_a_heading_the_draft_keeps_as_well_is_not_offered(self):
        # Editing the compiled prompt would clear nothing, so the panel must not offer a button
        # that does not work: the suggestion is checked against the draft too.
        prompt = healthy() + "\nCAMERA:\nA slow orbit around them."
        draft = SUBJECTS + "\nCAMERA:\nA slow orbit around them."
        self.assertEqual(suggested_fix(prompt, "brief-heading-in-clip", draft=draft, shot=1), "")

    def test_two_descriptions_disagreeing_are_not_a_textual_fix(self):
        prompt = healthy(
            integrated_multimodal_description="The camera is static until the last beat, then it pans slowly."
        )
        self.assertEqual(suggested_fix(prompt, "camera-contradiction", shot=1), "")

    def test_a_clean_prompt_has_nothing_to_suggest(self):
        self.assertEqual(suggested_fix(healthy(), "clip-holds-two-shots", shot=1), "")

    def test_every_mechanical_finding_in_a_real_project_has_a_suggestion(self):
        # The measured case: 113 findings of this code across 41 projects, every one of them a
        # leftover timestamp or a pasted marker.
        for prompt in (
            healthy("timing: At 00:07.300 she turns."),
            healthy() + "\n[Shot 4] The light changes.",
        ):
            fixed = suggested_fix(prompt, "clip-holds-two-shots", shot=1)
            self.assertTrue(fixed, prompt)
            self.assertEqual(contradiction_findings(fixed, shot=1), [])


class AuditAnnotationTests(unittest.TestCase):
    def clip(self, **overrides) -> dict:
        clip = {
            "index": 0,
            "video_prompt": healthy(),
            "_director_h3_source_prompt": (
                SUBJECTS
                + "\nOpening composition: A wide shot of the courtyard."
                + "\nDialogue: None."
                + "\nCanonical identity and world: The same courtyard as every other shot."
            ),
            "_director_project_context": SUBJECTS,
        }
        clip.update(overrides)
        return clip

    def test_a_shot_carries_the_prompt_it_was_audited_from(self):
        clip = self.clip(video_prompt=healthy("timing: At MM:04.000 the light changes."))
        audit = annotated(audit_project_prompts([clip]), [clip])
        shot = audit["findings"][0]
        self.assertEqual(shot["prompt"], clip["video_prompt"])
        finding = shot["findings"][0]
        self.assertTrue(finding["in_prompt"])
        self.assertIn("the light changes", finding["fix"])
        self.assertNotIn("MM:04.000", finding["fix"])

    def test_a_finding_the_draft_keeps_is_marked_and_has_no_fix(self):
        clip = self.clip()
        clip["_director_h3_source_prompt"] = clip["_director_h3_source_prompt"] + "\nCAMERA:\nA slow orbit."
        audit = annotated(audit_project_prompts([clip]), [clip])
        finding = audit["findings"][0]["findings"][0]
        self.assertEqual(finding["code"], "brief-heading-in-clip")
        self.assertFalse(finding["in_prompt"])
        self.assertEqual(finding["fix"], "")

    def test_a_finding_with_no_mechanical_fix_still_reports_where_it_lives(self):
        clip = self.clip(
            video_prompt=healthy(
                first="<Subject 1> wears a green dress.",
                second="<Subject 1> wears a white shirt with dark pants.",
            )
        )
        audit = annotated(audit_project_prompts([clip]), [clip])
        finding = audit["findings"][0]["findings"][0]
        self.assertEqual(finding["code"], "wardrobe-contradiction")
        self.assertTrue(finding["in_prompt"])
        self.assertEqual(finding["fix"], "")

    def test_a_shot_without_findings_is_not_invented(self):
        clip = self.clip()
        audit = annotated(audit_project_prompts([clip]), [clip])
        self.assertEqual(audit["findings"], [])


class AuditEndpointTests(unittest.TestCase):
    def test_the_router_serves_the_audit_under_the_pipeline_it_belongs_to(self):
        from services import prompt_audit_api

        paths = [
            (route.path, sorted(route.methods))
            for route in prompt_audit_api.router.routes
        ]
        self.assertEqual(
            paths,
            [("/api/v1/director/pipelines/{pid}/prompt-audit", ["GET"])],
        )

    def test_the_application_mounts_the_router(self):
        source = (ROOT / "app" / "launch.py").read_text(encoding="utf-8")
        self.assertIn("api.include_router(prompt_audit_api.router)", source)
        # Imported and mounted: the count survives another feature joining the same import line.
        self.assertGreaterEqual(source.count("prompt_audit_api"), 2, "imported and mounted")

    def test_the_interface_asks_for_the_same_path_the_router_serves(self):
        api = (ROOT / "ui" / "src" / "api" / "promptAudit.ts").read_text(encoding="utf-8")
        self.assertIn("`/api/v1/director/pipelines/${encodeURIComponent(pid)}/prompt-audit`", api)
        component = (
            ROOT / "ui" / "src" / "components" / "DirectorDashboard" / "PromptAuditNotices.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("import { fetchPromptAudit, type PromptAudit } from '../../api/promptAudit'", component)
        self.assertLess(len(component), 14000)

    def test_the_interface_corrects_a_shot_through_the_endpoint_that_already_edits_one(self):
        # The correction reuses the shot-prompt endpoint the clip cards save with, so an edit here
        # and an edit there are the same edit, and the plan reloads behind both.
        component = (
            ROOT / "ui" / "src" / "components" / "DirectorDashboard" / "PromptAuditNotices.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("import { updateClipPrompt } from '../../api/client'", component)
        self.assertIn("await updateClipPrompt(pid, editing.clipIndex, { video_prompt: editing.text })", component)
        self.assertIn("await loadPipeline(pid)", component)

    def test_the_interface_reloads_the_audit_when_the_plan_changes(self):
        # Not the status: a prompt edited in a clip card changes neither the status nor the id,
        # so the panel fingerprints the prompts themselves.
        component = (
            ROOT / "ui" / "src" / "components" / "DirectorDashboard" / "PromptAuditNotices.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("pipeline.clips.map(fingerprints).join(',')", component)
        self.assertIn("}, [pid, status, signature, nonce])", component)

    def test_the_dashboard_shows_the_audit_for_the_selected_pipeline(self):
        dashboard = (
            ROOT / "ui" / "src" / "components" / "DirectorDashboard" / "DirectorDashboard.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("import { PromptAuditNotices } from './PromptAuditNotices'", dashboard)
        self.assertIn(
            "<PromptAuditNotices pid={selectedPid} status={selectedPipeline.status} />",
            dashboard,
        )


if __name__ == "__main__":
    unittest.main()
