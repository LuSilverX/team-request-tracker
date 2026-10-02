"""Shared abuse throttles; cache outages must not disable authenticated API work."""

import logging

from redis.exceptions import RedisError
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)


class CacheOutageMixin:
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except RedisError:
            logger.warning("Rate-limit cache unavailable; allowing request without throttling")
            return True


class AnonymousThrottle(CacheOutageMixin, AnonRateThrottle):
    pass


class UserThrottle(CacheOutageMixin, UserRateThrottle):
    pass


class AuthenticationThrottle(CacheOutageMixin, ScopedRateThrottle):
    pass
