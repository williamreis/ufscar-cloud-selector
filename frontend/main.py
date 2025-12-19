import streamlit as st
import requests
from pydantic import BaseModel

API_URL = "http://cloud_backend:8000/api/recommend"  # no docker-compose, backend service name

st.set_page_config(page_title="Cloud Provider Selector", layout="wide")

st.title("Assistente de Seleção de Provedores de Cloud")

with st.form("questionnaire"):
    respondent = st.text_input("Nome do gestor / e-mail (opcional)")

    st.header("Sustentabilidade")
    s1 = st.selectbox(
        "1. Ao definir critérios para a escolha de provedores de Cloud Computing, qual é a relevância da eficiência energética dos data centers?",
        ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
        index=0
    )
    s2 = st.selectbox(
        "2. No processo de decisão sobre a contratação de serviços em nuvem, qual é a relevância das práticas de uso de energia renovável e redução de emissões?",
        ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
        index=0
    )
    s_text = st.text_area("3. Em sua opinião, como a sustentabilidade deve ser considerada na contratação de serviços em nuvem?")

    st.header("Desempenho")
    p1 = st.selectbox("4. Na avaliação de provedores de Cloud Computing, qual é a relevância da disponibilidade (uptime) dos serviços?",
                      ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
                      index=0)

    p2 = st.selectbox("5. No processo de escolha do provedor, qual é a relevância do suporte técnico e do tempo de resposta a incidentes?",
                      ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
                      index=0)

    p_text = st.text_area("6. Cite um exemplo de problema de desempenho que impactaria a continuidade dos serviços da instituição?")

    st.header("Segurança")
    sec1 = st.selectbox("7. Ao selecionar um provedor de Cloud Computing, qual é a relevância das certificações de segurança da informação (ex.: ISO 27001, SOC 2, GDPR)?",
                        ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
                        index=0)

    sec2 = st.selectbox("8. No contexto da segurança da informação, qual é a relevância dos mecanismos de backup e recuperação de desastres como requisitos mínimos do serviço?",
                        ["", "Decisivo (critério indispensável)", "Muito relevante", "Relevante", "Pouco relevante", "Irrelevante"],
                        index=0)
    
    sec_text = st.text_area("9. Em sua percepção, quais medidas de segurança são indispensáveis em provedores de Cloud?")

    st.header("Comparações indiretas (entre critérios principais)")
    c1 = st.selectbox(
        "10. Quando há necessidade de priorizar recursos, sua instituição tende a valorizar mais:",
        ["", "Sustentabilidade ambiental", "Desempenho e estabilidade", "Segurança da informação"],
        index=0
    )
    c2 = st.selectbox(
        "11. Se fosse necessário renunciar a um dos três aspectos (sustentabilidade, desempenho ou segurança), qual teria menor impacto na sua operação?",
        ["", "Sustentabilidade", "Desempenho", "Segurança"],
        index=0
    )
    c3 = st.selectbox(
        "12. Em decisões estratégicas, qual dimensão costuma receber maior atenção dos gestores?",
        ["", "Sustentabilidade", "Desempenho", "Segurança"],
        index=0
    )

    st.header("Avaliação global e alternativas")
    g1 = st.selectbox(
        "13. Considerando provedores de mercado (AWS, Azure, Google Cloud), qual você percebe como mais alinhado às demandas da sua instituição?",
        ["", "AWS", "Azure", "Google Cloud", "Nenhum / On-Premise"],
        index=0
    )
    g2 = st.selectbox(
        "14. Qual fator mais influenciaria uma mudança de provedor?",
        ["", "Sustentabilidade e responsabilidade ambiental", "Desempenho e escalabilidade", "Segurança e conformidade", "Custo total de propriedade"],
        index=0
    )
    g_text = st.text_area("15. Em uma frase, descreva o que significa “provedor ideal” para sua instituição.")

    submitted = st.form_submit_button("Enviar Questionário")

if submitted:
    # montar payload
    answers = [
        {"question_id": "sust_p1", "choice": s1, "text": None}, # 1
        {"question_id": "sust_p2", "choice": s2, "text": s_text}, # 2, 3
        {"question_id": "perf_p1", "choice": p1, "text": None}, # 4
        {"question_id": "perf_p2", "choice": p2, "text": p_text}, # 5, 6
        {"question_id": "sec_p1", "choice": sec1, "text": None}, # 7
        {"question_id": "sec_p2", "choice": sec2, "text": sec_text}, # 8, 9
        {"question_id": "comp_p1", "choice": c1, "text": None}, # 10
        {"question_id": "comp_p2", "choice": c2, "text": None}, # 11
        {"question_id": "comp_p3", "choice": c3, "text": None}, # 12
        {"question_id": "global_p1", "choice": g1, "text": None}, # 13
        {"question_id": "global_p2", "choice": g2, "text": g_text}, # 14, 15
    ]
    payload = {"respondent": respondent, "answers": answers}
    with st.spinner("Processando... (pode levar alguns segundos)"):
        r = requests.post(API_URL, json=payload, timeout=120)
    if r.status_code == 200:
        data = r.json()
        st.success("Recomendação gerada")
        st.subheader("Ranking de provedores")
        for row in data["ranking"]:
            st.write(f"**{row['rank']}. {row['name']}** — Score: {row['score']:.3f}")
        st.subheader("Pesos atribuídos (critério)")
        st.json(data["criteria_weights"])
        st.subheader("Justificativa do sistema")
        st.write(data["notes"])
        st.subheader("Evidências (trechos de documentos)")
        for p_id, docs in data["evidences"].items():
            st.markdown(f"**{p_id}**")
            for d in docs:
                st.write(d["page_content"])
                st.write(f"_score: {d['score']:.3f_}")
    else:
        st.error(f"Erro {r.status_code}: {r.text}")
