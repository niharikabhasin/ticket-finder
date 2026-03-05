from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime
import enum


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"


class SeatLock(Base):
    """Tracks active Redis-backed seat locks in the DB as a record."""
    __tablename__ = "seat_locks"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    seat_id = Column(Integer, nullable=False, index=True)
    booking_id = Column(String(36), nullable=False)  # UUID
    locked_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    booking_ref = Column(String(36), unique=True, nullable=False)  # UUID
    event_id = Column(Integer, nullable=False)
    seat_id = Column(Integer, nullable=False)
    user_id = Column(String(100), nullable=False)
    status = Column(Enum(BookingStatus), default=BookingStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime)
