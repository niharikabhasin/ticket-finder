-- Initialize separate databases for each service
CREATE DATABASE events_db;
CREATE DATABASE tickets_db;

-- Grant access to ticketing user
GRANT ALL PRIVILEGES ON DATABASE events_db TO ticketing;
GRANT ALL PRIVILEGES ON DATABASE tickets_db TO ticketing;
