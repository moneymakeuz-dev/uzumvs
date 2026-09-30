import asyncio
import selectors
import sys
from collections.abc import Coroutine
from typing import Any


def selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    loop_factory = selector_loop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(coroutine)
