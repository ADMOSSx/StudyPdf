"""A tiny stand-in for PySide6/PyMuPDF so the client's *logic* can be tested without a GUI or the packages.

`install()` does nothing when the real PySide6 is importable. The stub gives QObject/Signal/QTimer real behaviour
(signals connect and emit); every other Qt class is an inert placeholder that accepts any call.
"""
from __future__ import annotations

import importlib.util
import sys
import types


class _Meta(type):
    def __getattr__(cls, name):                 # Qt.AlignmentFlag.AlignCenter, QFont.StyleHint.Monospace, ...
        if name.startswith("__"):
            raise AttributeError(name)
        return _Anything()


class _Anything(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Anything()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Anything()

    def __or__(self, other):
        return self

    __ror__ = __and__ = __rand__ = __or__

    def _num(self, *a):
        return _Anything()

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = __floordiv__ = _num
    __neg__ = __pos__ = __abs__ = __mod__ = __pow__ = _num

    def _false(self, *a):
        return False

    __lt__ = __le__ = __gt__ = __ge__ = _false

    def __float__(self):
        return 0.0

    def __index__(self):
        return 0

    def __int__(self):
        return 0

    def __bool__(self):
        return False                 # keeps `while widget.count():` style loops finite

    def __iter__(self):
        return iter(())


class _Bound:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def disconnect(self, fn=None):
        self.slots = [s for s in self.slots if fn is not None and s is not fn]

    def emit(self, *a):
        for s in list(self.slots):
            s(*a)


class Signal:
    def __init__(self, *types_):
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = "_sig_" + name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        if self.name not in obj.__dict__:
            obj.__dict__[self.name] = _Bound()
        return obj.__dict__[self.name]


class QObject(_Anything):
    def __init__(self, *a, **k):
        pass


class QTimer(QObject):
    timeout = Signal()

    def __init__(self, *a, **k):
        self.active = False

    def start(self, *a):
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


class _Module(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        cls = type(name, (_Anything,), {})
        setattr(self, name, cls)
        return cls


IS_STUB = False


def install() -> None:
    global IS_STUB
    if importlib.util.find_spec("PySide6") is not None and "PySide6" not in sys.modules:
        return
    IS_STUB = True
    for name in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtWebSockets",
                 "PySide6.QtNetwork", "PySide6.QtSvg", "pymupdf", "fitz"):
        sys.modules[name] = _Module(name)
    core = sys.modules["PySide6.QtCore"]
    core.QObject, core.Signal, core.QTimer = QObject, Signal, QTimer
