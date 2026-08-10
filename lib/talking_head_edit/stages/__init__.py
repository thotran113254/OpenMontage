"""Pipeline stages. Each module exposes `run(job, options) -> dict`."""

from lib.talking_head_edit.stages import (  # noqa: F401
    audit, calibrate, direct, probe, render, resolve, revise, select, transcribe,
    verify,
)

__all__ = ["probe", "transcribe", "select", "direct", "audit", "calibrate",
           "resolve", "render", "verify", "revise"]
