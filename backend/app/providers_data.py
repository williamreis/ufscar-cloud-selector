"""
Provedores avaliados.

`doc_keywords` liga um documento ao provedor pelo nome do arquivo: na ingestão,
o primeiro provedor cujo keyword aparece no nome do arquivo é gravado no metadado
`provider` de cada chunk. As evidências e a extração de desempenho são então
filtradas por esse campo, para que um trecho da AWS nunca sustente o indicador
de outro provedor.

Consequência prática: um documento cujo nome não contenha nenhum desses termos
não é atribuído a nenhum provedor e não aparece como evidência. Ao adicionar
arquivos em data/pdf (ou via upload), inclua o nome do provedor no nome do arquivo.

**Não há notas aqui.** Até a Fase 2 este módulo carregava um dicionário `scores`
com constantes digitadas à mão, e era dele que saía o ranking. O desempenho dos
provedores agora vem da extração documental (`evidence.py`) e da normalização
determinística (`domain/normalization.py`); um provedor sem evidência comparável
não recebe nota de reserva, ele sai do conjunto comparável daquele indicador.
"""

# Procedência do desempenho usado no ranking. Vai para `coverage` na resposta da
# API e é exibida no relatório: o gestor precisa saber de onde veio cada número
# antes de tratar a ordem como recomendação.
PROVIDER_SCORES_PROVENANCE = {
    "status": "evidence_extracted",
    "summary": (
        "O desempenho de cada provedor é extraído dos documentos indexados pelo RAG, "
        "indicador a indicador, e convertido em valor comparável por regras determinísticas. "
        "Indicador sem evidência comparável em todas as alternativas sai da avaliação em vez "
        "de receber nota."
    ),
}

PROVIDERS = [
    {"id": "aws", "name": "AWS", "doc_keywords": ["aws", "amazon"]},
    {"id": "gcp", "name": "Google Cloud", "doc_keywords": ["gcp", "google"]},
    {"id": "azure", "name": "Microsoft Azure", "doc_keywords": ["azure", "microsoft"]},
    {"id": "oracle", "name": "Oracle Cloud", "doc_keywords": ["oracle", "oci"]},
    {"id": "ibm", "name": "IBM Cloud", "doc_keywords": ["ibm"]},
]
