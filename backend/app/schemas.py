from pydantic import BaseModel
from typing import List, Dict, Optional


class QuestionAnswer(BaseModel):
    question_id: str
    choice: Optional[str] = None
    text: Optional[str] = None


class QuestionnaireResponse(BaseModel):
    respondent: str
    answers: List[QuestionAnswer]

    def to_numeric_scores(self) -> Dict[str, float]:
        """
        Mapeia respostas fechadas para valores 1-5 por dimensão.
        Convenção: question_id como 'sust_p1', 'perf_p1', 'sec_p1', etc.
        Retorna dict com agregação (média) por dimensão.
        """
        buckets = {"sustainability": [], "performance": [], "security": []}
        mapping = {
            # mapeamento de choices para scores
            "Alta": 5, "Muito": 5, "Superior a 99,9%": 5,
            "Média": 3, "Parcialmente": 3,
            "Baixa": 2, "Não": 1, "Não sei informar": 3
        }
        for ans in self.answers:
            qid = ans.question_id.lower()
            if "sust" in qid:
                if ans.choice:
                    buckets["sustainability"].append(mapping.get(ans.choice, 3))
            if "perf" in qid:
                if ans.choice:
                    buckets["performance"].append(mapping.get(ans.choice, 3))
            if "sec" in qid:
                if ans.choice:
                    buckets["security"].append(mapping.get(ans.choice, 3))
        # calcula médias
        return {k: (sum(v) / len(v) if v else 3.0) for k, v in buckets.items()}

    def free_texts_dict(self):
        d = {}
        for ans in self.answers:
            if ans.text:
                d[ans.question_id] = ans.text
        return d


class RecommendationResponse(BaseModel):
    ranking: List[Dict]
    criteria_weights: Dict[str, float]
    notes: Optional[str]
    evidences: Dict[str, List[Dict]]
