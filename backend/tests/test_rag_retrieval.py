"""
Recuperação de trechos: escopo, filtro por provedor e busca em lote.

A busca em lote existe por desempenho — 39 consultas por avaliação viravam 39
ida-e-voltas até a API de embeddings (17,5s medidos, contra 0,44s agrupadas).
O que estes testes protegem é que a economia não custe nada em método: mesmo
resultado, mesmo filtro, e um caminho de recuo quando o lote falha.
"""

# --- Recuperação em lote ---------------------------------------------------


class _IndiceFalso:
    """
    Índice que conta as chamadas de embedding e responde por vetor.

    O ponto do teste não é o conteúdo devolvido — é **quantas vezes** a API de
    embeddings é chamada. Eram 39 por avaliação, uma por consulta.
    """

    def __init__(self):
        self.lotes = 0
        self.textos = []
        self.embedding_function = self

    def embed_documents(self, textos):
        self.lotes += 1
        self.textos.extend(textos)
        return [[float(len(t)), 0.0] for t in textos]

    def embed_query(self, texto):  # usado pelo caminho consulta a consulta
        self.lotes += 1
        self.textos.append(texto)
        return [float(len(texto)), 0.0]

    def _doc(self, provider):
        class D:
            page_content = "trecho"
            metadata = {"chunk_id": f"c-{provider}", "provider_id": provider}

        return D()

    def similarity_search_with_score_by_vector(self, vector, k, filter=None, fetch_k=None):
        provedor = (filter or {}).get("provider", "?")
        return [(self._doc(provedor), 0.1)]

    def similarity_search_with_score(self, query, k, filter=None, fetch_k=None):
        provedor = (filter or {}).get("provider", "?")
        return [(self._doc(provedor), 0.1)]


def test_lote_faz_uma_unica_chamada_de_embedding(monkeypatch):
    from rag import retrieval

    indice = _IndiceFalso()
    monkeypatch.setattr(retrieval.index_module, "load", lambda: indice)

    consultas = [(f"consulta {i}", "aws") for i in range(39)]
    resultados = retrieval.search_many(consultas, top_k=3)

    assert len(resultados) == 39
    assert indice.lotes == 1, "39 consultas precisam viajar numa chamada só"
    assert indice.textos == [q for q, _ in consultas]


def test_lote_devolve_o_mesmo_que_a_busca_uma_a_uma(monkeypatch):
    """Agrupar é transporte, não método: o resultado precisa ser idêntico."""
    from rag import retrieval

    indice = _IndiceFalso()
    monkeypatch.setattr(retrieval.index_module, "load", lambda: indice)

    consultas = [("energia renovável", "aws"), ("criptografia", "gcp")]
    em_lote = retrieval.search_many(consultas, top_k=3)
    uma_a_uma = [retrieval.search(q, 3, None, pid) for q, pid in consultas]

    assert em_lote == uma_a_uma


def test_filtro_por_provedor_continua_valendo_no_lote(monkeypatch):
    """
    O isolamento da §14.2 não pode se perder no agrupamento: cada consulta é
    filtrada pelo seu provedor, e não pelo do vizinho de lote.
    """
    from rag import retrieval

    indice = _IndiceFalso()
    monkeypatch.setattr(retrieval.index_module, "load", lambda: indice)

    resultados = retrieval.search_many([("q1", "aws"), ("q2", "azure")], top_k=3)
    assert resultados[0][0]["chunk_id"] == "c-aws"
    assert resultados[1][0]["chunk_id"] == "c-azure"


def test_falha_no_lote_cai_para_consulta_a_consulta(monkeypatch):
    """
    O lote é desempenho; a avaliação não pode depender dele. Se a API recusar o
    conjunto, cada consulta segue sozinha — mais lento e com o mesmo resultado.
    """
    from rag import retrieval

    indice = _IndiceFalso()

    def _explode(_textos):
        raise RuntimeError("payload grande demais")

    indice.embed_documents = _explode
    monkeypatch.setattr(retrieval.index_module, "load", lambda: indice)

    resultados = retrieval.search_many([("q1", "aws"), ("q2", "gcp")], top_k=3)
    assert [r[0]["chunk_id"] for r in resultados] == ["c-aws", "c-gcp"]


def test_lote_vazio_nao_chama_a_api(monkeypatch):
    from rag import retrieval

    indice = _IndiceFalso()
    monkeypatch.setattr(retrieval.index_module, "load", lambda: indice)
    assert retrieval.search_many([], top_k=3) == []
    assert indice.lotes == 0
