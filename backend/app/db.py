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

Mapeamento com as tabelas previstas na DIRETRIZ (seção 23): `submissions` cobre
QUESTIONNAIRES + ANALYSIS_SESSIONS + AHP_RESULTS, `submission_answers` cobre
QUESTIONNAIRE_RESPONSES e `ahp_judgments` cobre AHP_COMPARISONS. As tabelas de
RAG/documentos/guardrails não entram aqui — o índice FAISS continua sendo a
fonte desses dados.
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


def init_db() -> None:
    Base.metadata.create_all(engine)


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


def save_submission(request_payload: Dict[str, Any], response_payload: Dict[str, Any]) -> str:
    """
    Grava um envio completo e devolve o id (que também é o trace_id).

    Recebe os dois payloads já serializáveis para que o registro seja
    exatamente o que trafegou na API, sem reconstrução.
    """
    ahp = response_payload.get("ahp") or {}
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
        llm_provider=os.getenv("LLM_PROVIDER"),
        llm_model=_current_llm_model(),
        request_json=json.dumps(request_payload, ensure_ascii=False, default=_json_default),
        response_json=json.dumps(response_payload, ensure_ascii=False, default=_json_default),
    )

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


def _current_llm_model() -> Optional[str]:
    """Modelo em uso, conforme o provedor configurado — rastreabilidade da execução."""
    provider = (os.getenv("LLM_PROVIDER") or "openai").lower()
    return {
        "openai": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "groq": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        "openrouter": os.getenv("OPENROUTER_MODEL"),
        "gemini": os.getenv("GEMINI_MODEL"),
        "ollama": os.getenv("OLLAMA_MODEL"),
    }.get(provider)


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
