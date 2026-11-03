"""Streaming data partitioner engine."""

import queue
import threading
from typing import Any

class StreamPartitioner:
    """Partitions data stream across worker queues with mutex synchronization."""

    def __init__(self, num_partitions: int = 4) -> None:
        self.num_partitions = num_partitions
        self.partitions: list[queue.Queue] = [queue.Queue() for _ in range(num_partitions)]
        self.lock = threading.Lock()
        self.total_processed = 0

    def hash_key(self, key: str) -> int:
        """Compute partition index for key."""
        return hash(key) % self.num_partitions

    def execute(self, items: list[tuple[str, Any]], timeout: float = 2.0) -> int:
        """Distribute items into worker partition queues under lock protection."""
        dispatched = 0
        with self.lock:
            for key, val in items:
                idx = self.hash_key(key)
                q = self.partitions[idx]
                try:
                    q.put((key, val), timeout=timeout)
                    dispatched += 1
                    self.total_processed += 1
                except queue.Full:
                    break
        return dispatched

    def drain(self, partition_idx: int) -> list[Any]:
        """Drain a specific partition queue."""
        results = []
        q = self.partitions[partition_idx]
        while not q.empty():
            results.append(q.get_nowait())
        return results
