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
    s1 = st.selectbox("O provedor adota práticas de eficiência energética?",
                      ["Sim, de forma comprovada", "Sim, parcialmente", "Não sei informar", "Não adota"])
    s2 = st.selectbox("A empresa demonstra preocupação ambiental?", ["Alta", "Média", "Baixa", "Não sei"])
    s_text = st.text_area("Quais iniciativas de sustentabilidade considera relevantes? (resposta curta)")
    st.header("Desempenho")
    p1 = st.selectbox("Como você avalia a disponibilidade (uptime) oferecida pelo provedor?",
                      ["Superior a 99,9%", "Entre 99% e 99,9%", "Inferior a 99%", "Não informado"])
    p2 = st.selectbox("O provedor garante escalabilidade conforme demanda?",
                      ["Totalmente", "Parcialmente", "Limitada", "Não sei informar"])
    p_text = st.text_area("Quais fatores mais influenciam sua percepção de desempenho?")
    st.header("Segurança")
    sec1 = st.selectbox("O provedor possui certificações de segurança (ex: ISO 27001)?",
                        ["Sim, várias", "Sim, pelo menos uma", "Não possui", "Não sei informar"])
    sec2 = st.selectbox("O provedor oferece backup e recuperação de desastres?",
                        ["Sim, com redundância geográfica", "Sim, local", "Parcialmente", "Não oferece"])
    sec_text = st.text_area("Principais riscos de segurança que você enxerga?")
    submitted = st.form_submit_button("Enviar e obter recomendação")

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
