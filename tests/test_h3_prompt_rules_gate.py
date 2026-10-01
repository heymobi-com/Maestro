"""Every H3 prompt rule is proved in both directions.

A rule that cannot be made to fire is not a rule. Each rule below has a prompt
that must pass and a mutant that must fail, and the mutant has to fail *by name*
-- the specific message, not merely some error. The pass side runs on prompts the
compiler itself produced, because a rule that fires on the compiler's own
artifact is a wrong rule.

Borrowed from OpenH3-IR (ruashots/open-h3-ir), which gates its own 100+ rules
this way and demoted two rules to guidance after their published example tripped
them. The mutants here are the same idea applied to Maestro's contract.
"""

import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    compile_h3_official_prompt,
    validate_h3_prompt_contract,
)
from services.director.performance_expression import (  # noqa: E402
    PERFORMANCE_EXPRESSION_DIRECTION,
    asks_for_stillness,
    names_visible_expression,
    needs_expression,
    performance_expression_problems,
)

BODY = (
    "A quiet rooftop at dusk, cinematic and warm. [Shot 1] The lead singer stands "
    "at the parapet while the city lights come up. [Shot 2] At 00:04.500, a closer "
    "angle holds the singer against the skyline."
)
SUBJECTS = [{
    "character_id": "lead",
    "speaker_name": "Ana",
    "visual_description": "the lead singer in a grey coat",
    "performance_role": "vocalist",
}]
IMAGE = {"type": "image", "path": "ref1.png", "role": "Ana"}
IMAGE_2 = {"type": "image", "path": "ref2.png", "role": "Ana"}
DRIVING_AUDIO = {
    "type": "audio", "path": "song.wav", "role": "Ana", "audio_intent": "drive",
}


def references_for(mode):
    if mode == "ref2va":
        return [IMAGE, DRIVING_AUDIO]
    if mode == "i2va":
        return [IMAGE]
    if mode in ("fl2va", "l2va"):
        return [IMAGE, IMAGE_2]
    return None


def swap_lines(text, first, second):
    """Move the line holding `first` to where the line holding `second` is."""

    lines = text.split("\n")
    i = next(n for n, line in enumerate(lines) if line.startswith(first))
    j = next(n for n, line in enumerate(lines) if line.startswith(second))
    lines[i], lines[j] = lines[j], lines[i]
    return "\n".join(lines)


def mutate_field(text, field, mutate):
    """Change one field's value only.

    Mutants have to be surgical: ``[Shot 1]`` appears in ``retention_analysis``
    before it appears in the body, so a naive string replace mutates the wrong
    rule's input and silently proves nothing.
    """

    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith(field + ": "):
            lines[index] = f"{field}: {mutate(line[len(field) + 2:])}"
            break
    return "\n".join(lines)


class RuleGate(unittest.TestCase):
    """One prompt per mode the compiler builds, plus a mutant per rule."""

    @classmethod
    def setUpClass(cls):
        cls.base = {}
        cls.clean = {}
        cls.anchors = {}
        for mode in ("t2va", "i2va", "fl2va", "l2va", "ref2va"):
            references = references_for(mode)
            # Only Ref2VA states the cast by name in the body, so the other modes
            # are checked without subject anchors: that is the compiler's shape,
            # not a gap this gate is meant to close.
            subjects = SUBJECTS if mode == "ref2va" else []
            compiled, _ = compile_h3_official_prompt(
                BODY,
                subjects,
                [],
                mode=mode,
                duration_seconds=7,
                references=references,
                audio_plan={"mode": "audio_driven"},
            )
            cls.base[mode] = compiled
            cls.anchors[mode] = {
                "references": references,
                "subjects": subjects,
            }
            cls.clean[mode] = validate_h3_prompt_contract(
                compiled, [], mode=mode, **cls.anchors[mode]
            )

    def errors(self, mode, text=None, **overrides):
        anchors = dict(self.anchors[mode])
        anchors.update(overrides)
        return validate_h3_prompt_contract(
            self.base[mode] if text is None else text, [], mode=mode, **anchors
        )

    def test_must_pass_compiler_output(self):
        for mode, errors in self.clean.items():
            with self.subTest(mode=mode):
                self.assertEqual([], errors, f"{mode} base prompt")

    def test_every_rule_fires_by_name(self):
        # (label, mode, mutant, expected fragments, expected error count). The
        # count is declared so a mutant that trips extra rules has to say so.
        cases = [
            (
                "duplicated field", "ref2va",
                lambda t: t + "\n\nsummary: a second summary",
                ["expected one summary field, found 2"], 1,
            ),
            (
                "missing field", "ref2va",
                lambda t: "\n".join(
                    line for line in t.split("\n")
                    if not line.startswith("non_diegetic_music")
                ),
                ["expected one non_diegetic_music field, found 0"], 1,
            ),
            (
                "fields out of order", "ref2va",
                lambda t: swap_lines(t, "overall_soundscape", "non_diegetic_music"),
                ["Context-IR fields are out of order"], 1,
            ),
            (
                "field the mode does not take", "t2va",
                lambda t: t + "\nsubject_definitions: nonsense",
                ["unexpected subject_definitions field for t2va"], 1,
            ),
            (
                "shot marker lost", "ref2va",
                lambda t: mutate_field(
                    t, "detailed_description",
                    # Every mention, not just the marker: the body also says
                    # "synchronized to <Audio 1> throughout [Shot 1]", and one
                    # surviving mention satisfies the rule (see the gap test).
                    lambda v: v.replace("[Shot 1]", "[Shot 2]"),
                ),
                ["detailed_description is missing [Shot 1]"], 1,
            ),
            (
                "no visual style before the first shot", "ref2va",
                lambda t: mutate_field(
                    t, "detailed_description",
                    lambda v: v[v.index("[Shot 1]"):],
                ),
                ["missing the visual-style opening before [Shot 1]"], 1,
            ),
            (
                "legacy wrapper left in the payload", "ref2va",
                lambda t: t + "\nFINAL BLOCKING: nobody moves.",
                ["legacy Maestro prompt wrapper remains"], 1,
            ),
            (
                "orphaned control character", "ref2va",
                lambda t: t + "\nan invisible \x8b byte",
                ["orphaned C1 control character"], 1,
            ),
            (
                "i2va without its alignment line", "i2va",
                lambda t: t.split("\n\n", 1)[1],
                ["I2VA prompt is missing the official 0.00-second alignment line"], 1,
            ),
            (
                "fl2va without its alignment line", "fl2va",
                lambda t: t.split("\n\n", 1)[1],
                ["FL2VA prompt is missing the official two-picture alignment line"], 1,
            ),
            (
                "l2va without its alignment line", "l2va",
                lambda t: t.split("\n\n", 1)[1],
                ["L2VA prompt is missing the official last-picture alignment line"], 1,
            ),
            (
                "t2va carrying a picture alignment header", "t2va",
                lambda t: "For the target video, " + t,
                # A header before the first field name also costs the field its
                # line-start, so this mutant trips both rules and declares both.
                ["T2VA prompt must not contain a picture-alignment header",
                 "expected one integrated_multimodal_description field"], 2,
            ),
            (
                "ref2va summary without its task type", "ref2va",
                lambda t: mutate_field(
                    t, "summary",
                    lambda v: v.replace("[reference generation + audio reuse] ", "", 1),
                ),
                ["Ref2VA summary is missing its official task-type prefix"], 1,
            ),
            (
                "driving performance never acted on the face", "ref2va",
                lambda t: t.replace(" " + PERFORMANCE_EXPRESSION_DIRECTION, "", 1),
                ["never acted on the face"], 1,
            ),
        ]
        for label, mode, mutate, fragments, expected in cases:
            with self.subTest(rule=label):
                errors = self.errors(mode, mutate(self.base[mode]))
                self.assertEqual(
                    expected, len(errors), f"{label}: {errors}"
                )
                for fragment in fragments:
                    self.assertTrue(
                        any(fragment in error for error in errors),
                        f"{label}: {fragment!r} not in {errors}",
                    )

    def test_missing_canonical_context_is_refused(self):
        errors = self.errors("ref2va", context_anchors=["Valeria"])
        self.assertEqual(1, len(errors), errors)
        self.assertIn("missing canonical identity/world context: Valeria", errors[0])

    def test_known_gap_a_passing_mention_satisfies_the_shot_marker_rule(self):
        """A recorded weakness, not an endorsement.

        The body also carries "Visible performance and lip movement remain
        synchronized to <Audio 1> throughout [Shot 1].", so replacing the shot
        marker alone leaves the rule satisfied by that aside. Tightening the
        check to the *first* marker in the body would refuse exactly this text
        and nothing legitimate, but that is a behaviour change to an upstream
        rule and is deliberately not made here.
        """

        marker_only = mutate_field(
            self.base["ref2va"], "detailed_description",
            lambda value: value.replace("[Shot 1]", "[Shot 2]", 1),
        )
        self.assertEqual([], self.errors("ref2va", marker_only))

    def test_compiler_supplies_the_expression_it_requires(self):
        """The rule can never refuse the compiler's own output."""

        compiled = self.base["ref2va"]
        self.assertIn(PERFORMANCE_EXPRESSION_DIRECTION, compiled)
        self.assertEqual([], self.errors("ref2va"))
        self.assertEqual(
            [], performance_expression_problems(
                compiled, compiled, references_for("ref2va"), SUBJECTS,
            ),
        )

    def test_expression_direction_is_not_added_twice(self):
        """Recompiling a compiled prompt must not stack the direction."""

        recompiled, _ = compile_h3_official_prompt(
            self.base["ref2va"],
            SUBJECTS,
            [],
            mode="ref2va",
            duration_seconds=7,
            references=references_for("ref2va"),
            audio_plan={"mode": "audio_driven"},
        )
        self.assertEqual(1, recompiled.count(PERFORMANCE_EXPRESSION_DIRECTION))
        self.assertEqual([], self.errors("ref2va", recompiled))

    def test_no_direction_when_the_shot_already_acts_it(self):
        acted = BODY.replace(
            "while the city lights come up",
            "while the city lights come up, her brow lifting as she breathes in",
        )
        compiled, _ = compile_h3_official_prompt(
            acted, SUBJECTS, [], mode="ref2va", duration_seconds=7,
            references=references_for("ref2va"),
            audio_plan={"mode": "audio_driven"},
        )
        self.assertNotIn(PERFORMANCE_EXPRESSION_DIRECTION, compiled)
        self.assertEqual([], self.errors("ref2va", compiled))


class ExpressionRuleBounds(unittest.TestCase):
    """The four ways to be outside the rule, and the two ways to be inside it."""

    def test_bounds(self):
        cases = [
            ("driving audio and a performer is the rule", True, SUBJECTS),
            ("nobody on screen to act it", False, []),
            ("no performance to drive", False, SUBJECTS),
        ]
        for label, driving, subjects in cases:
            with self.subTest(bound=label):
                self.assertEqual(
                    driving,
                    needs_expression(BODY, driving=driving, subjects=subjects),
                )
        self.assertFalse(needs_expression(
            "Her gaze settles on the street below.", driving=True, subjects=SUBJECTS,
        ), "prose that already names the face needs no direction")
        self.assertFalse(needs_expression(
            BODY + " The guitarist remains a non-singing presence.",
            driving=True, subjects=SUBJECTS,
        ), "a shot asking for stillness is not defective")

    def test_emotion_inside_the_dialogue_tag_does_not_count(self):
        text = f"<d>[Spanish] {PERFORMANCE_EXPRESSION_DIRECTION}</d>"
        self.assertFalse(names_visible_expression(text))
        self.assertTrue(needs_expression(text, driving=True, subjects=SUBJECTS))
        self.assertTrue(asks_for_stillness("she does not sing here"))
        # Inside <d> is not prose either way: the tag carries spoken words, so a
        # stillness instruction written there cannot exempt the shot. H3 reads
        # the face only from the prose, which is why this module exists.
        self.assertTrue(needs_expression(
            "<d>[Spanish] she does not sing</d>", driving=True, subjects=SUBJECTS,
        ))
        self.assertFalse(needs_expression(
            "she does not sing here", driving=True, subjects=SUBJECTS,
        ))

    def test_reference_manifest_decides_not_the_wording(self):
        self.assertEqual(
            [],
            performance_expression_problems(
                BODY, BODY, [{"type": "image", "path": "a.png"}], SUBJECTS,
            ),
            "an image reference does not drive a performance",
        )
        self.assertEqual(
            1,
            len(performance_expression_problems(
                BODY, BODY, references_for("ref2va"), SUBJECTS,
            )),
        )


if __name__ == "__main__":
    unittest.main()
