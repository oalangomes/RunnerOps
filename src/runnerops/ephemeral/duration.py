#!/usr/bin/env python3
"""Finite positive duration validation for the ephemeral lifecycle."""

import math
from typing import Any


def finite_positive_duration(value: Any, name: str) -> float:
    """Return a duration only when it is finite and strictly positive."""
    if isinstance(value, bool):
        raise ValueError("{} must be a finite positive duration".format(name))
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("{} must be a finite positive duration".format(name)) from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("{} must be a finite positive duration".format(name))
    return parsed
