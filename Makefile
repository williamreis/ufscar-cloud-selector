.PHONY: build up ingest all clean

build:
	docker-compose build

up:
	docker-compose up -d

ingest: up
	docker-compose exec cloud_backend python backend/scripts/ingest_rag.py

all: up ingest

clean:
	docker-compose down -v --remove-orphans
