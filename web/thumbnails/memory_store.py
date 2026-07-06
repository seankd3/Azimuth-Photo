"""In-memory thumbnail LRU operations."""

from collections import OrderedDict
from collections.abc import Callable


class MemoryThumbnailStore:
    def __init__(self, tiers: tuple[str, ...]):
        # Global LRU order (also aliased externally); per-tier mirrors keep
        # tier-scoped eviction O(1) instead of scanning every key.
        self.cache: OrderedDict[tuple[str, int], tuple[str, bytes]] = OrderedDict()
        self.cache_bytes = 0
        self.tier_bytes = {size: 0 for size in tiers}
        self._tier_order: dict[str, OrderedDict[tuple[str, int], None]] = {
            size: OrderedDict() for size in tiers
        }

    def _tier_order_dict(self, size: str) -> OrderedDict:
        order = self._tier_order.get(size)
        if order is None:
            order = self._tier_order.setdefault(size, OrderedDict())
        return order

    def _touch(self, key: tuple[str, int]) -> None:
        self.cache.move_to_end(key)
        order = self._tier_order.get(key[0])
        if order is not None and key in order:
            order.move_to_end(key)

    def get_entry_fast(self, size: str, image_id: int) -> tuple[str, bytes] | None:
        key = (size, image_id)
        entry = self.cache.get(key)
        if entry is None:
            return None
        self._touch(key)
        return entry

    def get_fast(self, size: str, image_id: int) -> bytes | None:
        entry = self.get_entry_fast(size, image_id)
        return entry[1] if entry is not None else None

    def get(self, size: str, image_id: int, source_signature: str) -> bytes | None:
        key = (size, image_id)
        entry = self.cache.get(key)
        if entry is None:
            return None
        cached_signature, data = entry
        if cached_signature != source_signature:
            self.remove(key)
            return None
        self._touch(key)
        return data

    def remove(self, key: tuple[str, int]) -> bool:
        entry = self.cache.pop(key, None)
        if entry is None:
            return False
        size = key[0]
        order = self._tier_order.get(size)
        if order is not None:
            order.pop(key, None)
        data_len = len(entry[1])
        self.cache_bytes -= data_len
        self.tier_bytes[size] = max(0, self.tier_bytes.get(size, 0) - data_len)
        return True

    def evict_oldest(self, size: str | None = None) -> bool:
        if size is None:
            if not self.cache:
                return False
            return self.remove(next(iter(self.cache)))
        order = self._tier_order.get(size)
        if not order:
            return False
        return self.remove(next(iter(order)))

    def enforce_budget(
        self,
        tiers: tuple[str, ...],
        memory_cache_bytes: int,
        tier_budget: Callable[[str], int],
    ) -> None:
        for size in tiers:
            budget = tier_budget(size)
            while self.tier_bytes.get(size, 0) > budget:
                if not self.evict_oldest(size):
                    break

        while self.cache and self.cache_bytes > memory_cache_bytes:
            if not self.evict_oldest():
                break

    def put(
        self,
        size: str,
        image_id: int,
        source_signature: str,
        data: bytes,
        tiers: tuple[str, ...],
        memory_cache_bytes: int,
        tier_budget: Callable[[str], int],
    ) -> None:
        if not data or memory_cache_bytes <= 0:
            return
        tier_limit = tier_budget(size)
        if tier_limit <= 0 or len(data) > tier_limit:
            return

        key = (size, image_id)
        self.remove(key)
        self.cache[key] = (source_signature, data)
        self.cache.move_to_end(key)
        order = self._tier_order_dict(size)
        order[key] = None
        order.move_to_end(key)
        data_len = len(data)
        self.cache_bytes += data_len
        self.tier_bytes[size] = self.tier_bytes.get(size, 0) + data_len
        self.enforce_budget(tiers, memory_cache_bytes, tier_budget)

    def clear(self, tiers: tuple[str, ...]) -> dict:
        counts = {size: 0 for size in tiers}
        for size, _image_id in self.cache.keys():
            counts[size] = counts.get(size, 0) + 1
        entries_cleared = len(self.cache)
        bytes_cleared = self.cache_bytes
        self.cache.clear()
        for order in self._tier_order.values():
            order.clear()
        self.cache_bytes = 0
        for size in tiers:
            self.tier_bytes[size] = 0
        return {
            "entries_cleared": entries_cleared,
            "bytes_cleared": bytes_cleared,
            "counts": counts,
        }

    def clear_tiers(self, tiers: tuple[str, ...]) -> None:
        for size in tiers:
            order = self._tier_order.get(size)
            if not order:
                continue
            for key in list(order.keys()):
                self.remove(key)

    def clear_image_ids(self, image_ids: set[int]) -> None:
        if not image_ids:
            return
        for key in list(self.cache.keys()):
            if key[1] in image_ids:
                self.remove(key)

    def stats(
        self,
        tiers: tuple[str, ...],
        memory_cache_bytes: int,
        tier_budget: Callable[[str], int],
    ) -> dict:
        tier_stats = {size: {"count": 0, "bytes": 0} for size in tiers}
        for (size, _image_id), (_signature, data) in self.cache.items():
            tier_stats[size]["count"] += 1
            tier_stats[size]["bytes"] += len(data)
        for size in tiers:
            tier_stats[size]["budget_bytes"] = tier_budget(size)
        return {
            "limit_bytes": memory_cache_bytes,
            "used_bytes": self.cache_bytes,
            "tiers": tier_stats,
        }
