from unittest import mock

import pytest


class _PatchProxy:
    def __init__(self, owner: "_MiniMocker") -> None:
        self._owner = owner

    def __call__(self, target, *args, **kwargs):
        patcher = mock.patch(target, *args, **kwargs)
        started = patcher.start()
        self._owner._patchers.append(patcher)
        return started

    def object(self, target, attribute, *args, **kwargs):
        patcher = mock.patch.object(target, attribute, *args, **kwargs)
        started = patcher.start()
        self._owner._patchers.append(patcher)
        return started


class _MiniMocker:
    Mock = mock.Mock
    ANY = mock.ANY
    sentinel = mock.sentinel

    def __init__(self) -> None:
        self._patchers = []
        self.patch = _PatchProxy(self)

    def stopall(self) -> None:
        while self._patchers:
            self._patchers.pop().stop()


@pytest.fixture
def mocker():
    helper = _MiniMocker()
    try:
        yield helper
    finally:
        helper.stopall()
