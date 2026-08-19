.PHONY: build up ingest test all clean

build:
	docker-compose build

up:
	docker-compose up -d

ingest: up
	docker-compose exec backend python /app/scripts/ingest_rag.py

test:
	docker-compose exec backend python -m pytest /app/tests -q

all: up ingest

clean:
	docker-compose down -v --remove-orphans
