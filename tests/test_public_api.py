"""The surface other projects import.

Anything asserted here is a promise: changing it breaks a dependent project
silently, since these are the names people write into their own code.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import omenled
from omenled import device, journal, paths, transport

from test_transport import FakeHidDevice, fake_hid  # noqa: F401


class TestExports:
    @pytest.mark.parametrize("name", omenled.__all__)
    def test_everything_promised_is_importable(self, name):
        assert hasattr(omenled, name)

    def test_the_headline_entry_points_exist(self):
        assert callable(omenled.open_lighting)
        assert callable(omenled.parse_colour)
        assert omenled.LED_COUNT == 60

    def test_errors_are_exported_so_callers_can_catch_them(self):
        for exc in (omenled.DeviceNotFound, omenled.TransportError,
                    omenled.RenderRejected):
            assert issubclass(exc, Exception)

    def test_it_has_a_version(self):
        assert omenled.__version__.count(".") == 2


class TestOpenLighting:
    def test_yields_a_lighting_and_closes_the_transport(self, fake_hid):
        with omenled.open_lighting() as lights:
            assert isinstance(lights, device.Lighting)
            lights.fill((255, 0, 0)).render(verify=False)
        assert fake_hid.closed

    def test_journalling_is_off_by_default(self, fake_hid, tmp_path):
        with omenled.open_lighting() as lights:
            lights.fill((1, 1, 1)).render(verify=False)
        # Nothing written anywhere under the (redirected) data directory.
        assert not os.path.isdir(paths.sessions_dir())

    def test_journalling_can_be_switched_on(self, fake_hid):
        with omenled.open_lighting(journal=True) as lights:
            lights.fill((1, 1, 1)).render(verify=False)
        assert any(journal.iter_sessions())

    def test_a_supplied_journal_is_used(self, fake_hid, tmp_path):
        log = journal.Journal(session="mine", directory=str(tmp_path))
        with omenled.open_lighting(journal=log) as lights:
            lights.fill((1, 1, 1)).render(verify=False)
        assert '"kind": "tx"' in open(log.path).read()

    def test_the_transport_closes_even_if_the_block_raises(self, fake_hid):
        with pytest.raises(RuntimeError):
            with omenled.open_lighting():
                raise RuntimeError("boom")
        assert fake_hid.closed

    def test_a_missing_device_raises_before_the_block_runs(self, monkeypatch):
        class NoDevices:
            @staticmethod
            def enumerate(vid, pid):
                return []

        monkeypatch.setattr(transport, "hid", NoDevices)
        with pytest.raises(omenled.DeviceNotFound):
            with omenled.open_lighting():
                pytest.fail("the block must not run")


class TestNullJournal:
    def test_it_satisfies_everything_transport_calls(self):
        null = journal.NullJournal()
        null.note("x")
        null.tx(b"\x00")
        null.rx(None)
        null.record("note")
        null.observation("x")
        null.close()

    def test_it_writes_nothing(self, tmp_path):
        before = list(os.listdir(tmp_path))
        journal.NullJournal().tx(b"\x00" * 64)
        assert list(os.listdir(tmp_path)) == before


class TestDataPaths:
    def test_data_never_lands_inside_the_package(self, monkeypatch):
        monkeypatch.delenv(paths.ENV_VAR, raising=False)
        package = os.path.dirname(os.path.abspath(omenled.__file__))
        assert not paths.data_dir().startswith(package)

    def test_the_env_var_overrides_the_location(self, monkeypatch, tmp_path):
        monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
        assert paths.data_dir() == str(tmp_path)

    def test_a_tilde_in_the_override_is_expanded(self, monkeypatch):
        monkeypatch.setenv(paths.ENV_VAR, "~/somewhere")
        assert paths.data_dir().startswith(os.path.expanduser("~"))

    def test_sessions_and_state_live_under_the_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
        assert paths.sessions_dir().startswith(str(tmp_path))
        assert paths.state_file().startswith(str(tmp_path))


class TestCliEntryPoint:
    def test_the_module_runs_as_a_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "omenled.cli", "--help"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0
        assert "brightness" in result.stdout

    def test_dry_run_needs_no_hardware(self):
        result = subprocess.run(
            [sys.executable, "-m", "omenled.cli", "--dry-run", "fill", "red"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0
        assert "red    plane: 05" in result.stdout


class TestPackagingMetadata:
    """setup.cfg and the package must not disagree about what they are."""

    def _setup_cfg(self):
        import configparser

        parser = configparser.ConfigParser()
        parser.read(os.path.join(ROOT, "setup.cfg"))
        return parser

    def test_the_declared_version_matches_the_package(self):
        assert self._setup_cfg()["metadata"]["version"] == omenled.__version__

    def test_the_console_script_points_at_a_real_callable(self):
        entry = self._setup_cfg()["options.entry_points"]["console_scripts"]
        target = entry.split("=", 1)[1].strip()
        module_path, _, attribute = target.partition(":")
        module = __import__(module_path, fromlist=[attribute])
        assert callable(getattr(module, attribute))

    def test_the_package_is_listed_for_installation(self):
        assert "omenled" in self._setup_cfg()["options"]["packages"]

    def test_py_typed_ships_so_annotations_reach_consumers(self):
        assert os.path.exists(os.path.join(ROOT, "omenled", "py.typed"))
        assert "py.typed" in self._setup_cfg()["options.package_data"]["omenled"]

    def test_the_runtime_dependency_is_declared(self):
        assert "hidapi" in self._setup_cfg()["options"]["install_requires"]
