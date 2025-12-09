"""Process manager tests."""

import builtins
import pathlib
import signal
import sys

import pytest
from pytest_mock import MockerFixture

from dvc_task.proc.exceptions import ProcessNotTerminatedError, UnsupportedSignalError
from dvc_task.proc.manager import ProcessManager
from dvc_task.proc.process import ProcessInfo

from .conftest import PID_RUNNING


def test_send_signal(
    mocker: MockerFixture,
    process_manager: ProcessManager,
    finished_process: str,
    running_process: str,
):
    """Terminate signal should be sent."""
    mock_kill = mocker.patch("os.kill")
    process_manager.send_signal(running_process, signal.SIGTERM, False)
    mock_kill.assert_called_once_with(PID_RUNNING, signal.SIGTERM)

    if sys.platform != "win32":
        gid = 100
        mocker.patch("os.getpgid", return_value=gid)
        mock_killpg = mocker.patch("os.killpg")
        process_manager.send_signal(running_process, signal.SIGINT, True)
        mock_killpg.assert_called_once_with(gid, signal.SIGINT)
    else:
        mock_kill.reset_mock()
        process_manager.send_signal(
            running_process,
            signal.CTRL_C_EVENT,
            True,
        )
        mock_kill.assert_called_once_with(
            PID_RUNNING,
            signal.CTRL_C_EVENT,
        )

    mock_kill.reset_mock()
    with pytest.raises(ProcessLookupError):
        process_manager.send_signal(finished_process, signal.SIGTERM, False)
    mock_kill.assert_not_called()

    if sys.platform == "win32":
        with pytest.raises(UnsupportedSignalError):
            process_manager.send_signal(finished_process, signal.SIGABRT)


def test_send_signal_exception(
    mocker: MockerFixture,
    process_manager: ProcessManager,
    running_process: str,
):
    """Dead process lookup should fail."""

    def side_effect(*args):
        if sys.platform == "win32":
            err = OSError()
            err.winerror = 87
            raise err
        raise ProcessLookupError

    mocker.patch("os.kill", side_effect=side_effect)
    with pytest.raises(ProcessLookupError):
        process_manager.send_signal(running_process, signal.SIGTERM, False)
    assert process_manager[running_process].returncode == -1

    with pytest.raises(ProcessLookupError):
        process_manager.send_signal("nonexists", signal.SIGTERM, False)


if sys.platform == "win32":
    SIGKILL = signal.SIGTERM
    SIGINT = signal.CTRL_C_EVENT
else:
    SIGKILL = signal.SIGKILL
    SIGINT = signal.SIGINT


@pytest.mark.parametrize(
    "method, sig, group",
    [
        ("kill", SIGKILL, False),
        ("terminate", signal.SIGTERM, False),
        ("interrupt", SIGINT, True),
    ],
)
def test_kill_commands(
    mocker: MockerFixture,
    process_manager: ProcessManager,
    method: str,
    sig: signal.Signals,
    group: bool,
):
    """Test shortcut for different signals."""
    name = "process"
    mock_kill = mocker.patch.object(process_manager, "send_signal")
    func = getattr(process_manager, method)
    func(name)
    mock_kill.assert_called_once_with(name, sig, group)


def test_remove(
    mocker: MockerFixture,
    tmp_path: pathlib.Path,
    process_manager: ProcessManager,
    running_process: str,
    finished_process: str,
):
    """Process should be removed."""
    mocker.patch("os.kill", return_value=None)
    process_manager.remove(finished_process)
    assert not (tmp_path / finished_process).exists()
    with pytest.raises(ProcessNotTerminatedError):
        process_manager.remove(running_process)
    assert (tmp_path / running_process).exists()
    process_manager.remove(running_process, True)
    assert not (tmp_path / running_process).exists()


@pytest.mark.parametrize("force", [True, False])
def test_cleanup(  # noqa: PLR0913
    mocker: MockerFixture,
    tmp_path: pathlib.Path,
    process_manager: ProcessManager,
    running_process: str,
    finished_process: str,
    force: bool,
):
    """Process directory should be removed."""
    mocker.patch("os.kill", return_value=None)
    process_manager.cleanup(force)
    assert (tmp_path / running_process).exists() != force
    assert not (tmp_path / finished_process).exists()


def test_follow(
    mocker: MockerFixture,
    process_manager: ProcessManager,
    running_process: str,
):
    """Output should be followed and not duplicated."""
    orig_open = builtins.open
    mock_file = mocker.mock_open()()
    expected = ["foo\n", "bar\n", "b", "", "az\n"]
    mock_file.readline = mocker.Mock(side_effect=expected)

    def _open(path, *args, **kwargs):
        if path.endswith(".out"):
            return mock_file
        return orig_open(path, *args, **kwargs)

    mocker.patch("builtins.open", _open)
    mock_sleep = mocker.patch("time.sleep")
    follow_gen = process_manager.follow(running_process)
    for line in expected:
        if line:
            assert line == next(follow_gen)
    mock_sleep.assert_called_once_with(1)

    # Process exit with no further output should cause StopIteration
    # (raised as RuntimeError)
    mocker.patch.object(
        process_manager,
        "__getitem__",
        return_value=ProcessInfo(
            pid=PID_RUNNING, stdin=None, stdout=None, stderr=None, returncode=0
        ),
    )
    with pytest.raises(RuntimeError):
        next(follow_gen)
