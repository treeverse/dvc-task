"""Process task tests."""

import pathlib
from typing import Any

import pytest
from pytest_mock import MockerFixture

from dvc_task.proc.process import ManagedProcess
from dvc_task.proc.tasks import run


@pytest.mark.usefixtures("celery_app", "celery_worker")
def test_run(
    tmp_path: pathlib.Path,
    popen_pid: int,
    mocker: MockerFixture,
):
    """Task should run the process."""
    env = {"FOO": "1"}
    wdir = str(tmp_path / "wdir")
    name = "foo"
    init = mocker.spy(ManagedProcess, "__init__")
    result: dict[str, Any] = run.delay("/bin/foo", env=env, wdir=wdir, name=name).get()
    assert result["pid"] == popen_pid
    init.assert_called_once_with(mocker.ANY, "/bin/foo", env=env, wdir=wdir, name=name)
