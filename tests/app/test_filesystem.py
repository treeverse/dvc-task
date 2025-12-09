"""Filesystem app tests."""

import json
import pathlib
from typing import TYPE_CHECKING, Any, Optional

import pytest
from celery.backends.filesystem import FilesystemBackend
from funcy import first

from dvc_task.app.filesystem import FSApp, _get_fs_config

if TYPE_CHECKING:
    from kombu.message import Message

TEST_MSG: dict[str, Any] = {
    "body": "",
    "content-encoding": "utf-8",
    "content-type": "application/json",
    "headers": {},
    "properties": {
        "correlation_id": "123",
        "reply_to": "456",
        "delivery_mode": 2,
        "delivery_info": {"exchange": "", "routing_key": "celery"},
        "priority": 0,
        "body_encoding": "base64",
        "delivery_tag": "789",
    },
}
EXPIRED_MSG: dict[str, Any] = {
    "body": "",
    "content-encoding": "utf-8",
    "content-type": "application/json",
    "headers": {"expires": 1},
    "properties": {
        "correlation_id": "123",
        "reply_to": "456",
        "delivery_mode": 2,
        "delivery_info": {"exchange": "", "routing_key": "celery"},
        "priority": 0,
        "body_encoding": "base64",
        "delivery_tag": "789-expired",
    },
}
TICKET_MSG: dict[str, Any] = {
    "body": "",
    "content-encoding": "utf-8",
    "content-type": "application/json",
    "headers": {"ticket": "abc123"},
    "properties": {
        "correlation_id": "123",
        "reply_to": "456",
        "delivery_mode": 2,
        "delivery_info": {"exchange": "celery.pidbox", "routing_key": "abc123"},
        "priority": 0,
        "body_encoding": "base64",
        "delivery_tag": "789-ticket",
    },
}


def write_tree(base, tree):
    for name, value in tree.items():
        path = base / name
        if isinstance(value, dict):
            path.mkdir(parents=True, exist_ok=True)
            write_tree(path, value)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")


def test_config(tmp_path: pathlib.Path):
    """Should return a filesystem broker/result config."""
    config = _get_fs_config(str(tmp_path), mkdir=True)
    assert (tmp_path / "broker" / "control").is_dir()
    assert (tmp_path / "broker" / "in").is_dir()
    assert (tmp_path / "broker" / "processed").is_dir()
    assert (tmp_path / "result").is_dir()
    assert config["broker_url"] == "filesystem://"


def test_fs_app(tmp_path: pathlib.Path):
    """App should be constructed with filesystem broker/result config."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    assert app.wdir == str(tmp_path)
    assert (tmp_path / "broker" / "in").is_dir()
    assert (tmp_path / "broker" / "processed").is_dir()
    assert (tmp_path / "result").is_dir()
    assert app.conf["broker_url"] == "filesystem://"
    backend = app.backend
    assert isinstance(backend, FilesystemBackend)
    assert backend.url == app.conf.result_backend


def test_iter_queued(tmp_path: pathlib.Path):
    """App should iterate over messages in 'broker/in'."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    msg: Optional[Message] = first(app.iter_queued())
    assert msg is None

    write_tree(tmp_path, {"broker": {"in": {"foo.msg": json.dumps(TEST_MSG)}}})

    msg = first(app.iter_queued())
    assert msg is not None
    for key, value in TEST_MSG.items():
        attr = getattr(msg, key.replace("-", "_"))
        if isinstance(attr, bytes):
            attr = attr.decode("utf-8")
        assert attr == value
    assert first(app.iter_processed()) is None


def test_iter_processed(tmp_path: pathlib.Path):
    """App should iterate over messages in 'broker/processed'."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    msg: Optional[Message] = first(app.iter_processed())
    assert msg is None

    write_tree(tmp_path, {"broker": {"processed": {"foo.msg": json.dumps(TEST_MSG)}}})
    msg = first(app.iter_processed())
    assert msg is not None
    for key, value in TEST_MSG.items():
        attr = getattr(msg, key.replace("-", "_"))
        if isinstance(attr, bytes):
            attr = attr.decode("utf-8")
        assert attr == value
    assert first(app.iter_queued()) is None


def test_reject(tmp_path: pathlib.Path):
    """Rejected message should be removed."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    write_tree(tmp_path, {"broker": {"in": {"foo.msg": json.dumps(TEST_MSG)}}})

    app.reject(TEST_MSG["properties"]["delivery_tag"])
    assert not (tmp_path / "broker" / "in" / "foo.msg").exists()

    write_tree(tmp_path, {"broker": {"in": {"foo.msg": json.dumps(TEST_MSG)}}})
    for msg in app.iter_queued():
        assert msg.delivery_tag
        app.reject(msg.delivery_tag)
    assert not (tmp_path / "broker" / "in" / "foo.msg").exists()

    with pytest.raises(ValueError):  # noqa: PT011
        app.reject(TEST_MSG["properties"]["delivery_tag"])


def test_purge(tmp_path: pathlib.Path):
    """Purge message should be removed."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    write_tree(tmp_path, {"broker": {"processed": {"foo.msg": json.dumps(TEST_MSG)}}})

    app.purge(TEST_MSG["properties"]["delivery_tag"])
    assert not (tmp_path / "broker" / "processed" / "foo.msg").exists()

    write_tree(tmp_path, {"broker": {"processed": {"foo.msg": json.dumps(TEST_MSG)}}})
    for msg in app.iter_processed():
        assert msg.delivery_tag
        app.purge(msg.delivery_tag)
    assert not (tmp_path / "broker" / "processed" / "foo.msg").exists()

    with pytest.raises(ValueError):  # noqa: PT011
        app.purge(TEST_MSG["properties"]["delivery_tag"])


def test_gc(tmp_path: pathlib.Path):
    """Expired messages and processed tickets should be removed."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    write_tree(
        tmp_path,
        {
            "broker": {
                "in": {
                    "expired.msg": json.dumps(EXPIRED_MSG),
                    "unexpired.msg": json.dumps(TEST_MSG),
                    "ticket.msg": json.dumps(TICKET_MSG),
                },
                "processed": {
                    "expired.msg": json.dumps(EXPIRED_MSG),
                    "unexpired.msg": json.dumps(TEST_MSG),
                    "ticket.msg": json.dumps(TICKET_MSG),
                },
            },
        },
    )

    app._gc()
    assert not (tmp_path / "broker" / "in" / "expired.msg").exists()
    assert (tmp_path / "broker" / "in" / "unexpired.msg").exists()
    assert (tmp_path / "broker" / "in" / "ticket.msg").exists()
    assert not (tmp_path / "broker" / "processed" / "expired.msg").exists()
    assert (tmp_path / "broker" / "in" / "unexpired.msg").exists()
    assert not (tmp_path / "broker" / "processed" / "ticket.msg").exists()


def test_gc_exclude(tmp_path: pathlib.Path):
    """Messages from excluded queues should not be removed."""
    app = FSApp(wdir=str(tmp_path), mkdir=True)
    write_tree(
        tmp_path,
        {
            "broker": {
                "in": {
                    "expired.msg": json.dumps(EXPIRED_MSG),
                    "unexpired.msg": json.dumps(TEST_MSG),
                    "ticket.msg": json.dumps(TICKET_MSG),
                },
                "processed": {
                    "expired.msg": json.dumps(EXPIRED_MSG),
                    "unexpired.msg": json.dumps(TEST_MSG),
                    "ticket.msg": json.dumps(TICKET_MSG),
                },
            },
        },
    )

    app._gc(exclude=["celery"])
    assert (tmp_path / "broker" / "in" / "expired.msg").exists()
    assert (tmp_path / "broker" / "in" / "unexpired.msg").exists()
    assert (tmp_path / "broker" / "in" / "ticket.msg").exists()
    assert (tmp_path / "broker" / "processed" / "expired.msg").exists()
    assert (tmp_path / "broker" / "in" / "unexpired.msg").exists()
    assert not (tmp_path / "broker" / "processed" / "ticket.msg").exists()
