from flask import Flask, request, jsonify
import pyodbc
import openai
import re
import os
import json
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ----------------------------------------------------
# Configuración de conexión a la base de datos (Links)
# ----------------------------------------------------
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_SERVER = os.getenv("DB_SERVER", "servidor-chattesis.database.windows.net")
DB_DATABASE = os.getenv("DB_DATABASE", "GrupoChatTesis")
DB_UID = os.getenv("DB_UID", "adminchat")
DB_PWD = os.getenv("DB_PWD", "Isra***228612")  # Sustituir en producción

connection_string = (
    f"DRIVER={{{DB_DRIVER}}};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={DB_DATABASE};"
    f"UID={DB_UID};"
    f"PWD={DB_PWD};"
    "Encrypt=yes;"
    "TrustServerCertificate=no;"
    "Connection Timeout=30;"
)

# ----------------------------------------------------
# Configuración de OpenAI
# ----------------------------------------------------
openai.api_type = "azure"
openai.api_base = os.getenv("OPENAI_API_BASE", "https://pruebai.openai.azure.com/")
openai.api_version = os.getenv("OPENAI_API_VERSION", "2023-05-15")
openai.api_key = os.getenv("OPENAI_API_KEY", "e23d37fb53de4064a91e8399b0dd35b7")

# ----------------------------------------------------
# Funciones comunes
# ----------------------------------------------------
def clean_prompt(prompt):
    patterns = [
        r'\bcual(es)?\s+es\s+el\s+link\s+para\b',
        r'\bpasame\s+el\s+link\s+para\b',
        r'\bdame\s+el\s+link\s+para\b',
        r'\blink\s+para\b',
        r'\blinks?\s+para\b'
    ]
    cleaned = prompt
    for pat in patterns:
        cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def refine_query(prompt):
    try:
        response = openai.ChatCompletion.create(
            deployment_id="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Devuelve exactamente 2 palabras clave relevantes para buscar en SQL en formato JSON, "
                    "por ejemplo: {\"keywords\": [\"palabra1\", \"palabra2\"]}."
                )},
                {"role": "user", "content": f"Extrae 2 palabras clave en JSON del siguiente texto: {prompt}"}
            ],
            max_tokens=20
        )
        content = response["choices"][0]["message"]["content"].strip()
        try:
            data = json.loads(content)
            keywords_list = data.get("keywords", [])
        except json.JSONDecodeError:
            logging.warning("No se pudo parsear la respuesta JSON, usando regex")
            keywords_list = re.findall(r'\b\w+\b', content.lower())[:2]
        if len(keywords_list) < 2:
            keywords_list = prompt.lower().split()[:2]
        return [kw.lower() for kw in keywords_list][:2]
    except Exception as e:
        logging.error(f"Error en OpenAI (refine_query): {e}")
        return prompt.lower().split()[:2]

def expects_single_link(query):
    q = query.lower()
    if "cual es el link" in q or "dame el link" in q or "pasame el link" in q:
        return True
    return False

def generate_natural_answer(user_query, sql_data):
    """
    Genera una respuesta natural en español a partir de los resultados obtenidos.
    No incluye el prefijo "Datos SQL:" en la respuesta.
    """
    if sql_data["results"]:
        results_text = ""
        for idx, res in enumerate(sql_data["results"], start=1):
            # Se genera un resumen simple de cada registro
            line_parts = [f"{key}: {val}" for key, val in res.items()]
            line_str = ", ".join(line_parts)
            results_text += f"{idx}. **{line_str}**; "
        if len(results_text) > 500:
            results_text = results_text[:500] + " ..."
        if expects_single_link(user_query):
            prompt = (f"Con la consulta '{user_query}', se han encontrado los siguientes resultados: {results_text} "
                      "Elige el resultado más relevante y genera una respuesta natural y concisa en español, "
                      "indicando únicamente el enlace principal en formato Markdown. No incluyas el prefijo 'Datos SQL:' en la respuesta.")
        else:
            prompt = (f"Con la consulta '{user_query}', se han encontrado los siguientes resultados: {results_text} "
                      "Genera una respuesta natural y concisa en español que resuma la información en formato de lista, "
                      "usando Markdown para resaltar los enlaces. No incluyas el prefijo 'Datos SQL:' en la respuesta.")
    else:
        prompt = (f"Con la consulta '{user_query}', no se encontró información en la base de datos. "
                  "Genera una respuesta natural en español indicando que no se hallaron resultados. "
                  "No incluyas el prefijo 'Datos SQL:' en la respuesta.")
    logging.info(f"🔎 Prompt para respuesta natural: {prompt}")
    try:
        response = openai.ChatCompletion.create(
            deployment_id="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un asistente que responde de forma natural en español."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200
        )
        generated = response["choices"][0]["message"]["content"].strip()
        return generated
    except Exception as e:
        logging.error(f"Error generando respuesta natural: {e}")
        return sql_data["message"]

# ----------------------------------------------------
# Endpoint: Consulta en dbo.LinksBusqueda
# ----------------------------------------------------
def search_database_links(prompt):
    try:
        cleaned_prompt = clean_prompt(prompt)
        logging.info(f"🧹 Prompt limpio (Links): '{cleaned_prompt}'")
        search_terms = refine_query(cleaned_prompt)
        logging.info(f"🔍 Palabras clave generadas (Links): {search_terms}")
        if len(search_terms) < 2:
            search_terms.append(search_terms[0])
        kw1, kw2 = search_terms[0], search_terms[1]
        query = """
            SELECT TOP 3 Nombre_del_Recurso AS Nombre, URL, Categoría, Descripción
            FROM dbo.LinksBusqueda
            WHERE (LOWER(Nombre_del_Recurso) LIKE ? OR LOWER(Nombre_del_Recurso) LIKE ?)
               OR (LOWER(Categoría) LIKE ? OR LOWER(Categoría) LIKE ?)
               OR (LOWER(Descripción) LIKE ? OR LOWER(Descripción) LIKE ?)
            ORDER BY 
                CASE 
                    WHEN LOWER(Nombre_del_Recurso) LIKE ? THEN 1
                    WHEN LOWER(Descripción) LIKE ? THEN 2
                    ELSE 3
                END
        """
        search_params = [
            f"%{kw1}%", f"%{kw2}%",
            f"%{kw1}%", f"%{kw2}%",
            f"%{kw1}%", f"%{kw2}%",
            f"%{kw1}%", f"%{kw2}%"
        ]
        logging.info(f"🟢 Ejecutando SQL (Links) con parámetros: {search_params}")
        with pyodbc.connect(connection_string) as conn:
            cursor = conn.cursor()
            cursor.execute(query, search_params)
            rows = cursor.fetchall()
        if not rows:
            logging.info("❌ No se encontraron resultados en SQL (Links).")
            return {"message": "❌ No se encontró información en la base de datos.", "results": []}
        resultados = []
        for row in rows:
            resultados.append({
                "Nombre": row[0],
                "URL": row[1],
                "Categoría": row[2],
                "Descripción": row[3]
            })
        logging.info(f"✅ Resultados SQL (Links): {resultados}")
        return {"message": "✅ Datos encontrados (Links).", "results": resultados}
    except Exception as e:
        logging.error(f"❌ Error en SQL (Links): {e}")
        return {"message": "❌ Error al consultar la base de datos (Links).", "error": str(e), "results": []}

@app.route('/search_sql', methods=['POST'])
def endpoint_links():
    user_query = request.json.get("query", "").strip()
    logging.info(f"🔵 Flask recibió la consulta (Links): {user_query}")
    if not user_query:
        return jsonify({"answer": "❌ Consulta vacía."}), 400
    sql_results = search_database_links(user_query)
    natural_answer = generate_natural_answer(user_query, sql_results)
    logging.info(f"🔹 Respuesta generada (Links): {natural_answer}")
    response = jsonify({"answer": natural_answer, "results": sql_results.get("results", [])})
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
