.PHONY: build up ingest test quadros all clean

build:
	docker-compose build

up:
	docker-compose up -d

ingest: up
	docker-compose exec backend python /app/scripts/ingest_rag.py

test:
	docker-compose exec backend python -m pytest /app/tests -q

# Quadros 22 e 24 gerados a partir de methodology/*.json — a fonte que o
# cálculo lê, para que texto e produto não voltem a divergir.
quadros:
	docker-compose exec backend python /app/scripts/generate_quadros.py

all: up ingest

clean:
	docker-compose down -v --remove-orphans
