"""Pub-sub event bus module."""

from typing import Callable, Any

class EventEmitter:
    """Synchronous event emitter broker."""

    def __init__(self) -> None:
        """Initialize listeners mapping."""
        self._listeners: dict[str, list[Callable]] = {}

    def on(self, event: str, callback: Callable) -> None:
        """Register an event listener."""
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def dispatch(self, event: str, payload: dict[str, Any]) -> int:
        """Emit event with payload dictionary to registered callbacks."""
        callbacks = self._listeners.get(event, [])
        for cb in callbacks:
            cb(payload)
        return len(callbacks)

def trigger_signup_event(bus: EventEmitter, user_id: int) -> None:
    """Helper to dispatch signup event."""
    bus.dispatch("user.signup", {"user_id": user_id})

def trigger_payment_event(bus: EventEmitter, order_id: str, amount: float) -> None:
    """Helper to dispatch payment event."""
    bus.dispatch("order.paid", {"order_id": order_id, "amount": amount})
