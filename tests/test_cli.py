"""CLI smoke tests: --version, the no-input error, and bundled-sample resolution."""

import pytest

from steelreuse import __version__
from steelreuse.cli import main
from steelreuse.resources import sample_path


def test_version_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_inputs_errors_with_exit_two():
    # argparse uses exit code 2 for usage errors.
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_bundled_samples_resolve():
    assert sample_path("donor.json").exists()
    assert sample_path("demand.json").exists()
