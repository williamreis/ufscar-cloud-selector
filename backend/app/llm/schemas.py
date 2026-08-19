"""
Schemas de saída da LLM (diretriz §19).

Toda geração passa por um destes modelos antes de ser aceita. O schema é a
primeira barreira do pipeline da §25 — o que não couber aqui não vira dado.
"""

from pydantic import BaseModel, Field


class PreferenceNotes(BaseModel):
    """Saída de `PROMPT_PREFERENCE_NOTES_V1`: apenas texto, nenhum número."""

    notes: str = Field(description="Justificativa textual das prioridades declaradas.")
