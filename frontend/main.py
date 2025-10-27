import streamlit as st
import requests
from pydantic import BaseModel

API_URL = "http://cloud_backend:8000/api/recommend"  # no docker-compose, backend service name

st.set_page_config(page_title="Cloud Provider Selector", layout="wide")

st.title("Assistente de Seleção de Provedores de Cloud")
st.markdown("Responda o questionário. O sistema usará IA + AHP para recomendar provedores.")

with st.form("questionnaire"):
    respondent = st.text_input("Nome do gestor / e-mail (opcional)")

    st.header("Sustentabilidade")
    s1 = st.selectbox(
        "1. O quanto a eficiência energética dos data centers é relevante para sua instituição na escolha de provedores de Cloud?",
        ["Extremamente relevante", "Muito relevante", "Moderadamente relevante", "Pouco relevante", "Irrelevante"]
    )
    s2 = st.selectbox(
        "2. O quanto práticas como uso de energia renovável e redução de emissões influenciam sua decisão?",
        ["Influenciam fortemente", "Influenciam moderadamente", "Pouco influenciam", "Não influenciam"]
    )
    s_text = st.text_area("3. Em sua opinião, como a sustentabilidade deve ser considerada na contratação de serviços em nuvem?")

    st.header("Desempenho")
    p1 = st.selectbox("4. O quanto a disponibilidade (uptime) dos serviços impacta a confiança no provedor?",
                      ["Impacta totalmente", "Impacta muito", "Impacta moderadamente", "Impacta pouco", "Não impacta"])

    p2 = st.selectbox("5. O suporte técnico e o tempo de resposta a incidentes são determinantes na sua avaliação?",
                      ["Sim, determinantes", "Sim, relevantes", "Moderadamente relevantes", "Pouco relevantes", "Irrelevantes"])

    p_text = st.text_area("6. Cite um exemplo de problema de desempenho que impactaria a continuidade dos serviços da instituição.?")

    st.header("Segurança")
    sec1 = st.selectbox("7. O quanto certificações de segurança (ex.: ISO 27001, SOC 2, GDPR) influenciam sua confiança no provedor?",
                        ["Influenciam totalmente", "Influenciam muito", "Influenciam moderadamente", "Pouco influenciam", "Não influenciam"])

    sec2 = st.selectbox("8. Quão importante é o backup e a recuperação de desastres como requisito mínimo de segurança?",
                        ["Extremamente importante", "Muito importante", "Moderadamente importante", "Pouco importante", "Irrelevante"])
    
    sec_text = st.text_area("9. Em sua percepção, quais medidas de segurança são indispensáveis em provedores de Cloud?")

    submitted = st.form_submit_button("Enviar Questionário")

if submitted:
    # montar payload
    answers = [
        {"question_id": "sust_p1", "choice": s1, "text": None},
        {"question_id": "sust_p2", "choice": s2, "text": s_text},
        {"question_id": "perf_p1", "choice": p1, "text": None},
        {"question_id": "perf_p2", "choice": p2, "text": p_text},
        {"question_id": "sec_p1", "choice": sec1, "text": None},
        {"question_id": "sec_p2", "choice": sec2, "text": sec_text},
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
