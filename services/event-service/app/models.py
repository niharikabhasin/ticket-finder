from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import relationship
from .database import Base
import enum


class SeatStatus(str, enum.Enum):
    available = "available"
    locked = "locked"
    sold = "sold"


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    venue = Column(String(255), nullable=False)
    city = Column(String(100), nullable=False)
    date = Column(DateTime, nullable=False)
    description = Column(Text)
    image_url = Column(String(500))
    total_seats = Column(Integer, default=0)

    seats = relationship("Seat", back_populates="event")


class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    row = Column(String(5), nullable=False)
    number = Column(Integer, nullable=False)
    section = Column(String(50))
    price = Column(Integer, nullable=False)  # in cents
    status = Column(Enum(SeatStatus), default=SeatStatus.available, nullable=False)

    event = relationship("Event", back_populates="seats")
