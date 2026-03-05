"""
Unit tests for the ticket-service Redis distributed lock.

Tests the core double-booking prevention logic:
- Lock acquisition with NX+EX
- Lock ownership enforcement (Lua-based release)
- Concurrent lock contention
"""

import pytest
from unittest.mock import MagicMock, patch
from services.ticket_service.app.redis_lock import SeatLockManager


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def lock_manager(mock_redis):
    manager = SeatLockManager()
    manager.redis = mock_redis
    return manager


class TestAcquireLock:
    def test_acquire_succeeds_when_key_not_exists(self, lock_manager, mock_redis):
        """Redis SET NX returns True when key doesn't exist → lock acquired."""
        mock_redis.set.return_value = True

        result = lock_manager.acquire(event_id=1, seat_id=42, booking_id="booking-abc")

        assert result == "booking-abc"
        mock_redis.set.assert_called_once_with(
            "seat_lock:1:42", "booking-abc", nx=True, ex=30
        )

    def test_acquire_fails_when_key_exists(self, lock_manager, mock_redis):
        """Redis SET NX returns None when key already exists → lock denied."""
        mock_redis.set.return_value = None
        mock_redis.get.return_value = "other-booking-id"
        mock_redis.ttl.return_value = 22

        result = lock_manager.acquire(event_id=1, seat_id=42, booking_id="booking-abc")

        assert result is None

    def test_acquire_different_seats_are_independent(self, lock_manager, mock_redis):
        """Two different seats can be locked simultaneously."""
        mock_redis.set.return_value = True

        r1 = lock_manager.acquire(1, 10, "booking-1")
        r2 = lock_manager.acquire(1, 11, "booking-2")

        assert r1 == "booking-1"
        assert r2 == "booking-2"

    def test_lock_key_format(self, lock_manager, mock_redis):
        """Lock key must be seat_lock:{event_id}:{seat_id}."""
        mock_redis.set.return_value = True
        lock_manager.acquire(event_id=99, seat_id=7, booking_id="any")

        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "seat_lock:99:7"


class TestReleaseLock:
    def test_release_succeeds_if_owner(self, lock_manager, mock_redis):
        """Lua script returns 1 when caller owns the lock."""
        mock_redis.eval.return_value = 1

        result = lock_manager.release(event_id=1, seat_id=42, token="booking-abc")

        assert result is True
        mock_redis.eval.assert_called_once()

    def test_release_fails_if_not_owner(self, lock_manager, mock_redis):
        """Lua script returns 0 when caller does not own the lock (prevents accidental release)."""
        mock_redis.eval.return_value = 0

        result = lock_manager.release(event_id=1, seat_id=42, token="wrong-token")

        assert result is False

    def test_release_uses_atomic_lua_script(self, lock_manager, mock_redis):
        """Verify the release uses eval (Lua) not simple DEL for atomicity."""
        mock_redis.eval.return_value = 1
        lock_manager.release(1, 42, "tok")
        # Must use eval, not delete
        mock_redis.eval.assert_called_once()
        mock_redis.delete.assert_not_called()


class TestGetLockInfo:
    def test_returns_lock_info_when_locked(self, lock_manager, mock_redis):
        mock_redis.get.return_value = "booking-xyz"
        mock_redis.ttl.return_value = 15

        info = lock_manager.get_lock_info(event_id=1, seat_id=42)

        assert info["locked"] is True
        assert info["booking_id"] == "booking-xyz"
        assert info["ttl_seconds"] == 15

    def test_returns_unlocked_when_no_key(self, lock_manager, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.ttl.return_value = -2

        info = lock_manager.get_lock_info(event_id=1, seat_id=42)

        assert info["locked"] is False
        assert info["booking_id"] is None
        assert info["ttl_seconds"] == 0
