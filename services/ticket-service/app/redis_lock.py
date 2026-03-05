"""
Redis Distributed Lock (Redlock pattern) for seat reservation.

The Double-Booking Problem:
  Two users click "Buy" for seat #42 at the exact same millisecond.
  Without locking, both reads see the seat as "available", both proceed,
  and both get confirmed — a double booking.

Our Solution (Defense in Depth):
  1. PRIMARY GUARD: Redis SETNX with 30s TTL
     - Atomic: only ONE caller can set the key
     - Fast: sub-millisecond operation
     - Auto-expiry: lock auto-releases if client crashes
  
  2. SECONDARY GUARD: PostgreSQL SELECT FOR UPDATE
     - Pessimistic row lock at the DB level
     - Prevents race between Redis lock release and DB update
"""

import os
import uuid
import redis
import logging
from typing import Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOCK_TTL_SECONDS = 30  # Seat held for 30 seconds during checkout

_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _seat_lock_key(event_id: int, seat_id: int) -> str:
    return f"seat_lock:{event_id}:{seat_id}"


class SeatLockManager:
    """
    Implements the Redlock pattern for distributed seat reservation.
    
    Usage:
        manager = SeatLockManager()
        token = manager.acquire(event_id=1, seat_id=42, booking_id="uuid")
        if token:
            # We have the lock — proceed with DB update
            ...
            manager.release(event_id=1, seat_id=42, token=token)
        else:
            raise 409 Conflict  # Seat already locked by someone else
    """

    def __init__(self):
        self.redis = get_redis()

    def acquire(self, event_id: int, seat_id: int, booking_id: str) -> Optional[str]:
        """
        Attempt to acquire a distributed lock for the seat.
        
        Uses Redis SET NX EX (atomic):
          - NX = Only set if Not eXists
          - EX = Set expiry in seconds
        
        Returns the lock token (booking_id) if acquired, None if already locked.
        """
        key = _seat_lock_key(event_id, seat_id)
        
        # This is atomic in Redis — no race condition possible
        result = self.redis.set(
            key,
            booking_id,
            nx=True,       # Only set if key does NOT exist
            ex=LOCK_TTL_SECONDS  # Auto-expire after 30s
        )
        
        if result:
            logger.info(f"🔒 Lock ACQUIRED: seat {seat_id} for event {event_id} | booking={booking_id}")
            return booking_id
        
        # Lock already held — get who holds it for debugging
        holder = self.redis.get(key)
        ttl = self.redis.ttl(key)
        logger.warning(
            f"🚫 Lock DENIED: seat {seat_id} for event {event_id} "
            f"| held by={holder} | ttl={ttl}s"
        )
        return None

    def release(self, event_id: int, seat_id: int, token: str) -> bool:
        """
        Release the lock ONLY if we are the owner (prevents releasing someone else's lock).
        Uses a Lua script for atomic check-and-delete.
        """
        key = _seat_lock_key(event_id, seat_id)
        
        # Lua script: atomically verify ownership then delete
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = self.redis.eval(release_script, 1, key, token)
        
        if result:
            logger.info(f"🔓 Lock RELEASED: seat {seat_id} for event {event_id}")
            return True
        else:
            logger.warning(f"⚠️  Lock release FAILED (not owner or expired): seat {seat_id}")
            return False

    def get_lock_info(self, event_id: int, seat_id: int) -> dict:
        """Get current lock status and TTL for a seat."""
        key = _seat_lock_key(event_id, seat_id)
        holder = self.redis.get(key)
        ttl = self.redis.ttl(key)
        return {
            "locked": holder is not None,
            "booking_id": holder,
            "ttl_seconds": max(ttl, 0) if ttl else 0,
        }

    def extend_lock(self, event_id: int, seat_id: int, token: str, additional_seconds: int = 30) -> bool:
        """Extend the lock TTL if we still own it (e.g., user is filling payment form)."""
        key = _seat_lock_key(event_id, seat_id)
        
        extend_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = self.redis.eval(extend_script, 1, key, token, str(additional_seconds))
        return bool(result)
