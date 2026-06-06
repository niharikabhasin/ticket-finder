# 🎟 TicketHub — Distributed Event Ticketing System

A production-inspired microservices system that demonstrates distributed transaction handling, the **Saga pattern**, **Redis distributed locking**, and **AWS ECS deployment**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         API Gateway (Nginx)                         │
└────────────┬────────────┬─────────────────┬────────────────────────┘
             │            │                 │                 │
     event-service  ticket-service  payment-service  booking-orchestrator
        :8001          :8002           :8003              :8004
             │            │                                   │
             │         Redis                         Saga Pattern
             │     (seat_lock:e:s)                  (Reserve→Pay→Confirm)
             │            │
             └────────────┴── PostgreSQL (events_db, tickets_db)
```

### Services

| Service | Port | Responsibility |
|---------|------|---------------|
| `event-service` | 8001 | Event catalog, seat maps |
| `ticket-service` | 8002 | **Seat reservation + Redis distributed lock** |
| `payment-service` | 8003 | Payment processing (simulated, 90% success) |
| `booking-orchestrator` | 8004 | **Saga coordinator** (Reserve → Pay → Confirm) |

---

## The Double-Booking Problem

### The Problem
Two users click "Buy" at the exact same millisecond for seat #42:
1. Both read: `SELECT status FROM seats WHERE id=42` → both see `available`
2. Both write: `UPDATE seats SET status='sold'` → **both succeed** 💀

### Our Solution

**Layer 1 — Redis Distributed Lock (Primary Guard)**
```python
# Atomic: only ONE caller wins. All others return None immediately.
result = redis.set(
    f"seat_lock:{event_id}:{seat_id}",
    booking_id,
    nx=True,   # Only set if Not eXists — atomic!
    ex=30      # Auto-expire after 30s (prevents deadlocks)
)
```

**Layer 2 — PostgreSQL Pessimistic Lock (Secondary Guard)**
```sql
-- If Redis and DB are briefly out of sync,
-- this row-level lock prevents the race condition
SELECT * FROM bookings 
WHERE seat_id = 42 AND status IN ('pending', 'confirmed')
FOR UPDATE;  -- Blocks concurrent access to this row
```

### The Saga Pattern
```
Reserve Seat ─success─→ Process Payment ─success─→ Confirm Ticket ✅
     │                        │
     │ (seat taken)           │ (payment declined)
     ↓                        ↓
  Return 409              Release Seat ↩️  (compensating transaction)
```

---

## Quick Start (Local with Docker)

### Prerequisites
- Docker Desktop
- Docker Compose v2

### Run the full stack
```bash
cd ticketing-system

# Build and start all services
docker-compose up --build

# Wait for all health checks to pass (~60s on first run)
docker-compose ps
```

### Access points
| URL | What |
|-----|------|
| `http://localhost` | Frontend (event browser + seat map) |
| `http://localhost:8001/docs` | Event Service API docs |
| `http://localhost:8002/docs` | Ticket Service API docs |
| `http://localhost:8003/docs` | Payment Service API docs |
| `http://localhost:8004/docs` | Booking Orchestrator API docs |

---

## Manual Test: Double-Booking Prevention

```bash
# 1. Get events
curl http://localhost/api/events | python3 -m json.tool

# 2. Try buying the same seat from two terminals simultaneously
# Terminal 1:
curl -X POST http://localhost/api/bookings \
  -H 'Content-Type: application/json' \
  -d '{"event_id":1,"seat_id":1,"user_id":"alice","amount_cents":15000}'

# Terminal 2 (run at same time as Terminal 1):
curl -X POST http://localhost/api/bookings \
  -H 'Content-Type: application/json' \
  -d '{"event_id":1,"seat_id":1,"user_id":"bob","amount_cents":15000}'

# Expected: One succeeds (status=confirmed), one fails (status=failed or 409)
```

---

## Run Tests

```bash
# Install test dependencies
pip install -r tests/requirements-test.txt

# Unit tests (no services required — uses mocks)
pytest tests/unit/ -v

# Integration tests (requires docker-compose up first)
pytest tests/integration/test_concurrent_booking.py -v -s
```

### What the concurrency test does
```
50 requests → same seat (event 1, seat 1) → at the exact same time
Expected:  1 confirmed, 49 rejected with 409/failed
```

---

## AWS Deployment

### Prerequisites
- AWS CLI configured (`aws configure`)
- Terraform >= 1.5.0
- Docker

### Step 1: Build and push images to ECR
```bash
# After running terraform apply (to create ECR repos)
cd infrastructure/terraform
terraform init
terraform plan -var="db_password=YourSecurePassword!"
terraform apply

# Get ECR registry URL from outputs
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=us-east-1

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Build and push each service
for SERVICE in event-service ticket-service payment-service booking-orchestrator; do
  ECR_URL=$(terraform output -json ecr_repositories | python3 -c "import sys,json; print(json.load(sys.stdin)['$SERVICE'])")
  docker build -t $ECR_URL:latest services/$SERVICE/
  docker push $ECR_URL:latest
done
```

### Step 2: Deploy Step Functions (Saga)
```bash
aws stepfunctions create-state-machine \
  --name "ticketing-booking-saga" \
  --definition file://infrastructure/step-functions/booking_saga.json \
  --role-arn arn:aws:iam::YOUR_ACCOUNT:role/step-functions-role
```

### Infrastructure Created by Terraform
| Resource | Type | Purpose |
|----------|------|---------|
| VPC + Subnets | Network | Isolated network with public/private subnets |
| RDS PostgreSQL | db.t3.micro | Transactional data (events, bookings) |
| ElastiCache Redis | cache.t3.micro | Distributed seat locks |
| ECS Fargate Cluster | Compute | Serverless container hosting |
| ECR Repositories | Container Registry | Docker image storage |
| Application Load Balancer | Network | Path-based routing to services |
| Step Functions | Serverless | Saga orchestration in cloud |

---

## Project Structure

```
ticketing-system/
├── docker-compose.yml
├── nginx/nginx.conf                    # Local API gateway
├── frontend/index.html                 # SPA: event browser + seat map
├── services/
│   ├── event-service/
│   │   ├── app/main.py                 # FastAPI routes
│   │   ├── app/models.py               # Event, Seat models
│   │   ├── app/seed.py                 # 10 events × 500 seats seed data
│   │   └── Dockerfile
│   ├── ticket-service/
│   │   ├── app/redis_lock.py           # ⭐ SeatLockManager (Redlock pattern)
│   │   ├── app/main.py                 # Reserve/confirm/release endpoints
│   │   ├── app/models.py               # Booking, SeatLock models
│   │   └── Dockerfile
│   ├── payment-service/
│   │   ├── app/main.py                 # Charge + refund (90/10 simulation)
│   │   └── Dockerfile
│   └── booking-orchestrator/
│       ├── app/main.py                 # ⭐ Saga coordinator
│       └── Dockerfile
├── infrastructure/
│   ├── init-db.sql                     # Create events_db + tickets_db
│   ├── step-functions/booking_saga.json # AWS Step Functions state machine
│   └── terraform/
│       ├── main.tf                     # Provider
│       ├── variables.tf
│       ├── network.tf                  # VPC, subnets, security groups
│       ├── rds_elasticache.tf          # RDS + Redis
│       ├── ecs.tf                      # ECS Fargate + ECR + ALB
│       └── outputs.tf
└── tests/
    ├── unit/test_redis_lock.py         # Lock unit tests (mocked Redis)
    └── integration/test_concurrent_booking.py  # ⭐ 50 concurrent request test
```

---
