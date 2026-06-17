"""Shared test helper.

The App class can't be instantiated in a unit test (it needs a Tk root, API
keys, and live network clients). But most of the pure/detection helpers are
instance methods that only read ``self.provider`` / ``self.model``. ``stub``
builds a bare mixin instance via ``__new__`` (bypassing ``__init__`` entirely —
no Tk, no network) and sets just the attributes a given helper reads.
"""


def stub(cls, **attrs):
    obj = cls.__new__(cls)
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj
