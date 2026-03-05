from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from .database import get_db, engine, Base
from .models import Event, Seat, SeatStatus
from .schemas import EventResponse, EventDetailResponse, SeatResponse

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Event Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "event-service"}


@app.get("/events", response_model=List[EventResponse])
def list_events(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
    events = db.query(Event).offset(skip).limit(limit).all()
    result = []
    for event in events:
        available = db.query(func.count(Seat.id)).filter(
            Seat.event_id == event.id,
            Seat.status == SeatStatus.available
        ).scalar()
        e = EventResponse.from_orm(event)
        e.available_seats = available
        result.append(e)
    return result


@app.get("/events/{event_id}", response_model=EventDetailResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    available = db.query(func.count(Seat.id)).filter(
        Seat.event_id == event_id,
        Seat.status == SeatStatus.available
    ).scalar()
    seats = db.query(Seat).filter(Seat.event_id == event_id).all()
    resp = EventDetailResponse.from_orm(event)
    resp.available_seats = available
    resp.seats = [SeatResponse.from_orm(s) for s in seats]
    return resp


@app.get("/events/{event_id}/seats", response_model=List[SeatResponse])
def get_event_seats(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    seats = db.query(Seat).filter(Seat.event_id == event_id).all()
    return [SeatResponse.from_orm(s) for s in seats]


@app.get("/seats/{seat_id}", response_model=SeatResponse)
def get_seat(seat_id: int, db: Session = Depends(get_db)):
    seat = db.query(Seat).filter(Seat.id == seat_id).first()
    if not seat:
        raise HTTPException(status_code=404, detail="Seat not found")
    return SeatResponse.from_orm(seat)
