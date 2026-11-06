"""Transform types offered for an axis, and a sensible starting transform of each type."""

from __future__ import annotations

import math

from ..core import Arcsinh, Biex, Linear, Log, Logicle, Parameter, Transform

KINDS = {"Linear": Linear, "Log": Log, "Biex": Biex, "Logicle": Logicle, "ArcSinh": Arcsinh}
NAMES = {cls: name for name, cls in KINDS.items()}


def starter_transform(name: str, param: Parameter, current: Transform) -> Transform:
    """A sensible transform of the given type for this parameter."""
    top = param.channel_range or 262144.0
    if name == "Linear":
        return Linear(0.0, top) if param.kind != "time" else current if isinstance(current, Linear) else Linear(0.0, top)
    if name == "Log":
        return Log(1.0, round(math.log10(top), 4))
    if name == "Biex":
        return Biex(max_range=top)
    if name == "Logicle":
        return Logicle(T=top, W=1.0, M=4.418539922, A=0.0)
    return Arcsinh(T=top, M=4.0, A=0.0, max_range=top)
