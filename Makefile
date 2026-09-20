.PHONY: build up ingest test quadros diagnostico determinismo all clean

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

# De onde vem — e de onde não vem — a diferença entre os provedores: quantos
# indicadores discriminam, o teto de separação do conjunto comparável e a
# saturação da rubrica. Roda sobre o que já está gravado, sem chamar a LLM.
# Use SUB=<id> para um envio específico, ou ARGS=--todas para o resumo.
diagnostico:
	docker-compose exec backend python /app/scripts/diagnostico_ranking.py $(if $(SUB),--submissao $(SUB),) $(ARGS)

# Reexecuta o mesmo envio N vezes e compara ranking e leitura de cada documento.
# Grava um envio por repetição no banco de auditoria — ver o cabeçalho do script.
determinismo:
	docker-compose exec backend python /app/scripts/checar_determinismo.py --api http://localhost:8000 $(if $(N),--repeticoes $(N),) $(ARGS)

all: up ingest

clean:
	docker-compose down -v --remove-orphans
