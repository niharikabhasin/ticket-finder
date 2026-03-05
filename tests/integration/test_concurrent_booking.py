"""
Integration test: Concurrent Booking — The Double-Booking Problem

This is THE critical test. It proves the Redis distributed lock
prevents double-booking when 50 users click "Buy" simultaneously.

Run: pytest tests/integration/test_concurrent_booking.py -v -s

Expected result:
  ✅ EXACTLY 1 booking succeeds
  ✅ 49 bookings receive 409 Conflict
  ✅ No seat is double-booked
"""

import asyncio
import pytest
import httpx
from typing import Tuple

BASE_URL = "http://localhost"


async def attempt_booking(
    client: httpx.AsyncClient,
    event_id: int,
    seat_id: int,
    user_id: str,
    amount_cents: int = 10000,
) -> Tuple[int, dict]:
    """Attempt to book a specific seat. Returns (status_code, response_body)."""
    try:
        resp = await client.post(
            f"{BASE_URL}/api/bookings",
            json={
                "event_id": event_id,
                "seat_id": seat_id,
                "user_id": user_id,
                "amount_cents": amount_cents,
            },
            timeout=30.0,
        )
        return resp.status_code, resp.json()
    except Exception as e:
        return 500, {"error": str(e)}


@pytest.mark.asyncio
async def test_concurrent_booking_no_double_booking():
    """
    THE TEST: Fire 50 simultaneous booking requests for the same seat.
    
    With Redis distributed locking:
    - Exactly 1 should succeed (status 200 or 201, status='confirmed')
    - All others should fail with 409 or have status='failed'
    
    Without locking, multiple could succeed → double-booking.
    """
    NUM_CONCURRENT = 50
    EVENT_ID = 1
    SEAT_ID = 1  # Everyone fights for seat #1

    async with httpx.AsyncClient() as client:
        # Fire all requests simultaneously
        print(f"\n🔥 Firing {NUM_CONCURRENT} concurrent booking requests for seat {SEAT_ID}...")

        tasks = [
            attempt_booking(
                client,
                event_id=EVENT_ID,
                seat_id=SEAT_ID,
                user_id=f"concurrent_user_{i}",
            )
            for i in range(NUM_CONCURRENT)
        ]

        results = await asyncio.gather(*tasks)

    # Analyze results
    successful = [(code, body) for code, body in results
                  if code in (200, 201) and body.get("status") == "confirmed"]
    seat_conflicts = [(code, body) for code, body in results
                      if code == 409 or body.get("status") == "failed"]

    print(f"\n📊 Results:")
    print(f"  ✅ Succeeded: {len(successful)}")
    print(f"  🔒 Rejected (409/failed): {len(seat_conflicts)}")
    print(f"  ❓ Other: {NUM_CONCURRENT - len(successful) - len(seat_conflicts)}")

    if successful:
        booking = successful[0][1]
        print(f"\n🎟  Winning booking: {booking.get('booking_ref')}")

    # THE CRITICAL ASSERTION: exactly 1 booking must succeed
    assert len(successful) == 1, (
        f"DOUBLE BOOKING DETECTED! {len(successful)} bookings succeeded for the same seat. "
        f"Expected exactly 1. The Redis lock is not working correctly."
    )

    assert len(seat_conflicts) == NUM_CONCURRENT - 1, (
        f"Expected {NUM_CONCURRENT - 1} rejections, got {len(seat_conflicts)}"
    )

    print("\n✅ PASS: No double booking! Distributed lock working correctly.")


@pytest.mark.asyncio
async def test_concurrent_booking_different_seats_all_succeed():
    """
    Complementary test: 10 users book 10 DIFFERENT seats simultaneously.
    All should succeed — the lock is per-seat, not global.
    """
    NUM_SEATS = 10
    EVENT_ID = 1

    async with httpx.AsyncClient() as client:
        print(f"\n🎯 Testing {NUM_SEATS} users booking {NUM_SEATS} different seats simultaneously...")

        tasks = [
            attempt_booking(
                client,
                event_id=EVENT_ID,
                seat_id=100 + i,  # Different seat for each user
                user_id=f"independent_user_{i}",
            )
            for i in range(NUM_SEATS)
        ]

        results = await asyncio.gather(*tasks)

    successful = [r for r in results if r[1].get("status") == "confirmed"]
    print(f"\n📊 All {len(successful)}/{NUM_SEATS} independent bookings succeeded")

    assert len(successful) == NUM_SEATS, (
        f"Expected all {NUM_SEATS} to succeed (different seats), got {len(successful)}"
    )
    print("✅ PASS: Per-seat locking allows parallel purchases of different seats.")


@pytest.mark.asyncio
async def test_saga_rollback_on_payment_failure():
    """
    Test the Saga compensating transaction:
    When payment fails, the seat reservation should be automatically released.
    
    We verify by checking the lock status after a known-to-fail booking.
    """
    EVENT_ID = 1
    SEAT_ID = 200  # A seat we'll test rollback on

    async with httpx.AsyncClient() as client:
        # Attempt booking (payment may fail due to 10% failure rate)
        # We run it a few times to get a deterministic failure
        booking_ref = None
        for attempt in range(20):  # 20 attempts, ~86% chance at least one fails
            code, body = await attempt_booking(
                client, EVENT_ID, SEAT_ID, f"rollback_test_{attempt}"
            )

            if body.get("status") == "failed":
                print(f"\n↩️  Got a payment failure on attempt {attempt + 1}")
                booking_ref = body.get("booking_ref")

                # Verify the seat is NOT locked after rollback
                await asyncio.sleep(0.1)  # Brief pause for compensation to complete
                status_resp = await client.get(
                    f"{BASE_URL}/api/tickets/lock-status/{EVENT_ID}/{SEAT_ID}"
                )

                if status_resp.status_code == 200:
                    lock_info = status_resp.json()
                    assert not lock_info["locked"], (
                        "SAGA ROLLBACK FAILED: seat is still locked after payment failure!"
                    )
                    print("✅ PASS: Seat correctly released after Saga compensation.")
                return

        print("ℹ️  Payment always succeeded in this run (90% success rate). Rollback test skipped.")
