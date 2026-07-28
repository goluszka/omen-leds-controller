"""Tests for the colour-model elimination logic.

This is the part of the workbench that decides which readings of offsets 4..6
survive contact with observation, so a bug here would silently keep a wrong
model alive or discard the right one.
"""

import os
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
    def test_distinct_positions_mean_byte_1_is_the_selector(self):
        text = probe.verdict_zones(
            {"00": "bottom left", "01": "bottom right", "02": "top left"}
        )
        assert "is the segment selector" in text

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
