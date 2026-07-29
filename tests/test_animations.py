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

from omenled import LED_COUNT, animations as police, device, protocol, state, transport
from omenled.journal import Journal

from test_transport import FakeHidDevice, fake_hid  # noqa: F401


@pytest.fixture
def lights(fake_hid, tmp_path):  # noqa: F811
    journal = Journal(session="t", directory=str(tmp_path))
    link = transport.Transport(journal, min_interval=0).open()
    yield device.Lighting(link), fake_hid
    link.close()
    journal.close()


def _example(name):
    path = os.path.join(ROOT, "examples", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alarm = _example("alarm")


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
    def test_a_lit_frame_covers_the_whole_diamond(self):
        # Splitting the strip in halves read as two separate lights on the
        # panel; a light bar alternates the whole fixture between colours.
        for pixels, _ in police.police_frames(cycles=1):
            lit = [p for p in pixels if p != (0, 0, 0)]
            assert len(lit) in (0, LED_COUNT)

    def test_both_colours_appear(self):
        seen = {p for pixels, _ in police.police_frames(cycles=1) for p in pixels}
        assert police.RED in seen and police.BLUE in seen

    def test_a_lit_frame_is_a_single_colour(self):
        for pixels, _ in police.police_frames(cycles=1):
            assert len(set(pixels)) == 1

    def test_it_starts_on_red_and_swap_starts_on_blue(self):
        first = next(pixels for pixels, _ in police.police_frames(cycles=1))
        swapped = next(pixels for pixels, _ in police.police_frames(cycles=1, swap=True))
        assert first[0] == police.RED
        assert swapped[0] == police.BLUE

    def test_no_two_lit_frames_run_together(self):
        frames = [pixels for pixels, _ in police.police_frames(cycles=1, flashes=2)]
        lit = [any(p != (0, 0, 0) for p in pixels) for pixels in frames]
        assert not any(a and b for a, b in zip(lit, lit[1:]))

    def test_the_on_time_is_shorter_than_the_gap_between_bursts(self):
        assert police.FLASH_ON < police.BURST_GAP

    def test_flash_count_per_colour_is_configurable(self):
        frames = list(police.police_frames(cycles=1, flashes=5))
        lit = sum(1 for pixels, _ in frames if any(p != (0, 0, 0) for p in pixels))
        assert lit == 10  # five per colour, two colours

    def test_the_led_count_is_configurable(self):
        pixels, _ = next(police.police_frames(count=12, cycles=1))
        assert len(pixels) == 12

    def test_frames_are_independent_copies(self):
        # play() must not be able to mutate a later frame through an earlier one.
        frames = [pixels for pixels, _ in police.police_frames(cycles=1)]
        frames[0][0] = (9, 9, 9)
        assert frames[2][0] != (9, 9, 9)


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


class TestPoliceCli:
    @pytest.fixture(autouse=True)
    def _no_sleeping(self, monkeypatch):
        """Play the frames without waiting for them.

        The pattern's timing is asserted on the generator; making the suite
        actually sleep through it buys nothing and slows every future run.
        """
        monkeypatch.setattr("omenled.animations.time.sleep", lambda _: None)

    def _run(self, light, argv):
        from omenled import cli

        args = cli.build_parser().parse_args(argv)
        return args.func(light, args)

    def test_it_renders_the_whole_diamond(self, lights):
        light, hid = lights
        self._run(light, ["police", "--cycles", "1", "--flashes", "1"])
        red_plane = hid.written[0][1:]
        assert set(red_plane[protocol.DATA_FIRST:]) == {0xFF}

    def test_it_restores_the_previous_picture(self, lights):
        light, hid = lights
        state.save([(0, 255, 0)] * LED_COUNT, 100)
        self._run(light, ["police", "--cycles", "1", "--flashes", "1"])
        green_plane = hid.written[-2][1:]
        assert set(green_plane[protocol.DATA_FIRST:]) == {0xFF}

    def test_it_does_not_overwrite_the_saved_picture(self, lights):
        # The siren is transient; `brightness` must still re-dim what the user
        # actually set.
        light, _ = lights
        state.save([(0, 255, 0)] * LED_COUNT, 70)
        self._run(light, ["police", "--cycles", "1", "--flashes", "1"])
        assert state.load() == ([(0, 255, 0)] * LED_COUNT, 70)

    def test_with_nothing_saved_it_ends_dark(self, lights):
        light, hid = lights
        self._run(light, ["police", "--cycles", "1", "--flashes", "1"])
        assert hid.written[-1][1] == protocol.OP_OFF

    def test_it_restores_even_when_playback_fails(self, lights, monkeypatch):
        light, hid = lights
        state.save([(0, 255, 0)] * LED_COUNT, 100)

        def boom(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr("omenled.animations.play", boom)
        with pytest.raises(KeyboardInterrupt):
            self._run(light, ["police", "--cycles", "1"])
        green_plane = hid.written[-2][1:]
        assert set(green_plane[protocol.DATA_FIRST:]) == {0xFF}

    def test_brightness_scales_the_animation(self, lights):
        light, hid = lights
        self._run(light, ["--brightness", "40", "police", "--cycles", "1",
                          "--flashes", "1"])
        assert hid.written[0][1 + protocol.DATA_FIRST] == 102

    def test_dry_run_touches_no_hardware_and_summarises(self, lights, capsys):
        light, hid = lights
        self._run(light, ["--dry-run", "police", "--cycles", "2"])
        assert hid.written == []
        out = capsys.readouterr().out
        assert "frames" in out and "all 60 LEDs" in out


class TestSpeed:
    def test_speed_1_is_the_baseline(self):
        assert next(police.police_frames(cycles=1))[1] == police.FLASH_ON

    def test_higher_speed_shortens_every_hold_proportionally(self):
        slow = [h for _, h in police.police_frames(cycles=1)]
        fast = [h for _, h in police.police_frames(cycles=1, speed=2.0)]
        assert all(f == pytest.approx(s / 2) for s, f in zip(slow, fast))

    def test_the_rhythm_is_preserved_not_just_the_on_time(self):
        # Shortening only the flash would turn a siren into a blink.
        # One cycle covers both colours, so take the first burst only.
        fast = list(police.police_frames(cycles=1, flashes=1, speed=4.0))
        on, off, gap = [h for _, h in fast][:3]
        assert on / off == pytest.approx(police.FLASH_ON / police.FLASH_OFF)
        assert gap / on == pytest.approx(police.BURST_GAP / police.FLASH_ON)

    def test_frame_count_is_unchanged_by_speed(self):
        assert len(list(police.police_frames(cycles=2, speed=5.0))) == \
               len(list(police.police_frames(cycles=2)))

    @pytest.mark.parametrize("bad", [0, -1, -0.5])
    def test_non_positive_speed_is_refused(self, bad):
        with pytest.raises(ValueError, match="positive"):
            list(police.police_frames(speed=bad))


class TestAnimationWriteSpacing:
    def test_police_asks_for_spacing_far_below_the_probing_default(self):
        """A frame is three writes; at the 50 ms probing margin the strobe ran
        2.5x slower than the pattern asks for."""
        from omenled import cli

        args = cli.build_parser().parse_args(["police"])
        assert args.min_interval <= 0.01

    def test_other_commands_keep_the_conservative_default(self):
        from omenled import cli

        args = cli.build_parser().parse_args(["fill", "red"])
        assert getattr(args, "min_interval", None) is None

    def test_the_cli_honours_a_command_s_requested_spacing(self, fake_hid):
        import omenled

        with omenled.open_lighting(min_interval=0.002) as lights:
            assert lights.transport._limiter.min_interval == 0.002


class TestSpeedParsing:
    @pytest.mark.parametrize("text", ["1", "2.5", "0.5", "20"])
    def test_accepted(self, text):
        from omenled import cli

        assert cli.speed(text) == float(text)

    @pytest.mark.parametrize("text", ["0", "-1", "21", "fast", ""])
    def test_rejected_with_a_usable_message(self, text):
        from omenled import cli

        with pytest.raises(Exception) as exc:
            cli.speed(text)
        assert "number" in str(exc.value) or "between 0 and 20" in str(exc.value)
