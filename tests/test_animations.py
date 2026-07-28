"""Animation patterns, tested without hardware.

The generators are pure so the shapes can be checked here rather than by
watching the monitor and hoping.
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from omenled import LED_COUNT, protocol, state


def _example(name):
    path = os.path.join(ROOT, "examples", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alarm = _example("alarm")
police = _example("police")


class TestAlarmEnvelope:
    def test_it_starts_dark(self):
        assert alarm.envelope(0.0) == 0

    def test_it_peaks_at_the_end_of_the_attack(self):
        assert alarm.envelope(0.12) == pytest.approx(1.0, abs=0.01)

    def test_it_returns_to_dark(self):
        assert alarm.envelope(1.0) == pytest.approx(0.0, abs=0.01)

    def test_the_attack_is_faster_than_the_decay(self):
        # What distinguishes an alarm from a breath: it snaps on and falls away.
        rise = alarm.envelope(0.06) - alarm.envelope(0.0)
        fall = alarm.envelope(0.50) - alarm.envelope(0.56)
        assert rise > fall

    def test_intensity_never_leaves_0_1(self):
        for step in range(101):
            assert 0.0 <= alarm.envelope(step / 100) <= 1.0


class TestAlarmFrames:
    def _frames(self, **kw):
        return list(alarm.alarm_frames(protocol.parse_colour("#9E0000"), **kw))

    def test_frame_count_follows_pulses_and_steps(self):
        assert len(self._frames(pulses=3, steps=10)) == 30

    def test_every_frame_covers_every_led(self):
        assert all(len(pixels) == LED_COUNT for pixels, _ in self._frames())

    def test_it_uses_the_requested_hue_not_an_arbitrary_red(self):
        brightest = max(self._frames(), key=lambda f: sum(f[0][0]))[0][0]
        # #9E0000 is pure red channel; green and blue must stay at zero.
        assert brightest[1] == 0 and brightest[2] == 0
        assert brightest[0] > 0

    def test_each_pulse_reaches_full_intensity(self):
        peak = max(pixels[0][0] for pixels, _ in self._frames())
        assert peak == 0x9E

    def test_holds_sum_to_the_requested_duration(self):
        total = sum(hold for _, hold in self._frames(pulses=2, pulse_seconds=0.5))
        assert total == pytest.approx(1.0, abs=0.01)


class TestPoliceFrames:
    def test_halves_are_disjoint_and_cover_the_strip(self):
        left = police.half_lit(police.RED, True)
        right = police.half_lit(police.BLUE, False)
        assert sum(1 for p in left if p != (0, 0, 0)) == LED_COUNT // 2
        assert all(l == (0, 0, 0) or r == (0, 0, 0) for l, r in zip(left, right))

    def test_both_colours_appear(self):
        seen = {p for pixels, _ in police.police_frames(cycles=1) for p in pixels}
        assert police.RED in seen and police.BLUE in seen

    def test_red_and_blue_light_opposite_halves(self):
        frames = [pixels for pixels, _ in police.police_frames(cycles=1)]
        red_frame = next(f for f in frames if police.RED in f)
        blue_frame = next(f for f in frames if police.BLUE in f)
        assert red_frame.index(police.RED) != blue_frame.index(police.BLUE)

    def test_swap_puts_the_colours_on_the_other_halves(self):
        normal = next(f for f, _ in police.police_frames(cycles=1) if police.RED in f)
        swapped = next(f for f, _ in police.police_frames(cycles=1, swap=True)
                       if police.RED in f)
        assert normal.index(police.RED) != swapped.index(police.RED)

    def test_no_two_lit_frames_run_together(self):
        # Every flash is separated by darkness, which is what makes it strobe
        # rather than stay on.
        frames = [pixels for pixels, _ in police.police_frames(cycles=1, flashes=2)]
        lit = [any(p != (0, 0, 0) for p in pixels) for pixels in frames]
        assert not any(a and b for a, b in zip(lit, lit[1:]))

    def test_it_both_starts_and_ends_on_a_flash_boundary(self):
        frames = [pixels for pixels, _ in police.police_frames(cycles=1, flashes=2)]
        assert any(p != (0, 0, 0) for p in frames[0])
        assert all(p == (0, 0, 0) for p in frames[-1])

    def test_the_on_time_is_shorter_than_the_gap_between_bursts(self):
        # Uneven timing is what makes it read as a siren.
        holds = [hold for _, hold in police.police_frames(cycles=1, flashes=2)]
        assert police.FLASH_ON < police.SIDE_GAP
        assert police.SIDE_GAP in holds

    def test_flash_count_per_side_is_configurable(self):
        frames = list(police.police_frames(cycles=1, flashes=5))
        lit = sum(1 for pixels, _ in frames if any(p != (0, 0, 0) for p in pixels))
        assert lit == 10  # five per side, two sides


class TestRememberedFrame:
    def test_nothing_saved_yields_nothing(self):
        assert state.remembered_frame(LED_COUNT) is None

    def test_it_applies_the_saved_brightness(self):
        state.save([(200, 100, 50)] * LED_COUNT, 50)
        assert state.remembered_frame(LED_COUNT)[0] == (100, 50, 25)

    def test_it_pads_a_short_frame_to_the_led_count(self):
        state.save([(255, 0, 0)], 100)
        frame = state.remembered_frame(LED_COUNT)
        assert len(frame) == LED_COUNT
        assert frame[-1] == (0, 0, 0)

    def test_it_truncates_a_long_frame(self):
        state.save([(255, 0, 0)] * 200, 100)
        assert len(state.remembered_frame(LED_COUNT)) == LED_COUNT

    def test_without_a_count_it_returns_what_was_saved(self):
        state.save([(1, 2, 3)] * 4, 100)
        assert len(state.remembered_frame()) == 4
