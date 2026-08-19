"""Camada transversal de auditoria (diretriz §27–§28)."""

from audit.versioning import canonical_hash, questionnaire_fingerprint, runtime_versions

__all__ = ["canonical_hash", "questionnaire_fingerprint", "runtime_versions"]
