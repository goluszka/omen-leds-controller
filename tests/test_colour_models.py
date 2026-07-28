"""Tests for the colour-model elimination logic.

This is the part of the workbench that decides which readings of offsets 4..6
survive contact with observation, so a bug here would silently keep a wrong
model alive or discard the right one.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import probe


class TestNameRgb:
    @pytest.mark.parametrize(
        "rgb,expected",
        [
            ((0xFF, 0x00, 0x00), "red"),
            ((0x00, 0xFF, 0x00), "green"),
            ((0x00, 0x00, 0xFF), "blue"),
            ((0xFF, 0xFF, 0xFF), "white"),
            ((0x00, 0x00, 0x00), "off"),
            ((0x90, 0x90, 0x90), "white"),
            ((0x10, 0x10, 0x10), "off"),
        ],
    )
    def test_names_the_unmistakable_cases(self, rgb, expected):
        assert probe.name_rgb(*rgb) == expected

    @pytest.mark.parametrize("rgb", [(0xFF, 0xFF, 0x00), (0x00, 0xFF, 0xFF), (0xFF, 0x00, 0xFF)])
    def test_ambiguous_mixtures_are_other_not_forced_into_a_bucket(self, rgb):
        assert probe.name_rgb(*rgb) == "other"


class TestVerdictColour:
    def test_plain_rgb_survives_when_the_observations_are_plain_rgb(self):
        text = probe.verdict_colour(
            {"ff0000": "red", "00ff00": "green", "0000ff": "blue",
             "ffffff": "white", "000000": "off"}
        )
        assert "plain RGB (4,5,6)" in text
        assert "Consistent with exactly one model" in text

    def test_the_real_observation_so_far_rules_plain_rgb_out(self):
        # ff ff ff produced red on the hardware, which no plain reading allows.
        text = probe.verdict_colour({"ffffff": "red"})
        assert "ruled out: plain RGB (4,5,6)" in text

    def test_bgr_survives_a_swapped_pair(self):
        text = probe.verdict_colour({"ff0000": "blue", "0000ff": "red"})
        assert "BGR (6,5,4)" in text
        assert "ruled out: plain RGB (4,5,6)" in text

    def test_reports_honestly_when_nothing_fits(self):
        text = probe.verdict_colour(
            {"ff0000": "green", "00ff00": "green", "0000ff": "green",
             "ffffff": "red", "000000": "white"}
        )
        assert "No candidate model fits" in text

    def test_says_when_models_remain_indistinguishable(self):
        # A single off observation cannot separate orderings.
        text = probe.verdict_colour({"000000": "off"})
        assert "Still consistent with" in text
        assert "More trials needed" in text

    def test_unnameable_answers_are_excluded_rather_than_guessed(self):
        assert "may not be colour at all" in probe.verdict_colour({"ff0000": "hard to tell"})

    def test_survives_an_empty_result_set(self):
        assert probe.verdict_colour({})


class TestVerdictZones:
    def test_distinct_positions_make_byte_1_a_candidate_selector(self):
        text = probe.verdict_zones(
            {"00": "bottom left", "01": "bottom right", "02": "top left"}
        )
        # Candidate, not conclusion: it still has to survive a prediction.
        assert "candidate segment selector" in text
        assert "predicting a position before sending" in text

    def test_one_position_everywhere_means_it_is_not_the_selector(self):
        text = probe.verdict_zones({"00": "bottom left", "01": "bottom left"})
        assert "does not select" in text

    def test_nothing_lighting_is_reported_as_such(self):
        text = probe.verdict_zones({"00": "", "01": "nothing"})
        assert "not the segment" in text

    def test_blank_answers_are_not_counted_as_positions(self):
        text = probe.verdict_zones({"00": "bottom left", "01": "", "02": "nothing"})
        assert "1 of 8 values" in text


class TestRegistry:
    def test_the_new_experiments_are_registered(self):
        assert "colour" in probe.EXPERIMENTS
        assert "zones" in probe.EXPERIMENTS

    def test_the_known_opcodes_are_named_rather_than_inlined(self):
        assert probe.OP_SET == 0x05
        assert probe.OP_BLANK == 0x0D


class TestPositionNormalisation:
    @pytest.mark.parametrize(
        "answer",
        [
            "left bottom",
            "left bottom, slighlty shifted",
            "left bottom, slightly shifted",
            "red left bottom segment, slightly smaller",
            "bottom left",
            "lewy dolny",
        ],
    )
    def test_the_same_place_described_differently_normalises_alike(self, answer):
        assert probe.position(answer) == ("bottom", "left")

    def test_a_qualifier_does_not_outvote_the_subject(self):
        # "bottom left ... slight part of right bottom" names bottom-left and
        # then qualifies it; treating the qualifier as the answer once produced
        # a false 'byte 1 is the selector' verdict.
        answer = "red bottom left segment, with slight part of right bottom segment"
        assert probe.position(answer) == ("bottom", "left")

    def test_genuinely_different_places_differ(self):
        assert probe.position("top right") != probe.position("bottom left")

    def test_an_answer_with_no_position_words_is_unrecognised(self):
        assert probe.position("it went red") is None


class TestVerdictZonesAfterFix:
    def test_wording_variants_do_not_count_as_movement(self):
        text = probe.verdict_zones(
            {
                "00": "left bottom",
                "01": "left bottom, slighlty shifted",
                "02": "left bottom, slightly shifted",
                "03": "left bottom",
            }
        )
        assert "does not select" in text
        assert "Wording differences" in text

    def test_it_points_at_the_animation_control_when_nothing_moved(self):
        text = probe.verdict_zones({"00": "left bottom", "01": "left bottom"})
        assert "experiment animation" in text

    def test_real_movement_is_still_detected(self):
        text = probe.verdict_zones({"00": "bottom left", "01": "top right"})
        assert "candidate segment selector" in text


class TestVerdictAnimation:
    def test_idle_movement_invalidates_the_earlier_results(self):
        text = probe.verdict_animation({"idle_moves": "yes"})
        assert "An animation IS running" in text
        assert "invalidates" in text

    def test_drift_after_a_write_also_counts(self):
        text = probe.verdict_animation({"idle_moves": "no", "on_drifts": "yes"})
        assert "An animation IS running" in text

    def test_a_static_result_promotes_the_earlier_findings(self):
        text = probe.verdict_animation({"idle_moves": "no", "on_drifts": "no"})
        assert "No animation detected" in text
        assert "past offset 10" in text

    def test_polish_yes_is_understood(self):
        assert "An animation IS running" in probe.verdict_animation({"idle_moves": "tak"})

    def test_self_restoring_lighting_is_called_out(self):
        text = probe.verdict_animation({"idle_moves": "no", "off_persists": "came back"})
        assert "temporary" in text


class TestFill:
    def test_sets_an_inclusive_range(self):
        out = probe._fill(bytes(64), 4, 6)
        assert out[3] == 0x00
        assert out[4:7] == b"\xff\xff\xff"
        assert out[7] == 0x00

    def test_leaves_the_opcode_alone_when_the_range_starts_at_one(self):
        base = probe.protocol.build_probe(opcode=0x05)
        assert probe._fill(base, 1, 63)[0] == 0x05

    def test_a_single_byte_range_touches_one_byte(self):
        assert probe._fill(bytes(64), 9, 9)[9] == 0xFF
        assert probe._fill(bytes(64), 9, 9)[8] == 0x00

    def test_does_not_mutate_the_input(self):
        base = bytes(64)
        probe._fill(base, 0, 63)
        assert base == bytes(64)


class TestVerdictBisect:
    def test_an_ignored_body_is_reported_as_an_on_off_switch(self):
        text = probe.verdict_bisect({"reference": "bottom left red", "responsive": "none"})
        assert "ignores the entire packet body" in text
        assert "on/off switch" in text

    def test_a_responsive_byte_is_named_with_its_hypothesis(self):
        text = probe.verdict_bisect({"reference": "x", "responsive": "7"})
        assert "Byte 7" in text
        assert "brightness" in text

    def test_a_byte_outside_the_hypothesis_is_still_reported(self):
        text = probe.verdict_bisect({"reference": "x", "responsive": "40"})
        assert "Byte 40" in text
        assert "not in the current hypothesis" in text

    def test_it_asks_for_confirmation_rather_than_declaring_the_field_solved(self):
        text = probe.verdict_bisect({"reference": "x", "responsive": "12"})
        assert "predict" in text
        assert "If several matter" in text

    def test_an_incomplete_run_says_so(self):
        assert "did not complete" in probe.verdict_bisect({"reference": "x"})


class TestDiffersDetection:
    @pytest.mark.parametrize("answer", ["different", "differs", "inne", "zmieniło się"])
    def test_change_words_are_recognised(self, answer):
        assert re.search(r"\b(diff\w*|inn\w*|zmien\w*)\b", answer.lower())

    @pytest.mark.parametrize("answer", ["same", "identical", "no change"])
    def test_sameness_is_not_read_as_change(self, answer):
        assert not re.search(r"\b(diff\w*|inn\w*|zmien\w*)\b", answer.lower())
