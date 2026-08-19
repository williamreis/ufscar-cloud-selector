import re
from pydantic import BaseModel, field_validator, model_validator
from typing import Any, Dict, List, Optional, Tuple

import pairwise

# Validação de e-mail deliberadamente frouxa: exige a forma "algo@dominio.tld"
# sem tentar cobrir a RFC 5322. O objetivo é impedir registro de auditoria com
# identificação vazia ou obviamente inválida, não recusar endereços exóticos.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CRITERIA = ("sustainability", "performance", "security")

# Escala de relevância das perguntas fechadas (seções A, B e C) → nota 1-5.
RELEVANCE_MAP = {
    "Decisivo (critério indispensável)": 5,
    "Muito relevante": 4,
    "Relevante": 3,
    "Pouco relevante": 2,
    "Irrelevante": 1,
}

# Convenção de question_id → dimensão, usada nas perguntas de relevância.
CRITERION_ID_HINTS = (("sust", "sustainability"), ("perf", "performance"), ("sec", "security"))

# ---------------------------------------------------------------------------
# Comparações par a par (seção D).
#
# Cada resposta traz o par comparado e o julgamento em forma semântica
# (`PairwiseAnswer`): qual dimensão o gestor priorizou e com que intensidade
# verbal. A razão de Saaty é derivada disso no servidor, em `pairwise.py`.
# Com três dimensões bastam três julgamentos, que preenchem a matriz inteira por
# reciprocidade.
#
# Um par novo não exige mudança aqui: o processamento é genérico, pelo campo
# `pairwise` da resposta, e não pelo question_id.
#
# O mapa abaixo existe só para os **envios no formato antigo**, em que o bloco D
# era uma lista de nove frases prontas e o par vinha implícito no question_id.
# ---------------------------------------------------------------------------
LEGACY_PAIRWISE_QUESTIONS: Dict[str, Tuple[str, str]] = {
    "comp_sust_perf": ("sustainability", "performance"),
    "comp_sust_sec": ("sustainability", "security"),
    "comp_perf_sec": ("performance", "security"),
}


class PairwiseAnswer(BaseModel):
    """
    Julgamento par a par como o gestor o informou: o par comparado, a dimensão
    priorizada (ou `equal`) e a intensidade verbal.

    Não existe campo para a razão de Saaty nem para o valor da matriz — eles são
    calculados a partir daqui (ver `pairwise.resolve`), de modo que o cliente não
    tem como enviar um peso pronto. A validação roda na desserialização: uma
    comparação incompleta ("prefiro segurança" sem intensidade) ou incoerente
    ("igual importância" com intensidade) é recusada com 422 antes de chegar ao
    AHP.
    """

    left: str
    right: str
    preference: str  # id da dimensão priorizada ou "equal"
    intensity: Optional[str] = None  # moderate | strong | very_strong | extreme

    @model_validator(mode="after")
    def _coherent(self) -> "PairwiseAnswer":
        pairwise.validate(self.left, self.right, self.preference, self.intensity, CRITERIA)
        return self

    def resolved(self) -> Dict[str, Any]:
        """Resposta estruturada completa, com a intensidade de Saaty e a razão a_ij."""
        return pairwise.resolve(self.left, self.right, self.preference, self.intensity, CRITERIA)


def _clean_label(label: Optional[str]) -> Optional[str]:
    """Remove o markdown-lite (**) e a numeração do enunciado vindo do questions.json."""
    if not label:
        return None
    return re.sub(r"^\s*\d+\.\s*", "", label.replace("**", "")).strip()


class QuestionAnswer(BaseModel):
    question_id: str
    # Enunciado da pergunta. Enviado pelo frontend para que o LLM interprete as
    # respostas sabendo o que foi perguntado (antes ele via só o question_id).
    question_text: Optional[str] = None
    choice: Optional[str] = None
    text: Optional[str] = None
    # Preenchido só nas comparações do bloco D (type "pairwise" no questions.json).
    pairwise: Optional[PairwiseAnswer] = None

    def pairwise_pair(self) -> Optional[Tuple[str, str]]:
        """
        Par comparado por esta resposta, ou None se ela não for uma comparação.

        A forma nova traz o par na própria resposta; a antiga só tinha o
        question_id, e é o mapa de compatibilidade que o recupera.
        """
        if self.pairwise is not None:
            return (self.pairwise.left, self.pairwise.right)
        return LEGACY_PAIRWISE_QUESTIONS.get(self.question_id)

    def resolved_pairwise(self) -> Optional[Dict[str, Any]]:
        """Julgamento resolvido (intensidade de Saaty + razão a_ij), em qualquer dos formatos."""
        if self.pairwise is not None:
            return self.pairwise.resolved()
        pair = LEGACY_PAIRWISE_QUESTIONS.get(self.question_id)
        if pair is None or not self.choice:
            return None
        return pairwise.from_legacy_choice(self.choice, pair)

    def display_choice(self) -> Optional[str]:
        """
        Resposta em texto, para leitura humana: o LLM, a lista de respostas do
        relatório e o registro de auditoria continuam vendo uma frase, mesmo com
        a comparação armazenada em forma estruturada.
        """
        if self.choice:
            return self.choice
        if self.pairwise is not None:
            return pairwise.describe(
                self.pairwise.left,
                self.pairwise.right,
                self.pairwise.preference,
                self.pairwise.intensity,
            )
        return None


class QuestionnaireResponse(BaseModel):
    respondent: str  # e-mail do respondente
    respondent_role: str  # cargo/função na instituição
    answers: List[QuestionAnswer]
    session_id: Optional[str] = None  # sessão ativa: documentos de data/upload/<session_id> entram no RAG

    # Ambos são obrigatórios porque cada envio vira registro de auditoria, e um
    # registro sem identificação do respondente não sustenta auditoria nenhuma.
    @field_validator("respondent")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not EMAIL_RE.match(v):
            raise ValueError("Informe um e-mail válido.")
        return v

    @field_validator("respondent_role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Informe o cargo/função.")
        return v

    # -- Perguntas de relevância (A, B, C) -----------------------------------
    def answers_by_question(self) -> Dict[str, Optional[str]]:
        """
        Mapa `question_id` → alternativa escolhida.

        É a entrada do cálculo de pesos locais dos indicadores (`domain.weights`),
        que a partir da Fase 1 é o destino metodológico dos blocos A/B/C.
        """
        return {ans.question_id: ans.choice for ans in self.answers if ans.choice}

    def relevance_by_criterion(self) -> Dict[str, float]:
        """
        Média 1-5 por dimensão a partir das perguntas de relevância.
        Dimensão sem nenhuma resposta fica no ponto neutro (3.0).

        Atenção: esta média NÃO alimenta a matriz do AHP. A escala 1-5 mede a
        relevância individual de cada indicador dentro de uma dimensão; a escala
        de Saaty mede preferência *relativa* entre duas dimensões. Converter uma
        na outra misturaria dois conceitos distintos, então os pesos entre
        dimensões vêm exclusivamente das comparações par a par da seção D.
        O valor é reportado na memória de cálculo como perfil de prioridade dos
        indicadores e é enviado ao LLM como contexto da justificativa.
        """
        buckets: Dict[str, List[int]] = {c: [] for c in CRITERIA}
        for ans in self.answers:
            if ans.pairwise_pair() is not None:
                continue  # seção D não usa a escala de relevância
            score = RELEVANCE_MAP.get(ans.choice) if ans.choice else None
            if score is None:
                continue
            qid = ans.question_id.lower()
            for hint, criterion in CRITERION_ID_HINTS:
                if hint in qid:
                    buckets[criterion].append(score)
                    break
        return {k: (sum(v) / len(v) if v else 3.0) for k, v in buckets.items()}

    # -- Comparações par a par (D) -------------------------------------------
    def pairwise_judgments(self) -> Dict[str, Dict[str, Any]]:
        """
        Julgamentos da seção D, chaveados por "<critério A>|<critério B>".

        Cada entrada traz a razão a_ij derivada no servidor e o julgamento que a
        originou — a dimensão priorizada, a intensidade verbal e a frase legível
        equivalente —, para que a matriz do AHP possa ser auditada de volta até a
        resposta do gestor. Pares não respondidos ficam de fora; quem monta a
        matriz decide o que fazer com a lacuna.

        A varredura é genérica: qualquer resposta que traga um par comparado
        entra, sem lista de question_ids privilegiados.
        """
        judgments: Dict[str, Dict[str, Any]] = {}
        for ans in self.answers:
            resolved = ans.resolved_pairwise()
            if resolved is None:
                continue
            key = f"{resolved['left_dimension']}|{resolved['right_dimension']}"
            judgments[key] = {
                "question_id": ans.question_id,
                "ratio": resolved["matrix_value"],
                "choice": resolved["description"],
                "preference": resolved["preference"],
                "intensity": resolved["intensity"],
                "saaty_intensity": resolved["saaty_intensity"],
            }
        return judgments

    def unscored_answers(self) -> List[str]:
        """
        question_ids fechados que não entram no cálculo numérico dos pesos (mas
        que ainda são enviados ao LLM como contexto, via qa_for_llm).

        Serve de rede de proteção: se uma pergunta fechada nova aparecer nesta
        lista, é sinal de que ela não usa a escala de relevância nem foi
        reconhecida como comparação par a par, e portanto está sem efeito no
        modelo.
        """
        unscored = []
        for ans in self.answers:
            if ans.text:
                continue  # texto livre sempre vai ao LLM
            if ans.resolved_pairwise() is not None:
                continue  # comparação par a par válida
            if not ans.choice:
                continue  # não respondida (opcional)
            if ans.pairwise_pair() is not None:
                continue  # comparação par a par (formato antigo já contabilizado acima)
            if RELEVANCE_MAP.get(ans.choice) is not None:
                continue  # pergunta de relevância
            unscored.append(ans.question_id)
        return unscored

    def audit_payload(self) -> Dict[str, Any]:
        """
        Payload da requisição para o registro de auditoria.

        É o `model_dump()` com uma diferença: nas comparações do bloco D o campo
        `choice` sai preenchido com a frase legível equivalente à resposta
        estruturada. Assim a tabela `submission_answers` continua tendo, em uma
        coluna, o que foi respondido em cada pergunta — como tinha antes da
        mudança de formato — enquanto o `pairwise` original segue íntegro no
        `request_json` e em `ahp_judgments`.
        """
        payload = self.model_dump()
        for ans, raw in zip(self.answers, payload.get("answers", [])):
            if raw.get("choice") is None:
                raw["choice"] = ans.display_choice()
        return payload

    # -- Entrada do LLM ------------------------------------------------------
    def qa_for_llm(self) -> List[Dict[str, str]]:
        """
        Pares pergunta/resposta legíveis para o LLM, incluindo as fechadas.
        Antes só os textos livres eram enviados, e chaveados pelo question_id.
        """
        pairs = []
        for ans in self.answers:
            answer = ans.text or ans.display_choice()
            if not answer:
                continue
            pairs.append(
                {
                    "pergunta": _clean_label(ans.question_text) or ans.question_id,
                    "resposta": answer,
                }
            )
        return pairs

    def free_texts_dict(self) -> Dict[str, str]:
        """Somente as respostas dissertativas, chaveadas pelo enunciado."""
        return {
            (_clean_label(a.question_text) or a.question_id): a.text
            for a in self.answers
            if a.text
        }


class RecommendationResponse(BaseModel):
    ranking: List[Dict]
    criteria_weights: Dict[str, float]
    provider_scores: Optional[List[Dict]] = None  # matriz por provedor para dashboard
    notes: Optional[str] = None
    evidences: Dict[str, List[Dict]]
    # Memória de cálculo do AHP (matriz par a par, autovetor, razão de consistência)
    ahp: Optional[Dict[str, Any]] = None
    # Memória de cálculo da síntese: nota → normalizada → contribuição → score final
    synthesis: Optional[Dict[str, Any]] = None
    # Pesos dos indicadores nos três níveis (§7): coeficiente de relevância,
    # peso local, peso da dimensão e peso global.
    indicator_weights: Optional[Dict[str, Any]] = None
    # Respostas fora do cálculo numérico (ainda enviadas ao LLM como contexto)
    unscored_answers: Optional[List[str]] = None
    # Cobertura documental e procedência das notas dos provedores
    coverage: Optional[Dict[str, Any]] = None
    # Versões em vigor na execução (§28): questionário + hash, algoritmo, prompts,
    # provedor/modelo de LLM e de embedding.
    versions: Optional[Dict[str, Any]] = None
    # COMPLETED ou COMPLETED_WITH_LIMITATIONS (§26)
    status: Optional[str] = None
    # Limitações registradas (§30). Informam, não penalizam a pontuação.
    limitations: Optional[List[str]] = None
    # Guardrails disparados no processamento deste envio (§27)
    guardrail_events: Optional[List[Dict[str, Any]]] = None
    # Id do registro de auditoria (também é o trace_id). Nulo = a gravação falhou
    # e este envio não ficou registrado no banco.
    submission_id: Optional[str] = None
