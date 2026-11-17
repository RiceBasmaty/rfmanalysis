"""A friendly, user-facing stop: shown in the UI as a red box with what happened and how to fix it."""
from __future__ import annotations


class RFMStop(Exception):
    def __init__(self, title: str, what: str = "", fix: str = ""):
        super().__init__(title)
        self.title = title
        self.what = what
        self.fix = fix

    def to_dict(self) -> dict:
        return {"title": self.title, "what": self.what, "fix": self.fix}


def stop(title: str, what: str = "", fix: str = "") -> None:
    raise RFMStop(title, what, fix)
