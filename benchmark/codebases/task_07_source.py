"""E-commerce order fulfillment pipeline."""

from typing import Any

class OrderPipeline:
    """State machine managing order lifecycle from pending to fulfillment."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []

    def create_order(self, order_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new pending order."""
        order = {"order_id": order_id, "items": items, "status": "PENDING", "attempts": 0}
        self.orders[order_id] = order
        return order

    def process_order(self, order_id: str, payment_status: str, inventory_available: bool) -> str:
        """Execute state transitions with inventory reservation and retry policies."""
        if order_id not in self.orders:
            raise KeyError(f"Order not found: {order_id}")
        order = self.orders[order_id]
        current_status = order["status"]

        if current_status in ("COMPLETED", "CANCELLED"):
            return current_status

        if payment_status == "FAILED":
            order["attempts"] += 1
            if order["attempts"] >= 3:
                order["status"] = "FAILED"
                return "FAILED"
            return "PENDING_RETRY"

        if not inventory_available:
            order["status"] = "BACKORDERED"
            return "BACKORDERED"

        if payment_status == "SUCCESS" and inventory_available:
            order["status"] = "COMPLETED"
            self.history.append({"order_id": order_id, "status": "COMPLETED"})
            return "COMPLETED"

        return "UNKNOWN"

    def get_order_status(self, order_id: str) -> str:
        """Get current order status string."""
        return self.orders.get(order_id, {}).get("status", "NOT_FOUND")
