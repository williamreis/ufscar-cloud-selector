"""
Persistência de auditoria (SQLite).

Cada envio do questionário grava uma linha em `submissions` mais as tabelas
filhas com as respostas, os julgamentos par a par e o ranking resultante. O
objetivo é auditoria: dado um resultado, reconstruir exatamente de quais
respostas ele saiu.

Duas camadas de fidelidade convivem de propósito:

  - **colunas normalizadas** (pesos, RC, provedor vencedor, ranking) — é o que o
    dashboard consulta e agrega, sem precisar abrir JSON;
  - **`request_json` / `response_json`** — o payload íntegro que entrou e saiu da
    API, para o caso de o modelo de dados evoluir e uma análise futura precisar
    de algo que hoje não tem coluna.

O arquivo fica em backend/data/, que já é volume persistente no docker-compose,
então o banco sobrevive a rebuild de imagem.

Mapeamento com as entidades previstas na diretriz (§32.1): `submissions` cobre
Evaluation + AhpResult, `submission_answers` cobre QuestionnaireResponse e
`ahp_judgments` cobre PairwiseJudgment. A partir da Fase 0 entram também
`documents` (§14.3), `rag_queries` + `retrieved_chunks`, `llm_runs` e
`guardrail_events` — os três blocos de auditoria que a §27 exige e que antes não
tinham onde ficar.

`DocumentChunk` da §32.1 **não** foi criada: o índice FAISS já guarda os chunks, e
os identificadores (`chunk_id`, `document_id`) são determinísticos, derivados do
conteúdo. Uma tabela espelho que ninguém escreve seria pior que a ausência dela.
As evidências da Fase 1 referenciam o `chunk_id` diretamente.

Migração: `init_db()` roda `create_all` e, em seguida, um passo aditivo que
acrescenta colunas novas a tabelas que já existiam. É o suficiente para SQLite e
preserva os envios já gravados; nenhuma coluna é removida ou reescrita.
"""

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# Caminho relativo ao processo (backend/app/): "../data" é o volume persistente.
DB_PATH = Path(os.getenv("AUDIT_DB_PATH", "../data/audit.db")).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# check_same_thread=False: o FastAPI atende em threadpool, e cada chamada abre e
# fecha a própria sessão — a conexão nunca é compartilhada entre threads.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """WAL permite ler enquanto se escreve; sem isso um relatório longo bloquearia o dashboard."""
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    """
    Serializa o que o json não conhece.

    Os scores de similaridade do FAISS chegam como escalares numpy; sem o
    `.item()` eles virariam string no registro e deixariam de ser comparáveis
    numericamente numa análise futura.
    """
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class Submission(Base):
    """Um envio do questionário e o resultado que ele produziu."""

    __tablename__ = "submissions"

    # Também serve de trace_id do processamento (DIRETRIZ, seção 22).
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    respondent_email: Mapped[str] = mapped_column(String(320), index=True)
    respondent_role: Mapped[Optional[str]] = mapped_column(String(200))
    session_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    # Pesos do AHP, desnormalizados para o dashboard agregar sem abrir o JSON
    weight_sustainability: Mapped[Optional[float]] = mapped_column(Float)
    weight_performance: Mapped[Optional[float]] = mapped_column(Float)
    weight_security: Mapped[Optional[float]] = mapped_column(Float)

    lambda_max: Mapped[Optional[float]] = mapped_column(Float)
    consistency_index: Mapped[Optional[float]] = mapped_column(Float)
    consistency_ratio: Mapped[Optional[float]] = mapped_column(Float, index=True)
    is_consistent: Mapped[Optional[bool]] = mapped_column(Boolean, index=True)

    # Relevância média 1-5 das perguntas fechadas (fora do cálculo dos pesos)
    relevance_sustainability: Mapped[Optional[float]] = mapped_column(Float)
    relevance_performance: Mapped[Optional[float]] = mapped_column(Float)
    relevance_security: Mapped[Optional[float]] = mapped_column(Float)

    top_provider_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    top_provider_name: Mapped[Optional[str]] = mapped_column(String(120))
    top_provider_score: Mapped[Optional[float]] = mapped_column(Float)

    llm_notes: Mapped[Optional[str]] = mapped_column(Text)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(40))
    llm_model: Mapped[Optional[str]] = mapped_column(String(120))
    embedding_provider: Mapped[Optional[str]] = mapped_column(String(40))
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120))

    # Versionamento (§28): sem isto, um resultado gravado deixa de ser
    # interpretável assim que o questionário ou a regra de pontuação mudar.
    questionnaire_version: Mapped[Optional[str]] = mapped_column(String(40))
    questions_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    algorithm_version: Mapped[Optional[str]] = mapped_column(String(40))

    # Estado da avaliação (§26). Envios gravados antes desta coluna ficam nulos —
    # e nulo aqui significa "gerado antes de existir máquina de estados", não
    # "estado desconhecido por falha".
    status: Mapped[Optional[str]] = mapped_column(String(40), index=True)

    request_json: Mapped[str] = mapped_column(Text)
    response_json: Mapped[str] = mapped_column(Text)

    answers: Mapped[List["SubmissionAnswer"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionAnswer.position"
    )
    judgments: Mapped[List["AhpJudgment"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    rankings: Mapped[List["SubmissionRanking"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionRanking.rank"
    )
    llm_runs: Mapped[List["LLMRun"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    guardrail_events: Mapped[List["GuardrailEventRecord"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    rag_queries: Mapped[List["RagQuery"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    indicator_weights: Mapped[List["IndicatorWeightRecord"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionAnswer(Base):
    """Resposta a uma pergunta, com o enunciado como estava no momento do envio."""

    __tablename__ = "submission_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    # Guardado junto porque o questions.json pode ser reescrito; sem isso uma
    # auditoria futura veria o question_id sem saber o que foi perguntado.
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    choice: Mapped[Optional[str]] = mapped_column(Text)
    text_answer: Mapped[Optional[str]] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="answers")


class AhpJudgment(Base):
    """Comparação par a par informada na seção D (AHP_COMPARISONS)."""

    __tablename__ = "ahp_judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[Optional[str]] = mapped_column(String(64))
    criterion_a: Mapped[str] = mapped_column(String(40))
    criterion_b: Mapped[str] = mapped_column(String(40))
    ratio: Mapped[float] = mapped_column(Float)
    choice: Mapped[Optional[str]] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="judgments")


class SubmissionRanking(Base):
    """Posição de um provedor no ranking daquele envio."""

    __tablename__ = "submission_rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(32), index=True)
    provider_name: Mapped[str] = mapped_column(String(120))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    # Contribuição de cada critério ao score (memória de cálculo da síntese)
    contributions_json: Mapped[Optional[str]] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="rankings")


class LLMRun(Base):
    """
    Uma chamada à LLM (§27, bloco "LLM").

    Guarda o hash da entrada e da saída, não o texto: o conteúdo íntegro já está
    em `response_json`, e duplicá-lo aqui multiplicaria o banco sem acrescentar
    rastreabilidade. O hash é o que permite provar que o registro corresponde ao
    que foi enviado e recebido.
    """

    __tablename__ = "llm_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    submission_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    prompt_id: Mapped[str] = mapped_column(String(80), index=True)
    prompt_version: Mapped[str] = mapped_column(String(20))
    provider: Mapped[Optional[str]] = mapped_column(String(40))
    model: Mapped[Optional[str]] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(40), index=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    attempts: Mapped[Optional[int]] = mapped_column(Integer)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer)

    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    output_hash: Mapped[Optional[str]] = mapped_column(String(64))
    error: Mapped[Optional[str]] = mapped_column(Text)

    submission: Mapped[Optional[Submission]] = relationship(back_populates="llm_runs")


class GuardrailEventRecord(Base):
    """
    Disparo de guardrail (§27, bloco "Guardrails").

    `masked_sample` é o único campo que carrega conteúdo, e ele já vem mascarado
    da camada de guardrail — a diretriz é explícita: conteúdo mascarado, nunca
    segredo puro.
    """

    __tablename__ = "guardrail_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    submission_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text)
    target: Mapped[Optional[str]] = mapped_column(String(200))
    masked_sample: Mapped[Optional[str]] = mapped_column(Text)

    submission: Mapped[Optional[Submission]] = relationship(back_populates="guardrail_events")


class RagQuery(Base):
    """Consulta de recuperação executada e os trechos que ela devolveu (§27, bloco "RAG")."""

    __tablename__ = "rag_queries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    submission_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # `indicator_id` fica nulo enquanto a consulta for por dimensão (ver
    # rag/queries.py); passa a ser preenchido quando a §16 entrar na Fase 1.
    dimension: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    indicator_id: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    query_text: Mapped[str] = mapped_column(Text)
    top_k: Mapped[Optional[int]] = mapped_column(Integer)
    result_count: Mapped[Optional[int]] = mapped_column(Integer)

    submission: Mapped[Optional[Submission]] = relationship(back_populates="rag_queries")
    chunks: Mapped[List["RetrievedChunk"]] = relationship(
        back_populates="query", cascade="all, delete-orphan", order_by="RetrievedChunk.position"
    )


class RetrievedChunk(Base):
    """Trecho devolvido por uma consulta, com o score de similaridade e a fonte."""

    __tablename__ = "retrieved_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rag_query_id: Mapped[str] = mapped_column(
        ForeignKey("rag_queries.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)

    # Nulos em trechos indexados antes da Fase 0, que não têm identidade própria.
    chunk_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(300))
    page: Mapped[Optional[int]] = mapped_column(Integer)
    score: Mapped[Optional[float]] = mapped_column(Float)

    query: Mapped[RagQuery] = relationship(back_populates="chunks")


class IndicatorWeightRecord(Base):
    """
    Os três níveis de peso de um indicador numa avaliação (§32.2).

    Persistidos lado a lado de propósito: a §7 é explícita em "nunca sobrescrever
    um nível com outro". Guardar só o peso global tornaria impossível responder
    *por que* ele é o que é — se veio de uma dimensão prioritária ou de um
    indicador dominante dentro dela.

    `local_weight` e `global_weight` nulos não significam zero: significam que o
    indicador não recebeu coeficiente válido (resposta "não sei" ou ausente), e a
    diretriz proíbe ler isso como irrelevância.
    """

    __tablename__ = "indicator_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    dimension_id: Mapped[str] = mapped_column(String(40), index=True)
    indicator_id: Mapped[str] = mapped_column(String(80), index=True)

    relevance_coefficient: Mapped[Optional[float]] = mapped_column(Float)
    relevance_state: Mapped[Optional[str]] = mapped_column(String(20))
    local_weight: Mapped[Optional[float]] = mapped_column(Float)
    dimension_weight: Mapped[Optional[float]] = mapped_column(Float)
    global_weight: Mapped[Optional[float]] = mapped_column(Float)

    # Peso após a renormalização sobre o conjunto comparável (§11.2). Fica nulo
    # enquanto não houver evidência que defina o conjunto V — o que só acontece
    # a partir da Fase 2.
    effective_weight: Mapped[Optional[float]] = mapped_column(Float)
    is_valid_for_comparison: Mapped[Optional[bool]] = mapped_column(Boolean)

    submission: Mapped[Submission] = relationship(back_populates="indicator_weights")


class Document(Base):
    """
    Documento ingerido no índice (§14.3).

    `document_id` é o hash do conteúdo, então reingerir o mesmo arquivo cai na
    mesma linha — o registro reflete documentos distintos, não uploads.
    """

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(300), index=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(80))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    provider_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer)
    document_hash: Mapped[Optional[str]] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    embedding_provider: Mapped[Optional[str]] = mapped_column(String(40))
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120))


# Colunas acrescentadas depois que a tabela já existia em instalações no ar.
# `create_all` cria tabelas novas, mas não altera tabelas existentes — daí o
# passo aditivo abaixo. Só ADD COLUMN: nada é removido nem reescrito.
_ADDITIVE_COLUMNS: Dict[str, Dict[str, str]] = {
    "submissions": {
        "embedding_provider": "VARCHAR(40)",
        "embedding_model": "VARCHAR(120)",
        "questionnaire_version": "VARCHAR(40)",
        "questions_hash": "VARCHAR(64)",
        "algorithm_version": "VARCHAR(40)",
        "status": "VARCHAR(40)",
    },
}


def _migrate_additive() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all já criou com o esquema completo
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name in present:
                    continue
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_additive()


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def save_submission(
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
    llm_runs: Optional[List[Dict[str, Any]]] = None,
    guardrail_events: Optional[List[Dict[str, Any]]] = None,
    rag_queries: Optional[List[Dict[str, Any]]] = None,
    status: Optional[str] = None,
    indicator_weights: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Grava um envio completo e devolve o id (que também é o trace_id).

    Recebe os dois payloads já serializáveis para que o registro seja
    exatamente o que trafegou na API, sem reconstrução. Os três blocos de
    auditoria da §27 (LLM, guardrails e RAG) chegam separados porque são
    coletados ao longo do processamento, não fazem parte da resposta da API.
    """
    ahp = response_payload.get("ahp") or {}
    versions = response_payload.get("versions") or {}
    weights = ahp.get("weights") or response_payload.get("criteria_weights") or {}
    relevance = ahp.get("relevance_by_criterion") or {}
    synthesis = response_payload.get("synthesis") or {}
    ranking = response_payload.get("ranking") or []
    top = ranking[0] if ranking else {}

    contributions_by_provider = {
        p["id"]: {c: cell.get("contribution") for c, cell in (p.get("cells") or {}).items()}
        for p in synthesis.get("providers", [])
    }

    submission = Submission(
        respondent_email=(request_payload.get("respondent") or "").strip(),
        respondent_role=(request_payload.get("respondent_role") or "").strip() or None,
        session_id=request_payload.get("session_id"),
        weight_sustainability=weights.get("sustainability"),
        weight_performance=weights.get("performance"),
        weight_security=weights.get("security"),
        lambda_max=ahp.get("lambda_max"),
        consistency_index=ahp.get("consistency_index"),
        consistency_ratio=ahp.get("consistency_ratio"),
        is_consistent=ahp.get("is_consistent"),
        relevance_sustainability=relevance.get("sustainability"),
        relevance_performance=relevance.get("performance"),
        relevance_security=relevance.get("security"),
        top_provider_id=top.get("id"),
        top_provider_name=top.get("name"),
        top_provider_score=top.get("score"),
        llm_notes=response_payload.get("notes"),
        llm_provider=versions.get("llm_provider") or _settings_value("llm_provider"),
        llm_model=versions.get("llm_model") or _settings_value("llm_model"),
        embedding_provider=versions.get("embedding_provider") or _settings_value("embedding_provider"),
        embedding_model=versions.get("embedding_model") or _settings_value("embedding_model"),
        questionnaire_version=versions.get("questionnaire_version"),
        questions_hash=versions.get("questions_hash"),
        algorithm_version=versions.get("algorithm_version"),
        status=status,
        request_json=json.dumps(request_payload, ensure_ascii=False, default=_json_default),
        response_json=json.dumps(response_payload, ensure_ascii=False, default=_json_default),
    )

    for run in llm_runs or []:
        submission.llm_runs.append(
            LLMRun(
                prompt_id=run.get("prompt_id", ""),
                prompt_version=str(run.get("prompt_version", "")),
                provider=run.get("provider"),
                model=run.get("model"),
                status=run.get("status", ""),
                latency_ms=run.get("latency_ms"),
                attempts=run.get("attempts"),
                input_tokens=run.get("input_tokens"),
                output_tokens=run.get("output_tokens"),
                total_tokens=run.get("total_tokens"),
                input_hash=run.get("input_hash"),
                output_hash=run.get("output_hash"),
                error=run.get("error"),
            )
        )

    for event_data in guardrail_events or []:
        submission.guardrail_events.append(
            GuardrailEventRecord(
                rule_id=event_data.get("rule_id", ""),
                stage=event_data.get("stage", ""),
                action=event_data.get("action", ""),
                reason=event_data.get("reason", ""),
                target=event_data.get("target"),
                masked_sample=event_data.get("masked_sample"),
            )
        )

    for weight in indicator_weights or []:
        submission.indicator_weights.append(
            IndicatorWeightRecord(
                dimension_id=weight.get("dimension", ""),
                indicator_id=weight.get("indicator_id", ""),
                relevance_coefficient=weight.get("relevance_coefficient"),
                relevance_state=weight.get("relevance_state"),
                local_weight=weight.get("local_weight"),
                dimension_weight=weight.get("dimension_weight"),
                global_weight=weight.get("global_weight"),
                effective_weight=weight.get("effective_weight"),
                is_valid_for_comparison=weight.get("is_valid_for_comparison"),
            )
        )

    for query in rag_queries or []:
        record = RagQuery(
            dimension=query.get("dimension"),
            indicator_id=query.get("indicator_id"),
            provider_id=query.get("provider_id"),
            query_text=query.get("query_text", ""),
            top_k=query.get("top_k"),
            result_count=len(query.get("chunks") or []),
        )
        for position, chunk in enumerate(query.get("chunks") or []):
            record.chunks.append(
                RetrievedChunk(
                    position=position,
                    chunk_id=chunk.get("chunk_id"),
                    document_id=chunk.get("document_id"),
                    source_name=chunk.get("file_name"),
                    page=chunk.get("page"),
                    score=chunk.get("score"),
                )
            )
        submission.rag_queries.append(record)

    for i, ans in enumerate(request_payload.get("answers") or []):
        submission.answers.append(
            SubmissionAnswer(
                position=i,
                question_id=ans.get("question_id", ""),
                question_text=ans.get("question_text"),
                choice=ans.get("choice"),
                text_answer=ans.get("text"),
            )
        )

    for key, j in (ahp.get("judgments") or {}).items():
        a, _, b = key.partition("|")
        submission.judgments.append(
            AhpJudgment(
                question_id=j.get("question_id"),
                criterion_a=a,
                criterion_b=b,
                ratio=float(j.get("ratio", 1.0)),
                choice=j.get("choice"),
            )
        )

    for row in ranking:
        submission.rankings.append(
            SubmissionRanking(
                provider_id=row.get("id", ""),
                provider_name=row.get("name", ""),
                rank=int(row.get("rank", 0)),
                score=float(row.get("score", 0.0)),
                contributions_json=json.dumps(
                    contributions_by_provider.get(row.get("id"), {}), ensure_ascii=False
                ),
            )
        )

    with session_scope() as session:
        session.add(submission)
        session.flush()
        return submission.id


def save_documents(details: List[Dict[str, Any]]) -> int:
    """
    Registra os documentos de uma ingestão (§14.3, "metadados persistidos").

    Idempotente por `document_id`: como o id é o hash do conteúdo, reingerir o
    mesmo arquivo atualiza a linha existente em vez de duplicá-la. Um arquivo
    editado gera outro hash e portanto outra linha — que é o comportamento certo,
    porque passa a ser outro documento.
    """
    if not details:
        return 0

    embedding_provider = _settings_value("embedding_provider")
    embedding_model = _settings_value("embedding_model")
    saved = 0

    with session_scope() as session:
        for detail in details:
            document_id = detail.get("document_id")
            if not document_id:
                continue
            existing = session.get(Document, document_id)
            if existing is None:
                existing = Document(document_id=document_id)
                session.add(existing)
            existing.source_name = detail.get("file_name") or ""
            existing.source_type = detail.get("source_type")
            existing.source_url = detail.get("source_url")
            existing.provider_id = detail.get("provider")
            existing.year = detail.get("year")
            existing.scope = detail.get("scope") or ""
            existing.session_id = detail.get("session_id")
            existing.chunk_count = detail.get("chunks")
            existing.document_hash = detail.get("document_hash")
            existing.ingested_at = _now()
            existing.embedding_provider = embedding_provider
            existing.embedding_model = embedding_model
            saved += 1

    return saved


def list_documents() -> List[Dict[str, Any]]:
    """Documentos ingeridos, para a área de gestão e para o painel de cobertura."""
    with session_scope() as session:
        rows = session.scalars(select(Document).order_by(Document.ingested_at.desc())).all()
        return [
            {
                "document_id": d.document_id,
                "source_name": d.source_name,
                "source_type": d.source_type,
                "provider_id": d.provider_id,
                "year": d.year,
                "scope": d.scope,
                "session_id": d.session_id,
                "chunk_count": d.chunk_count,
                "ingested_at": _iso(d.ingested_at),
                "embedding_provider": d.embedding_provider,
                "embedding_model": d.embedding_model,
            }
            for d in rows
        ]


def _settings_value(attribute: str) -> Optional[str]:
    """
    Valor de configuração como fallback do bloco de versões.

    A resolução de provedor/modelo mora em `config.settings` desde a Fase 0; aqui
    ela só é consultada quando a resposta não trouxe o bloco `versions` — caso de
    envio processado por um caminho que ainda não o produz.
    """
    try:
        from config import get_settings

        return getattr(get_settings(), attribute, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Leitura (dashboard e auditoria)
# ---------------------------------------------------------------------------


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    # Datas são gravadas em UTC; o sufixo evita que o cliente as leia como local.
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).isoformat()


def list_submissions(
    limit: int = 50, offset: int = 0, search: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], int]:
    """Página da lista de envios, mais o total para a paginação."""
    with session_scope() as session:
        stmt = select(Submission)
        count_stmt = select(func.count(Submission.id))
        if search:
            like = f"%{search.strip()}%"
            cond = Submission.respondent_email.ilike(like) | Submission.respondent_role.ilike(like)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        total = session.scalar(count_stmt) or 0
        rows = session.scalars(
            stmt.order_by(Submission.created_at.desc()).limit(limit).offset(offset)
        ).all()

        return [
            {
                "id": s.id,
                "created_at": _iso(s.created_at),
                "respondent_email": s.respondent_email,
                "respondent_role": s.respondent_role,
                "weights": {
                    "sustainability": s.weight_sustainability,
                    "performance": s.weight_performance,
                    "security": s.weight_security,
                },
                "consistency_ratio": s.consistency_ratio,
                "is_consistent": s.is_consistent,
                "top_provider_id": s.top_provider_id,
                "top_provider_name": s.top_provider_name,
                "top_provider_score": s.top_provider_score,
            }
            for s in rows
        ], total


def get_submission(submission_id: str) -> Optional[Dict[str, Any]]:
    """Registro completo de um envio, para a tela de auditoria."""
    with session_scope() as session:
        s = session.get(Submission, submission_id)
        if s is None:
            return None
        return {
            "id": s.id,
            "created_at": _iso(s.created_at),
            "respondent_email": s.respondent_email,
            "respondent_role": s.respondent_role,
            "session_id": s.session_id,
            "weights": {
                "sustainability": s.weight_sustainability,
                "performance": s.weight_performance,
                "security": s.weight_security,
            },
            "relevance": {
                "sustainability": s.relevance_sustainability,
                "performance": s.relevance_performance,
                "security": s.relevance_security,
            },
            "lambda_max": s.lambda_max,
            "consistency_index": s.consistency_index,
            "consistency_ratio": s.consistency_ratio,
            "is_consistent": s.is_consistent,
            "llm_notes": s.llm_notes,
            "llm_provider": s.llm_provider,
            "llm_model": s.llm_model,
            "status": s.status,
            # Versionamento (§28): identifica com que questionário, algoritmo e
            # modelos este resultado foi produzido.
            "versions": {
                "questionnaire_version": s.questionnaire_version,
                "questions_hash": s.questions_hash,
                "algorithm_version": s.algorithm_version,
                "llm_provider": s.llm_provider,
                "llm_model": s.llm_model,
                "embedding_provider": s.embedding_provider,
                "embedding_model": s.embedding_model,
            },
            "llm_runs": [
                {
                    "prompt_id": r.prompt_id,
                    "prompt_version": r.prompt_version,
                    "provider": r.provider,
                    "model": r.model,
                    "status": r.status,
                    "latency_ms": r.latency_ms,
                    "attempts": r.attempts,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "total_tokens": r.total_tokens,
                    "input_hash": r.input_hash,
                    "output_hash": r.output_hash,
                    "error": r.error,
                    "created_at": _iso(r.created_at),
                }
                for r in s.llm_runs
            ],
            "guardrail_events": [
                {
                    "rule_id": g.rule_id,
                    "stage": g.stage,
                    "action": g.action,
                    "reason": g.reason,
                    "target": g.target,
                    "masked_sample": g.masked_sample,
                    "created_at": _iso(g.created_at),
                }
                for g in s.guardrail_events
            ],
            "indicator_weights": [
                {
                    "dimension": w.dimension_id,
                    "indicator_id": w.indicator_id,
                    "relevance_coefficient": w.relevance_coefficient,
                    "relevance_state": w.relevance_state,
                    "local_weight": w.local_weight,
                    "dimension_weight": w.dimension_weight,
                    "global_weight": w.global_weight,
                    "effective_weight": w.effective_weight,
                    "is_valid_for_comparison": w.is_valid_for_comparison,
                }
                for w in s.indicator_weights
            ],
            "rag_queries": [
                {
                    "dimension": q.dimension,
                    "indicator_id": q.indicator_id,
                    "provider_id": q.provider_id,
                    "query_text": q.query_text,
                    "top_k": q.top_k,
                    "result_count": q.result_count,
                    "chunks": [
                        {
                            "position": c.position,
                            "chunk_id": c.chunk_id,
                            "document_id": c.document_id,
                            "source_name": c.source_name,
                            "page": c.page,
                            "score": c.score,
                        }
                        for c in q.chunks
                    ],
                }
                for q in s.rag_queries
            ],
            "answers": [
                {
                    "position": a.position,
                    "question_id": a.question_id,
                    "question_text": a.question_text,
                    "choice": a.choice,
                    "text": a.text_answer,
                }
                for a in s.answers
            ],
            "judgments": [
                {
                    "question_id": j.question_id,
                    "criterion_a": j.criterion_a,
                    "criterion_b": j.criterion_b,
                    "ratio": j.ratio,
                    "choice": j.choice,
                }
                for j in s.judgments
            ],
            "ranking": [
                {
                    "provider_id": r.provider_id,
                    "provider_name": r.provider_name,
                    "rank": r.rank,
                    "score": r.score,
                    "contributions": json.loads(r.contributions_json or "{}"),
                }
                for r in s.rankings
            ],
            # O submission_id é atribuído depois da serialização em /api/recommend,
            # então não está no JSON gravado; injetado aqui para que a tela de
            # auditoria mostre o trace_id igual ao relatório original.
            "response_json": {**json.loads(s.response_json or "{}"), "submission_id": s.id},
        }


def delete_submission(submission_id: str) -> bool:
    """
    Remove um envio e tudo que pende dele (respostas, julgamentos, ranking).

    A remoção em cascata vem do `cascade="all, delete-orphan"` nos relacionamentos
    somado ao `PRAGMA foreign_keys=ON`; sem os dois, as tabelas filhas ficariam
    órfãs apontando para um envio inexistente.
    """
    with session_scope() as session:
        s = session.get(Submission, submission_id)
        if s is None:
            return False
        session.delete(s)
        return True


def dashboard_stats() -> Dict[str, Any]:
    """Agregados da tela de gestão."""
    with session_scope() as session:
        total = session.scalar(select(func.count(Submission.id))) or 0
        if total == 0:
            return {
                "total": 0,
                "average_weights": {},
                "average_relevance": {},
                "consistency": {"consistent": 0, "inconsistent": 0, "average_ratio": None},
                "top_provider_counts": [],
                "submissions_by_day": [],
                "roles": [],
            }

        avg_weights = session.execute(
            select(
                func.avg(Submission.weight_sustainability),
                func.avg(Submission.weight_performance),
                func.avg(Submission.weight_security),
            )
        ).one()
        avg_relevance = session.execute(
            select(
                func.avg(Submission.relevance_sustainability),
                func.avg(Submission.relevance_performance),
                func.avg(Submission.relevance_security),
            )
        ).one()

        consistent = (
            session.scalar(
                select(func.count(Submission.id)).where(Submission.is_consistent.is_(True))
            )
            or 0
        )
        avg_cr = session.scalar(select(func.avg(Submission.consistency_ratio)))

        top_counts = session.execute(
            select(
                Submission.top_provider_id,
                Submission.top_provider_name,
                func.count(Submission.id),
            )
            .where(Submission.top_provider_id.is_not(None))
            .group_by(Submission.top_provider_id, Submission.top_provider_name)
            .order_by(func.count(Submission.id).desc())
        ).all()

        by_day = session.execute(
            select(func.date(Submission.created_at), func.count(Submission.id))
            .group_by(func.date(Submission.created_at))
            .order_by(func.date(Submission.created_at))
        ).all()

        roles = session.execute(
            select(Submission.respondent_role, func.count(Submission.id))
            .where(Submission.respondent_role.is_not(None))
            .group_by(Submission.respondent_role)
            .order_by(func.count(Submission.id).desc())
            .limit(10)
        ).all()

        return {
            "total": total,
            "average_weights": {
                "sustainability": avg_weights[0],
                "performance": avg_weights[1],
                "security": avg_weights[2],
            },
            "average_relevance": {
                "sustainability": avg_relevance[0],
                "performance": avg_relevance[1],
                "security": avg_relevance[2],
            },
            "consistency": {
                "consistent": consistent,
                "inconsistent": total - consistent,
                "average_ratio": avg_cr,
            },
            "top_provider_counts": [
                {"id": pid, "name": name, "count": count} for pid, name, count in top_counts
            ],
            "submissions_by_day": [{"day": day, "count": count} for day, count in by_day],
            "roles": [{"role": role, "count": count} for role, count in roles],
        }


def export_rows() -> List[Dict[str, Any]]:
    """Uma linha achatada por envio, para exportar em CSV."""
    with session_scope() as session:
        rows = session.scalars(select(Submission).order_by(Submission.created_at)).all()
        out = []
        for s in rows:
            record = {
                "id": s.id,
                "created_at": _iso(s.created_at),
                "respondent_email": s.respondent_email,
                "respondent_role": s.respondent_role or "",
                "peso_sustentabilidade": s.weight_sustainability,
                "peso_desempenho": s.weight_performance,
                "peso_seguranca": s.weight_security,
                "relevancia_sustentabilidade": s.relevance_sustainability,
                "relevancia_desempenho": s.relevance_performance,
                "relevancia_seguranca": s.relevance_security,
                "lambda_max": s.lambda_max,
                "razao_consistencia": s.consistency_ratio,
                "consistente": s.is_consistent,
                "provedor_1o": s.top_provider_name,
                "score_1o": s.top_provider_score,
                "llm_provider": s.llm_provider,
                "llm_model": s.llm_model,
            }
            for a in s.answers:
                record[a.question_id] = a.choice or a.text_answer or ""
            out.append(record)
        return out
