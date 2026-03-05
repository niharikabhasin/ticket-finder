import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .database import SessionLocal, engine, Base
from .models import Event, Seat, SeatStatus

EVENTS = [
    {"name": "Taylor Swift: Eras Tour", "venue": "Madison Square Garden", "city": "New York", "days": 5, "description": "The record-breaking Eras Tour live experience.", "image_url": "https://images.unsplash.com/photo-1540039155733-5bb30b4f731b?w=800"},
    {"name": "Coldplay: Music of the Spheres", "venue": "SoFi Stadium", "city": "Los Angeles", "days": 10, "description": "An otherworldly live music experience.", "image_url": "https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?w=800"},
    {"name": "Kendrick Lamar World Tour", "venue": "United Center", "city": "Chicago", "days": 15, "description": "Hip-hop's biggest artist live on stage.", "image_url": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=800"},
    {"name": "Beyoncé: Renaissance World Tour", "venue": "AT&T Stadium", "city": "Dallas", "days": 20, "description": "Renaissance live — an immersive visual spectacle.", "image_url": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=800"},
    {"name": "The Weeknd: After Hours Tour", "venue": "Barclays Center", "city": "Brooklyn", "days": 25, "description": "Cinematic dark pop at its finest.", "image_url": "https://images.unsplash.com/photo-1429962714451-bb934ecdc4ec?w=800"},
    {"name": "Dua Lipa: Future Nostalgia Tour", "venue": "Rogers Centre", "city": "Toronto", "days": 30, "description": "Disco-pop perfection.", "image_url": "https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?w=800"},
    {"name": "Harry Styles: Love On Tour", "venue": "TD Garden", "city": "Boston", "days": 35, "description": "Retro-pop theatrics with Harry Styles.", "image_url": "https://images.unsplash.com/photo-1467810563316-b5476525c0d9?w=800"},
    {"name": "Bad Bunny: El Último Tour del Mundo", "venue": "Kaseya Center", "city": "Miami", "days": 40, "description": "Reggaeton superstar Bad Bunny live.", "image_url": "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?w=800"},
    {"name": "Drake: It's All A Blur Tour", "venue": "Scotiabank Arena", "city": "Toronto", "days": 7, "description": "OVO Sound experience live.", "image_url": "https://images.unsplash.com/photo-1571266028243-d220c6ce4de2?w=800"},
    {"name": "Billie Eilish: Hit Me Hard and Soft Tour", "venue": "Chase Center", "city": "San Francisco", "days": 12, "description": "Billie Eilish: intimate and electrifying.", "image_url": "https://images.unsplash.com/photo-1524368535928-5b5e00ddc76b?w=800"},
]

SECTIONS = [
    ("Floor", 15000),
    ("Section A", 12000),
    ("Section B", 9500),
    ("Section C", 7500),
    ("Section D", 5000),
]

ROWS = "ABCDEFGHIJKLMNOPQRST"


def seed_database():
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        if db.query(Event).count() > 0:
            print("Database already seeded, skipping.")
            return

        base_date = datetime(2026, 6, 1, 20, 0, 0)

        for i, ev_data in enumerate(EVENTS):
            event_date = base_date + timedelta(days=ev_data["days"])
            event = Event(
                name=ev_data["name"],
                venue=ev_data["venue"],
                city=ev_data["city"],
                date=event_date,
                description=ev_data["description"],
                image_url=ev_data["image_url"],
                total_seats=0,
            )
            db.add(event)
            db.flush()

            seat_count = 0
            for section_name, price_cents in SECTIONS:
                num_rows = 4 if section_name == "Floor" else 3
                seats_per_row = 10 if section_name == "Floor" else 8
                for r_idx in range(num_rows):
                    row_letter = ROWS[r_idx]
                    for num in range(1, seats_per_row + 1):
                        seat = Seat(
                            event_id=event.id,
                            row=row_letter,
                            number=num,
                            section=section_name,
                            price=price_cents,
                            status=SeatStatus.available,
                        )
                        db.add(seat)
                        seat_count += 1

            event.total_seats = seat_count
            db.commit()
            print(f"  Seeded event: {event.name} with {seat_count} seats")

        print(f"\n✅ Seeded {len(EVENTS)} events successfully!")
    except Exception as e:
        db.rollback()
        print(f"❌ Seeding failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
