"""Bounded native work; tracking state and cache publication stay on the caller."""

from concurrent.futures import ThreadPoolExecutor


class ResourcePending(Exception):
    """A resource is still being decoded and cannot prove visual uniqueness."""


class LocalizationWorkers:
    def __init__(self, match_workers=2):
        self.resources = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VisualResource")
        self.matches = ThreadPoolExecutor(max_workers=match_workers, thread_name_prefix="VisualMapSearch")
        self.closed = False

    def close(self):
        self.closed = True
        # Running decoders own only their result, never a live cache or pose.
        self.resources.shutdown(wait=False, cancel_futures=True)
        self.matches.shutdown(wait=False, cancel_futures=True)
