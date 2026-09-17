"""Gulyas Tasks core — v0 stub (RED). Everything raises, tests prove it."""


class TodoStore:
    def __init__(self):
        self._items = {}
        self._next_id = 1

    def add(self, title):
        raise NotImplementedError

    def toggle(self, item_id):
        raise NotImplementedError

    def list(self, filter="all"):
        raise NotImplementedError

    def remove(self, item_id):
        raise NotImplementedError

    def clear_done(self):
        raise NotImplementedError

    def to_dict(self):
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data):
        raise NotImplementedError
