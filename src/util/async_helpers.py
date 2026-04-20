"""Async helper utilities."""
import inspect
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def maybe_await(fn: Callable[..., T | Any], *args) -> T | None:
    """Call a function and await the result if it's awaitable.

    Args:
        fn: Function to call
        *args: Arguments to pass to the function

    Returns:
        The result of the function call, awaited if necessary
    """
    if not fn:
        return None
    result = fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result
