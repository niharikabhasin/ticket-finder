from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from enum import Enum


class SeatStatusEnum(str, Enum):
    available = "available"
    locked = "locked"
    sold = "sold"


class SeatBase(BaseModel):
    row: str
    number: int
    section: Optional[str]
    price: int
    status: SeatStatusEnum


class SeatResponse(SeatBase):
    id: int
    event_id: int

    class Config:
        from_attributes = True


class EventBase(BaseModel):
    name: str
    venue: str
    city: str
    date: datetime
    description: Optional[str]
    image_url: Optional[str]


class EventResponse(EventBase):
    id: int
    total_seats: int
    available_seats: Optional[int] = None

    class Config:
        from_attributes = True


class EventDetailResponse(EventResponse):
    seats: List[SeatResponse] = []
