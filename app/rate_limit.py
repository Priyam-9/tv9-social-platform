"""
Rate limiting, keyed by client IP. Closes the "no rate limiting" gap
identified in the security review — without this, repeated calls could
trigger excessive publish attempts or unnecessary platform API usage
(which costs real money on X specifically).

Limits are intentionally tighter on publish-now than on read endpoints,
since publishing is the highest-cost, highest-risk action in the system.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
