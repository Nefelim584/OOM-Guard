"""Cryptographic Merkle tree ledger verification."""

import hashlib

class LedgerTree:
    """Binary Merkle tree implementation for transaction integrity verification."""

    def __init__(self, transactions: list[str]) -> None:
        self.transactions = transactions
        self.leaves = [self._hash(tx) for tx in transactions]

    def _hash(self, val: str) -> str:
        """Compute SHA-256 hex digest."""
        return hashlib.sha256(val.encode('utf-8')).hexdigest()

    def compute_root(self) -> str:
        """Compute Merkle tree root hash by pairwise hashing adjacent nodes."""
        if not self.leaves:
            return self._hash("")
        current_level = list(self.leaves)
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                combined_hash = self._hash(left + right)
                next_level.append(combined_hash)
            current_level = next_level
        return current_level[0]

    def verify_transaction(self, tx: str, root: str) -> bool:
        """Verify if a transaction is present in the tree."""
        tx_hash = self._hash(tx)
        return tx_hash in self.leaves
