from pydantic import BaseModel
from typing import Optional
from enum import Enum
from datetime import datetime


class BookingStatusEnum(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"


class ReserveRequest(BaseModel):
    event_id: int
    seat_id: int
    user_id: str


class ReserveResponse(BaseModel):
    booking_id: str
    event_id: int
    seat_id: int
    user_id: str
    lock_ttl_seconds: int
    message: str


class ConfirmRequest(BaseModel):
    booking_id: str
    event_id: int
    seat_id: int


class ReleaseRequest(BaseModel):
    booking_id: str
    event_id: int
    seat_id: int


class BookingResponse(BaseModel):
    booking_ref: str
    event_id: int
    seat_id: int
    user_id: str
    status: BookingStatusEnum
    created_at: datetime
    confirmed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LockStatusResponse(BaseModel):
    event_id: int
    seat_id: int
    locked: bool
    booking_id: Optional[str]
    ttl_seconds: int
