import re
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

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
# Perguntas comparativas (seções D e E).
#
# Elas não usam a escala de relevância: cada alternativa aponta para uma dimensão.
# `effect` é quanto a escolha desloca a intensidade daquela dimensão (escala 1-5):
# positivo quando a pergunta indica prioridade, negativo quando indica renúncia.
#
# Para acrescentar uma nova pergunta comparativa no questions.json, registre o
# question_id aqui — caso contrário ela é apenas coletada e reportada em
# `ignored_answers`, sem afetar o cálculo.
# ---------------------------------------------------------------------------
COMPARATIVE_EFFECTS: Dict[str, float] = {
    "comp_p1": +0.5,   # "tende a valorizar mais"
    "comp_p2": -0.5,   # "qual teria menor impacto se renunciasse"
    "comp_p3": +0.5,   # "recebe maior atenção dos gestores"
    "global_p2": +0.25,  # "fator que mais influenciaria uma mudança" (sinal mais fraco)
}

# Alternativas das perguntas comparativas → dimensão. None = não mapeia para
# nenhum critério do modelo (ex.: custo, que não é uma das três dimensões).
COMPARATIVE_OPTION_CRITERIA: Dict[str, Optional[str]] = {
    "sustentabilidade ambiental": "sustainability",
    "desempenho e estabilidade": "performance",
    "segurança da informação": "security",
    "sustentabilidade": "sustainability",
    "desempenho": "performance",
    "segurança": "security",
    "sustentabilidade e responsabilidade ambiental": "sustainability",
    "desempenho e escalabilidade": "performance",
    "segurança e conformidade": "security",
    "custo total de propriedade": None,
}


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


class QuestionnaireResponse(BaseModel):
    respondent: str
    answers: List[QuestionAnswer]
    session_id: Optional[str] = None  # sessão ativa: documentos de data/upload/<session_id> entram no RAG

    # -- Perguntas de relevância (A, B, C) -----------------------------------
    def to_numeric_scores(self) -> Dict[str, float]:
        """
        Média 1-5 por dimensão a partir das perguntas de relevância.
        Dimensão sem nenhuma resposta fica no ponto neutro (3.0).
        """
        buckets: Dict[str, List[int]] = {c: [] for c in CRITERIA}
        for ans in self.answers:
            score = RELEVANCE_MAP.get(ans.choice) if ans.choice else None
            if score is None:
                continue
            qid = ans.question_id.lower()
            for hint, criterion in CRITERION_ID_HINTS:
                if hint in qid:
                    buckets[criterion].append(score)
        return {k: (sum(v) / len(v) if v else 3.0) for k, v in buckets.items()}

    # -- Perguntas comparativas (D, E) ---------------------------------------
    def comparative_adjustments(self) -> Dict[str, float]:
        """
        Deslocamento de intensidade por dimensão vindo das perguntas comparativas.
        É o que antes era descartado: as escolhas dessas perguntas não estão na
        escala de relevância, então caíam fora de to_numeric_scores() e, por não
        terem texto livre, também não chegavam ao LLM.
        """
        adjustments: Dict[str, float] = {c: 0.0 for c in CRITERIA}
        for ans in self.answers:
            effect = COMPARATIVE_EFFECTS.get(ans.question_id)
            if effect is None or not ans.choice:
                continue
            criterion = COMPARATIVE_OPTION_CRITERIA.get(ans.choice.strip().lower())
            if criterion:
                adjustments[criterion] += effect
        return adjustments

    def unscored_answers(self) -> List[str]:
        """
        question_ids que não entram no cálculo numérico dos pesos (mas que ainda
        são enviados ao LLM como contexto, via qa_for_llm).

        Hoje cai aqui apenas `global_p1` ("qual provedor você percebe como mais
        alinhado"), de propósito: é a percepção prévia do gestor sobre uma
        alternativa, não a importância de um critério. Somá-la aos pesos faria a
        preferência subjetiva enviesar o ranking que deveria avaliá-la.

        Serve também de rede de proteção: se uma pergunta obrigatória nova passar
        a aparecer nesta lista, é sinal de que ela não foi registrada em
        COMPARATIVE_EFFECTS e está sem efeito no modelo.
        """
        unscored = []
        for ans in self.answers:
            if ans.text:
                continue  # texto livre sempre vai ao LLM
            if ans.choice and RELEVANCE_MAP.get(ans.choice) is not None:
                continue  # pergunta de relevância
            if ans.question_id in COMPARATIVE_EFFECTS:
                continue  # pergunta comparativa registrada
            if not ans.choice:
                continue  # não respondida (opcional)
            unscored.append(ans.question_id)
        return unscored

    # -- Entrada do LLM ------------------------------------------------------
    def qa_for_llm(self) -> List[Dict[str, str]]:
        """
        Pares pergunta/resposta legíveis para o LLM, incluindo as fechadas.
        Antes só os textos livres eram enviados, e chaveados pelo question_id.
        """
        pairs = []
        for ans in self.answers:
            answer = ans.text or ans.choice
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
    # Respostas fora do cálculo numérico (ainda enviadas ao LLM como contexto)
    unscored_answers: Optional[List[str]] = None
    # Cobertura documental e procedência das notas dos provedores
    coverage: Optional[Dict[str, Any]] = None
