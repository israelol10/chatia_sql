from flask import Flask, request, jsonify
import os
import json
import logging
from sqlalchemy import create_engine, text
import openai
import re

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------
# Configuración de SQL (con pyodbc)
# ---------------------------
DB_SERVER = os.getenv("DB_SERVER", "servidor-chattesis.database.windows.net")
DB_DATABASE = os.getenv("DB_DATABASE", "GrupoChatTesis")
DB_UID = os.getenv("DB_UID", "adminchat")
DB_PWD = os.getenv("DB_PWD", "Israel***228612")  # Usa variable de entorno real en Render

connection_string = (
    f"mssql+pyodbc://{DB_UID}:{DB_PWD}@{DB_SERVER}:1433/{DB_DATABASE}"
    "?driver=ODBC+Driver+18+for+SQL+Server"
)
engine = create_engine(connection_string)

# ---------------------------
# Configuración de OpenAI
# ---------------------------
openai.api_type = "azure"
openai.api_base = os.getenv("AZURE_OPENAI_API_BASE", "https://pruebai.openai.azure.com/")
openai.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15")
openai.api_key = os.getenv("AZURE_OPENAI_API_KEY")

# ---------------------------
# Funciones de utilidad
# ---------------------------
def clean_prompt(prompt):
    patterns = [
        r'\bcual(es)?\s+es\s+el\s+link\s+para\b',
        r'\bpasame\s+el\s+link\s+para\b',
        r'\bdame\s+el\s+link\s+para\b',
        r'\blink\s+para\b',
        r'\blinks?\s+para\b'
    ]
    for pat in patterns:
        prompt = re.sub(pat, '', prompt, flags=re.IGNORECASE)
    return prompt.strip()

def refine_query(prompt):
    try:
        response = openai.ChatCompletion.create(
            deployment_id="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Devuelve exactamente 2 palabras clave relevantes para buscar en SQL en formato JSON."},
                {"role": "user", "content": f"Extrae 2 palabras clave en JSON del siguiente texto: {prompt}"}
            ],
            max_tokens=20
        )
        content = response["choices"][0]["message"]["content"].strip()
        data = json.loads(content)
        return [kw.lower() for kw in data.get("keywords", [])][:2]
    except:
        return prompt.lower().split()[:2]

def expects_single_link(query):
    return any(x in query.lower() for x in ["cual es el link", "dame el link", "pasame el link"])

def generate_natural_answer(user_query, sql_data):
    if sql_data:
        joined = "; ".join([f"{r['Nombre']}: {r['URL']}" for r in sql_data])
        prompt = f"Con la consulta '{user_query}', los siguientes resultados fueron encontrados: {joined}. Genera una respuesta clara y concisa en Markdown."
    else:
        prompt = f"Con la consulta '{user_query}', no se encontró información en la base de datos."

    try:
        response = openai.ChatCompletion.create(
            deployment_id="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Responde en español usando Markdown."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error al generar respuesta: {e}"

# ---------------------------
# Endpoint SQL
# ---------------------------
@app.route('/search_sql', methods=['POST'])
def search_sql():
    user_query = request.json.get("query", "")
    logging.info(f"Consulta recibida: {user_query}")
    if not user_query:
        return jsonify({"answer": "Consulta vacía."}), 400

    prompt_clean = clean_prompt(user_query)
    keywords = refine_query(prompt_clean)
    if len(keywords) < 2:
        keywords.append(keywords[0])
    kw1, kw2 = keywords[0], keywords[1]

    sql = text("""
        SELECT TOP 3 Nombre_del_Recurso AS Nombre, URL
        FROM dbo.LinksBusqueda
        WHERE (LOWER(Nombre_del_Recurso) LIKE :kw1 OR LOWER(Descripción) LIKE :kw1)
           OR (LOWER(Nombre_del_Recurso) LIKE :kw2 OR LOWER(Descripción) LIKE :kw2)
    """)

    try:
        with engine.connect() as conn:
            result = conn.execute(sql, {"kw1": f"%{kw1}%", "kw2": f"%{kw2}%"})
            rows = [dict(r) for r in result]
            respuesta = generate_natural_answer(user_query, rows)
            return jsonify({"answer": respuesta, "results": rows})
    except Exception as e:
        logging.error(f"Error SQL: {e}")
        return jsonify({"answer": "Error al consultar la base de datos."})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
