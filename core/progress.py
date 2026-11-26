
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProgressEvent:

    percent: int

    stage: str

    message: str


ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(
    callback,
    percent,
    stage,
    message
):

    if callback is not None:

        callback(
            ProgressEvent(
                percent=percent,
                stage=stage,
                message=message
            )
        )
