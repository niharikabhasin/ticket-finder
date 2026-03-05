"""
Payment Service — Simulates payment processing.

In a real system this would call Stripe/Braintree.
Here we simulate realistic behavior:
  - 90% of payments succeed
  - 10% fail (to test Saga rollback / compensating transactions)
  - Payments can be refunded (compensating transaction)
"""

import uuid
import random
import logging
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Payment Service", version="1.0.0")

# In-memory store for demo (replace with DB in production)
payments: dict = {}


class PaymentStatus(str, Enum):
    success = "success"
    failed = "failed"
    refunded = "refunded"


class ChargeRequest(BaseModel):
    booking_id: str
    user_id: str
    amount_cents: int
    currency: str = "USD"
    card_token: Optional[str] = "tok_test_visa"  # Simulated card token


class ChargeResponse(BaseModel):
    payment_id: str
    booking_id: str
    status: PaymentStatus
    amount_cents: int
    currency: str
    charged_at: datetime
    message: str


class RefundRequest(BaseModel):
    payment_id: str
    booking_id: str
    reason: str = "booking_cancelled"


class RefundResponse(BaseModel):
    refund_id: str
    payment_id: str
    booking_id: str
    status: str
    refunded_at: datetime


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment-service"}


@app.post("/payments/charge", response_model=ChargeResponse)
def charge_payment(request: ChargeRequest):
    """
    Simulate charging a payment card.
    
    90% success rate, 10% random failure (to test Saga compensation).
    In production: call Stripe API here.
    """
    payment_id = str(uuid.uuid4())

    # Simulate payment gateway latency
    import time
    time.sleep(0.1)

    # Simulate 90% success / 10% failure
    success = random.random() > 0.10

    if success:
        payment_record = {
            "payment_id": payment_id,
            "booking_id": request.booking_id,
            "status": PaymentStatus.success,
            "amount_cents": request.amount_cents,
            "currency": request.currency,
            "charged_at": datetime.utcnow(),
        }
        payments[payment_id] = payment_record
        logger.info(f"💳 Payment SUCCESS: payment={payment_id} booking={request.booking_id} amount=${request.amount_cents/100:.2f}")
        return ChargeResponse(
            **payment_record,
            message=f"Payment of ${request.amount_cents/100:.2f} {request.currency} charged successfully."
        )
    else:
        logger.warning(f"💳 Payment FAILED: booking={request.booking_id}")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "PAYMENT_DECLINED",
                "message": "Your card was declined. Please try a different payment method.",
                "booking_id": request.booking_id,
            }
        )


@app.post("/payments/refund", response_model=RefundResponse)
def refund_payment(request: RefundRequest):
    """
    Compensating transaction: Refund a previously charged payment.
    Called by the Saga orchestrator when downstream steps fail after payment.
    """
    payment = payments.get(request.payment_id)
    if not payment:
        # Payment not found — might not have been charged (idempotent)
        logger.warning(f"Refund requested for unknown payment {request.payment_id} — skipping")
        return RefundResponse(
            refund_id=str(uuid.uuid4()),
            payment_id=request.payment_id,
            booking_id=request.booking_id,
            status="not_found_skipped",
            refunded_at=datetime.utcnow()
        )

    payment["status"] = PaymentStatus.refunded
    refund_id = str(uuid.uuid4())

    logger.info(f"💰 Refund issued: refund={refund_id} payment={request.payment_id} reason={request.reason}")
    return RefundResponse(
        refund_id=refund_id,
        payment_id=request.payment_id,
        booking_id=request.booking_id,
        status="refunded",
        refunded_at=datetime.utcnow()
    )


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    payment = payments.get(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment
