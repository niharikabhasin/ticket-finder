"""
Booking Orchestrator — Implements the Saga Pattern.

The Saga Pattern solves the distributed transaction problem:
  In a microservices world, you can't use a database ACID transaction
  across multiple services. Instead, you sequence local transactions
  with compensating (rollback) actions on failure.

Our Booking Saga:
  ┌─────────────────────────────────────────────────────────────────┐
  │ Step 1: Reserve Seat   (ticket-service)                        │
  │   → Success: proceed to Step 2                                  │
  │   → Failure: 409 Conflict (seat taken) → abort, no rollback needed │
  ├─────────────────────────────────────────────────────────────────┤
  │ Step 2: Process Payment (payment-service)                       │
  │   → Success: proceed to Step 3                                  │
  │   → Failure: COMPENSATE → Release Seat (ticket-service)        │
  ├─────────────────────────────────────────────────────────────────┤
  │ Step 3: Confirm Ticket  (ticket-service)                        │
  │   → Success: Booking complete ✅                                │
  │   → Failure: COMPENSATE → Refund Payment + Release Seat        │
  └─────────────────────────────────────────────────────────────────┘
"""

import os
import uuid
import logging
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Booking Orchestrator", version="1.0.0")

TICKET_SERVICE_URL = os.getenv("TICKET_SERVICE_URL", "http://ticket-service:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8003")
HTTP_TIMEOUT = 30.0


class BookingRequest(BaseModel):
    event_id: int
    seat_id: int
    user_id: str
    amount_cents: int
    card_token: Optional[str] = "tok_test_visa"


class BookingStatus(str, Enum):
    confirmed = "confirmed"
    failed = "failed"


class BookingResult(BaseModel):
    correlation_id: str
    status: BookingStatus
    booking_ref: Optional[str] = None
    payment_id: Optional[str] = None
    message: str
    saga_steps: list
    completed_at: datetime


class SagaStep(BaseModel):
    step: str
    status: str
    detail: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "booking-orchestrator"}


@app.post("/bookings", response_model=BookingResult)
async def create_booking(request: BookingRequest):
    """
    Execute the full booking Saga.
    
    Orchestrates: Reserve → Pay → Confirm
    With compensating transactions on any failure.
    """
    correlation_id = str(uuid.uuid4())
    saga_steps = []
    booking_id = None
    payment_id = None

    logger.info(f"🎬 Saga START: correlation={correlation_id} event={request.event_id} seat={request.seat_id}")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:

        # ══════════════════════════════════════════════════════════════
        # STEP 1: Reserve Seat (Ticket Service)
        # ══════════════════════════════════════════════════════════════
        logger.info(f"[{correlation_id}] Step 1: Reserving seat {request.seat_id}...")
        try:
            reserve_resp = await client.post(
                f"{TICKET_SERVICE_URL}/tickets/reserve",
                json={
                    "event_id": request.event_id,
                    "seat_id": request.seat_id,
                    "user_id": request.user_id,
                },
            )

            if reserve_resp.status_code == 409:
                error_detail = reserve_resp.json().get("detail", {})
                saga_steps.append({"step": "reserve_seat", "status": "conflict", "detail": str(error_detail)})
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "SEAT_UNAVAILABLE",
                        "message": error_detail.get("message", "Seat is not available"),
                        "correlation_id": correlation_id,
                    }
                )

            if reserve_resp.status_code not in (200, 201):
                raise Exception(f"Reserve failed with status {reserve_resp.status_code}")

            reserve_data = reserve_resp.json()
            booking_id = reserve_data["booking_id"]
            saga_steps.append({"step": "reserve_seat", "status": "success", "detail": f"booking_id={booking_id}"})
            logger.info(f"[{correlation_id}] Step 1 ✅ Reserved: booking={booking_id}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[{correlation_id}] Step 1 ❌ Failed: {e}")
            saga_steps.append({"step": "reserve_seat", "status": "error", "detail": str(e)})
            return BookingResult(
                correlation_id=correlation_id,
                status=BookingStatus.failed,
                message=f"Failed to reserve seat: {str(e)}",
                saga_steps=saga_steps,
                completed_at=datetime.utcnow(),
            )

        # ══════════════════════════════════════════════════════════════
        # STEP 2: Process Payment (Payment Service)
        # ══════════════════════════════════════════════════════════════
        logger.info(f"[{correlation_id}] Step 2: Processing payment ${request.amount_cents/100:.2f}...")
        try:
            pay_resp = await client.post(
                f"{PAYMENT_SERVICE_URL}/payments/charge",
                json={
                    "booking_id": booking_id,
                    "user_id": request.user_id,
                    "amount_cents": request.amount_cents,
                    "card_token": request.card_token,
                },
            )

            if pay_resp.status_code == 402:
                # Payment declined — COMPENSATE: release the seat reservation
                logger.warning(f"[{correlation_id}] Step 2 ❌ Payment declined — rolling back...")
                saga_steps.append({"step": "process_payment", "status": "declined"})

                await _compensate_release(client, request.event_id, request.seat_id, booking_id, correlation_id, saga_steps)

                return BookingResult(
                    correlation_id=correlation_id,
                    status=BookingStatus.failed,
                    message="Payment was declined. Seat reservation has been released.",
                    saga_steps=saga_steps,
                    completed_at=datetime.utcnow(),
                )

            pay_data = pay_resp.json()
            payment_id = pay_data["payment_id"]
            saga_steps.append({"step": "process_payment", "status": "success", "detail": f"payment_id={payment_id}"})
            logger.info(f"[{correlation_id}] Step 2 ✅ Payment captured: payment={payment_id}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[{correlation_id}] Step 2 ❌ Error: {e}")
            saga_steps.append({"step": "process_payment", "status": "error", "detail": str(e)})
            await _compensate_release(client, request.event_id, request.seat_id, booking_id, correlation_id, saga_steps)
            return BookingResult(
                correlation_id=correlation_id,
                status=BookingStatus.failed,
                message=f"Payment service error: {str(e)}. Seat released.",
                saga_steps=saga_steps,
                completed_at=datetime.utcnow(),
            )

        # ══════════════════════════════════════════════════════════════
        # STEP 3: Confirm Ticket (Ticket Service)
        # ══════════════════════════════════════════════════════════════
        logger.info(f"[{correlation_id}] Step 3: Confirming ticket...")
        try:
            confirm_resp = await client.post(
                f"{TICKET_SERVICE_URL}/tickets/confirm",
                json={
                    "booking_id": booking_id,
                    "event_id": request.event_id,
                    "seat_id": request.seat_id,
                },
            )

            if confirm_resp.status_code not in (200, 201):
                raise Exception(f"Confirm failed with status {confirm_resp.status_code}")

            saga_steps.append({"step": "confirm_ticket", "status": "success"})
            logger.info(f"[{correlation_id}] Step 3 ✅ Confirmed!")

        except Exception as e:
            logger.error(f"[{correlation_id}] Step 3 ❌ Confirm error — refunding + releasing: {e}")
            saga_steps.append({"step": "confirm_ticket", "status": "error", "detail": str(e)})

            # COMPENSATE: refund payment + release seat
            await _compensate_release(client, request.event_id, request.seat_id, booking_id, correlation_id, saga_steps)
            if payment_id:
                await _compensate_refund(client, payment_id, booking_id, correlation_id, saga_steps)

            return BookingResult(
                correlation_id=correlation_id,
                status=BookingStatus.failed,
                message="Ticket confirmation failed. Payment refunded and seat released.",
                saga_steps=saga_steps,
                completed_at=datetime.utcnow(),
            )

    # ══════════════════════════════════════════════════════════════════
    # SAGA COMPLETE ✅
    # ══════════════════════════════════════════════════════════════════
    logger.info(f"🎉 Saga COMPLETE: correlation={correlation_id} booking={booking_id}")
    return BookingResult(
        correlation_id=correlation_id,
        status=BookingStatus.confirmed,
        booking_ref=booking_id,
        payment_id=payment_id,
        message=f"🎟️ Booking confirmed! Your ticket is secured. Booking ref: {booking_id}",
        saga_steps=saga_steps,
        completed_at=datetime.utcnow(),
    )


# ──── Compensating Transaction Helpers ────────────────────────────────────────

async def _compensate_release(client, event_id, seat_id, booking_id, correlation_id, saga_steps):
    """Compensating transaction: release seat reservation."""
    try:
        await client.post(
            f"{TICKET_SERVICE_URL}/tickets/release",
            json={"booking_id": booking_id, "event_id": event_id, "seat_id": seat_id},
        )
        saga_steps.append({"step": "compensate_release_seat", "status": "success"})
        logger.info(f"[{correlation_id}] ↩️  Compensated: seat released")
    except Exception as e:
        saga_steps.append({"step": "compensate_release_seat", "status": "error", "detail": str(e)})
        logger.error(f"[{correlation_id}] ⚠️  Compensate release failed: {e}")


async def _compensate_refund(client, payment_id, booking_id, correlation_id, saga_steps):
    """Compensating transaction: refund payment."""
    try:
        await client.post(
            f"{PAYMENT_SERVICE_URL}/payments/refund",
            json={"payment_id": payment_id, "booking_id": booking_id, "reason": "saga_failure"},
        )
        saga_steps.append({"step": "compensate_refund_payment", "status": "success"})
        logger.info(f"[{correlation_id}] ↩️  Compensated: payment refunded")
    except Exception as e:
        saga_steps.append({"step": "compensate_refund_payment", "status": "error", "detail": str(e)})
        logger.error(f"[{correlation_id}] ⚠️  Compensate refund failed: {e}")
