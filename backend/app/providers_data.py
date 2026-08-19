"""
Provedores avaliados pelo AHP.

`doc_keywords` liga um documento ao provedor pelo nome do arquivo: na ingestão,
o primeiro provedor cujo keyword aparece no nome do arquivo é gravado no metadado
`provider` de cada chunk. As evidências do relatório são então filtradas por esse
campo, para que um trecho da AWS nunca seja citado como evidência de outro provedor.

Consequência prática: um documento cujo nome não contenha nenhum desses termos
não é atribuído a nenhum provedor e não aparece como evidência. Ao adicionar
arquivos em data/pdf (ou via upload), inclua o nome do provedor no nome do arquivo.
"""

# ---------------------------------------------------------------------------
# PROCEDÊNCIA DAS NOTAS ABAIXO — leia antes de usar o resultado em publicação.
#
# Os valores em `scores` são constantes fixas, digitadas manualmente. Eles NÃO são
# derivados dos documentos indexados no RAG, nem de benchmark, medição ou fonte
# citável. O RAG alimenta apenas a seção de evidências do relatório; o ranking é
# calculado só a partir destas constantes e dos pesos do questionário.
#
# Consequência: o ranking entre provedores não é sustentado por evidência — a
# ordem AWS > Google > Azure decorre destes números, não dos relatórios oficiais.
# Para que o ranking tenha lastro, cada nota precisa ser substituída por um valor
# rastreável (ex.: PUE declarado no relatório do provedor, SLA de disponibilidade
# contratual, lista de certificações válidas), com a fonte registrada em `sources`.
# ---------------------------------------------------------------------------
PROVIDER_SCORES_PROVENANCE = {
    "status": "unsourced_placeholder",
    "summary": (
        "As notas por critério são valores de referência fixos, definidos manualmente, "
        "sem derivação dos documentos indexados nem fonte citável."
    ),
}

PROVIDERS = [
    {
        "id": "aws",
        "name": "AWS",
        "doc_keywords": ["aws", "amazon"],
        "scores": {"sustainability": 0.7, "performance": 0.9, "security": 0.85, "cost": 0.6, "support": 0.8},
    },
    {
        "id": "gcp",
        "name": "Google Cloud",
        "doc_keywords": ["gcp", "google"],
        "scores": {"sustainability": 0.8, "performance": 0.85, "security": 0.8, "cost": 0.7, "support": 0.75},
    },
    {
        "id": "azure",
        "name": "Microsoft Azure",
        "doc_keywords": ["azure", "microsoft"],
        "scores": {"sustainability": 0.75, "performance": 0.8, "security": 0.9, "cost": 0.65, "support": 0.85},
    },
    {
        "id": "oracle",
        "name": "Oracle Cloud",
        "doc_keywords": ["oracle", "oci"],
        "scores": {"sustainability": 0.6, "performance": 0.7, "security": 0.7, "cost": 0.8, "support": 0.7},
    },
    {
        "id": "ibm",
        "name": "IBM Cloud",
        "doc_keywords": ["ibm"],
        "scores": {"sustainability": 0.65, "performance": 0.75, "security": 0.75, "cost": 0.75, "support": 0.75},
    },
]
