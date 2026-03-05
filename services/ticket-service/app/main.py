"""
Ticket Service — FastAPI application.

The critical flow for seat reservation:
1. Client calls POST /tickets/reserve
2. We attempt to acquire a Redis distributed lock (SETNX with 30s TTL)
3. If lock acquired → write a pending booking to PostgreSQL
4. If lock denied → return 409 Conflict (seat is being purchased by someone else)
5. Booking Orchestrator then calls /confirm or /release based on payment outcome
"""

import uuid
import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from .database import get_db, engine, Base
from .models import Booking, SeatLock, BookingStatus
from .schemas import (
    ReserveRequest, ReserveResponse,
    ConfirmRequest, ReleaseRequest,
    BookingResponse, LockStatusResponse
)
from .redis_lock import SeatLockManager, LOCK_TTL_SECONDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ticket Service", version="1.0.0")
lock_manager = SeatLockManager()


@app.get("/health")
def health():
    return {"status": "ok", "service": "ticket-service"}


@app.post("/tickets/reserve", response_model=ReserveResponse, status_code=201)
def reserve_seat(request: ReserveRequest, db: Session = Depends(get_db)):
    """
    Step 1 of the Saga: Reserve a seat with a distributed lock.
    
    Defense-in-depth:
    - Redis distributed lock (primary): prevents concurrent reservations
    - PostgreSQL check (secondary): ensures DB consistency
    """
    booking_id = str(uuid.uuid4())

    # ── PRIMARY GUARD: Redis distributed lock ──────────────────────────────
    token = lock_manager.acquire(
        event_id=request.event_id,
        seat_id=request.seat_id,
        booking_id=booking_id,
    )

    if not token:
        lock_info = lock_manager.get_lock_info(request.event_id, request.seat_id)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "SEAT_LOCKED",
                "message": f"Seat {request.seat_id} is currently being purchased by another user.",
                "retry_after_seconds": lock_info["ttl_seconds"],
            }
        )

    # ── SECONDARY GUARD: Check DB state ────────────────────────────────────
    # Prevents the edge case where Redis and DB are briefly out of sync
    existing = db.execute(
        select(Booking).where(
            Booking.event_id == request.event_id,
            Booking.seat_id == request.seat_id,
            Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed])
        ).with_for_update()  # Pessimistic row lock
    ).scalars().first()

    if existing:
        # DB already has this seat — release our Redis lock and reject
        lock_manager.release(request.event_id, request.seat_id, token)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "SEAT_ALREADY_BOOKED",
                "message": f"Seat {request.seat_id} for event {request.event_id} is already booked.",
            }
        )

    # ── Create pending booking ──────────────────────────────────────────────
    expires_at = datetime.utcnow() + timedelta(seconds=LOCK_TTL_SECONDS)

    booking = Booking(
        booking_ref=booking_id,
        event_id=request.event_id,
        seat_id=request.seat_id,
        user_id=request.user_id,
        status=BookingStatus.pending,
    )
    seat_lock_record = SeatLock(
        event_id=request.event_id,
        seat_id=request.seat_id,
        booking_id=booking_id,
        expires_at=expires_at,
    )
    db.add(booking)
    db.add(seat_lock_record)
    db.commit()

    logger.info(f"✅ Reserved: booking={booking_id} seat={request.seat_id} user={request.user_id}")

    return ReserveResponse(
        booking_id=booking_id,
        event_id=request.event_id,
        seat_id=request.seat_id,
        user_id=request.user_id,
        lock_ttl_seconds=LOCK_TTL_SECONDS,
        message="Seat reserved. Complete your purchase within 30 seconds.",
    )


@app.post("/tickets/confirm", response_model=BookingResponse)
def confirm_ticket(request: ConfirmRequest, db: Session = Depends(get_db)):
    """
    Step 3 of the Saga: Mark the booking as confirmed after successful payment.
    Releases the Redis lock (seat is now permanently sold).
    """
    booking = db.query(Booking).filter(
        Booking.booking_ref == request.booking_id,
        Booking.event_id == request.event_id,
        Booking.seat_id == request.seat_id,
        Booking.status == BookingStatus.pending,
    ).first()

    if not booking:
        raise HTTPException(
            status_code=404,
            detail=f"Pending booking {request.booking_id} not found. It may have expired or already been confirmed."
        )

    booking.status = BookingStatus.confirmed
    booking.confirmed_at = datetime.utcnow()

    # Clean up lock record
    db.query(SeatLock).filter(SeatLock.booking_id == request.booking_id).delete()
    db.commit()

    # Release Redis lock (seat is sold — no longer needs a lock, DB is source of truth)
    lock_manager.release(request.event_id, request.seat_id, request.booking_id)

    logger.info(f"🎟️  Confirmed: booking={request.booking_id}")
    return BookingResponse.from_orm(booking)


@app.post("/tickets/release")
def release_seat(request: ReleaseRequest, db: Session = Depends(get_db)):
    """
    Compensating transaction: Release the seat lock and cancel the booking.
    Called by the Saga orchestrator when payment fails.
    """
    booking = db.query(Booking).filter(
        Booking.booking_ref == request.booking_id
    ).first()

    if booking:
        booking.status = BookingStatus.cancelled
        db.query(SeatLock).filter(SeatLock.booking_id == request.booking_id).delete()
        db.commit()

    released = lock_manager.release(request.event_id, request.seat_id, request.booking_id)

    logger.info(f"🔓 Released: booking={request.booking_id} redis_released={released}")
    return {"status": "released", "booking_id": request.booking_id}


@app.get("/tickets/lock-status/{event_id}/{seat_id}", response_model=LockStatusResponse)
def get_lock_status(event_id: int, seat_id: int):
    """Check if a seat is currently locked (being purchased) and how long until it expires."""
    info = lock_manager.get_lock_info(event_id, seat_id)
    return LockStatusResponse(
        event_id=event_id,
        seat_id=seat_id,
        **info
    )


@app.get("/bookings/{booking_ref}", response_model=BookingResponse)
def get_booking(booking_ref: str, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.booking_ref == booking_ref).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingResponse.from_orm(booking)
