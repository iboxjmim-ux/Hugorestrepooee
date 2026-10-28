"""
OEE por proceso — aplicación web.

Sirve la página (static/index.html) y una API pequeña:
  GET    /api/estado               configuración disponible
  GET    /api/reportes             todos los reportes
  PUT    /api/reportes/<id>        crea o reemplaza un reporte
  DELETE /api/reportes/<id>        borra un reporte
  POST   /api/leer                 lee la foto con Gemini y devuelve los datos
  GET    /api/velocidades          velocidades estándar
  PUT    /api/velocidades/<id>     cambia una velocidad (requiere iniciar sesión)
  POST   /api/login | /api/logout  entrar a la aplicación / modificar velocidades

Toda la API (menos /api/estado y /api/login) exige haber iniciado sesión.

Variables de entorno:
  GEMINI_API_KEY   clave de Google AI Studio (obligatoria para leer fotos)
  DATABASE_URL     Postgres (Supabase). Sin ella usa un archivo SQLite local.
  SECRET_KEY       firma de la sesión
  APP_USUARIO      usuario general para entrar a la app (operarios, supervisores)
  APP_CLAVE        contraseña general para entrar a la app
  GEMINI_MODEL     opcional, por defecto gemini-flash-latest
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session

BASE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=15 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

MODELO_GEMINI = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,120}$")

# Acceso para modificar velocidades. Solo se guardan huellas (PBKDF2), no el texto.
SAL = bytes.fromhex("e39dfec061e74e6d36ce7314fe53ade8")
HUELLA_USUARIO = "fe16532df28f38884e34231c248a03135f4663f56da67dc4c313ea9a17445bea"
HUELLA_CLAVE = "a8843ead87938aa2e4e3d9b43f3d36cebf7208d61619ab4a6faf5d7b92f7983d"


# ------------------------------------------------------------ base de datos
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    import psycopg

    PH = "%s"

    def conectar():
        # prepare_threshold=None: compatible con el pooler de Supabase
        return psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None)
else:
    PH = "?"
    RUTA_SQLITE = os.environ.get("SQLITE_PATH", str(BASE / "oee.db"))

    def conectar():
        con = sqlite3.connect(RUTA_SQLITE)
        con.isolation_level = None
        return con


def sql(consulta, parametros=(), filas=False):
    con = conectar()
    try:
        cur = con.cursor()
        cur.execute(consulta.replace("?", PH), parametros)
        return cur.fetchall() if filas else None
    finally:
        con.close()


def iniciar_bd():
    for tabla in ("reportes", "velocidades"):
        sql(f"CREATE TABLE IF NOT EXISTS {tabla} (id TEXT PRIMARY KEY, datos TEXT NOT NULL)")


def guardar(tabla, id_, datos):
    sql(f"INSERT INTO {tabla} (id, datos) VALUES (?, ?) "
        f"ON CONFLICT (id) DO UPDATE SET datos = excluded.datos", (id_, json.dumps(datos, ensure_ascii=False)))


def todos(tabla):
    return [(i, json.loads(d)) for i, d in sql(f"SELECT id, datos FROM {tabla}", filas=True)]


iniciar_bd()


def error(codigo, mensaje, estado=400):
    return jsonify({"code": codigo, "error": mensaje}), estado


def es_admin():
    return session.get("admin") is True


def con_acceso():
    return session.get("acceso") is True


def requiere_acceso(vista):
    @wraps(vista)
    def envuelta(*args, **kwargs):
        if not con_acceso():
            return error("sin_acceso", "Inicia sesión para usar la aplicación.", 401)
        return vista(*args, **kwargs)
    return envuelta


# ------------------------------------------------------------------ páginas
@app.get("/")
def inicio():
    return send_from_directory(BASE / "static", "index.html")


@app.get("/api/estado")
def estado():
    return jsonify({
        "lectura": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "baseDatos": "postgres" if DATABASE_URL else "local",
        "acceso": con_acceso(),
        "admin": es_admin(),
    })


# ---------------------------------------------------------------- reportes
@app.get("/api/reportes")
@requiere_acceso
def listar_reportes():
    return jsonify([{**d, "id": i} for i, d in todos("reportes")])


@app.put("/api/reportes/<id_>")
@requiere_acceso
def guardar_reporte(id_):
    datos = request.get_json(silent=True)
    if not ID_VALIDO.match(id_) or not isinstance(datos, dict) or not isinstance(datos.get("filas"), list):
        return error("invalido", "Reporte no válido.")
    datos.pop("id", None)
    guardar("reportes", id_, datos)
    return jsonify({"ok": True})


@app.delete("/api/reportes/<id_>")
@requiere_acceso
def borrar_reporte(id_):
    if not ID_VALIDO.match(id_):
        return error("invalido", "Identificador no válido.")
    sql("DELETE FROM reportes WHERE id = ?", (id_,))
    return jsonify({"ok": True})


# ------------------------------------------------------------- velocidades
@app.get("/api/velocidades")
@requiere_acceso
def listar_velocidades():
    return jsonify({i: d for i, d in todos("velocidades")})


@app.put("/api/velocidades/<id_>")
@requiere_acceso
def guardar_velocidad(id_):
    if not es_admin():
        return error("no_autorizado", "Inicia sesión para cambiar velocidades.", 401)
    datos = request.get_json(silent=True) or {}
    vel = datos.get("velocidad")
    if not ID_VALIDO.match(id_) or not str(datos.get("nombre", "")).strip():
        return error("invalido", "Proceso no válido.")
    if vel is not None and (not isinstance(vel, (int, float)) or vel <= 0):
        return error("invalido", "La velocidad debe ser un número mayor que cero.")
    guardar("velocidades", id_, {
        "nombre": str(datos["nombre"]).strip()[:200], "unidad": str(datos.get("unidad", ""))[:40],
        "velocidad": vel, "actualizado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return jsonify({"ok": True})


# ------------------------------------------------------------------- acceso
intentos = defaultdict(deque)


@app.post("/api/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    ahora = time.time()
    cola = intentos[ip]
    while cola and ahora - cola[0] > 300:
        cola.popleft()
    if len(cola) >= 5:
        return error("bloqueado", "Demasiados intentos. Espera 5 minutos.", 429)
    datos = request.get_json(silent=True) or {}
    usuario, clave = str(datos.get("usuario", "")).strip(), str(datos.get("clave", ""))
    huella = lambda t: hashlib.pbkdf2_hmac("sha256", t.encode(), SAL, 200_000).hex()
    admin = hmac.compare_digest(huella(usuario), HUELLA_USUARIO) & hmac.compare_digest(huella(clave), HUELLA_CLAVE)
    gen_usuario, gen_clave = os.environ.get("APP_USUARIO", ""), os.environ.get("APP_CLAVE", "")
    general = bool(gen_usuario and gen_clave) and (
        hmac.compare_digest(usuario.encode(), gen_usuario.encode()) & hmac.compare_digest(clave.encode(), gen_clave.encode()))
    if not (admin or general):
        cola.append(ahora)
        return error("credenciales", "Usuario o contraseña incorrectos.", 401)
    session.clear()
    session.permanent = True
    session["acceso"] = True
    session["admin"] = bool(admin)
    return jsonify({"ok": True, "admin": bool(admin)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# ------------------------------------------------------- lectura de la foto
NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
TXT = {"type": "string"}
ESQUEMA = {
    "type": "object",
    "properties": {
        "fecha": TXT, "turno": TXT, "area": TXT, "supervisor": TXT,
        "jornadaInicio": TXT, "jornadaFin": TXT,
        "filas": {"type": "array", "items": {"type": "object", "properties": {
            "proceso": TXT, "ini": TXT, "fin": TXT, "cantidad": NUM, "unidad": TXT, "rechazo": NUM,
            "paradas": NUM, "codigo": TXT, "detalle": TXT, "personas": NUM,
        }, "required": ["proceso", "ini", "fin", "cantidad", "unidad", "rechazo", "paradas", "codigo", "detalle", "personas"]}},
        "paradasPlanificadas": {"type": "array", "items": {"type": "object", "properties": {
            "tipo": TXT, "desde": TXT, "hasta": TXT}, "required": ["tipo", "desde", "hasta"]}},
        "observaciones": TXT,
        "advertencias": {"type": "array", "items": TXT},
    },
    "required": ["fecha", "turno", "area", "supervisor", "jornadaInicio", "jornadaFin", "filas",
                 "paradasPlanificadas", "observaciones", "advertencias"],
}

INSTRUCCIONES = """Esta foto es un "REPORTE DIARIO DE PRODUCCIÓN" llenado a mano. Transcribe sus datos.

Estructura del formato:
- Encabezado: Fecha (dd/mm/aaaa), Turno (casilla marcada A, B o C), Área / Bodega, Jornada hora inicio y hora fin (24 h), Supervisor.
- Tabla de hasta 12 filas: #, Proceso / Actividad, Hora inicio (24 h), Hora final (24 h), Cantidad realizada, Unidad (t, kg, m³, und, tambores, estibas…), Reproceso / rechazo (misma unidad), Paradas NO planificadas (min), Causa de la parada (código + detalle), N° personas.
- Recuadro "PARADAS PLANIFICADAS": Capacitación, Alimentación, Mantenimiento programado, Otra; cada una con Desde y Hasta.
- Códigos de causa: F = Falla de equipo, M = Falta de material, P = Falta de personal, L = Limpieza no programada, O = Otra.
- Observaciones al final.

Reglas:
- Incluye solo las filas de la tabla que tengan datos; ignora las vacías.
- Horas como "HH:MM" en 24 h (ini = hora inicio, fin = hora final). Vacía o ilegible: "".
- Fecha como "dd/mm/aaaa". Si no se lee: "".
- Números sin unidades. Casilla numérica vacía: null. Si escribieron 0, "-" o una raya en rechazo o paradas: 0.
- paradas = minutos de paradas NO planificadas. codigo: la letra (F, M, P, L u O) o "" si no hubo parada. detalle: el texto junto al código.
- paradasPlanificadas: solo las que tengan Desde y Hasta escritos.
- No inventes datos. Si algo es dudoso o ilegible, escribe tu mejor lectura y explica la duda en "advertencias" indicando fila y columna.
"""


@app.post("/api/leer")
@requiere_acceso
def leer():
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return error("config", "Falta configurar GEMINI_API_KEY en el servidor.", 503)
    foto = request.files.get("foto")
    if not foto:
        return error("invalido", "No llegó ninguna foto.")
    imagen = foto.read()
    tipo = foto.mimetype if foto.mimetype in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
    try:
        conocidos = [str(n)[:120] for n in json.loads(request.form.get("conocidos") or "[]")][:300]
    except (ValueError, TypeError):
        conocidos = []
    texto = INSTRUCCIONES
    if conocidos:
        texto += ("- Procesos ya registrados. Si un proceso de la foto es claramente uno de estos, "
                  "usa el nombre EXACTO:\n" + "\n".join("  · " + n for n in conocidos))

    from google import genai
    from google.genai import errors, types

    client = genai.Client()
    contenido = [types.Part.from_bytes(data=imagen, mime_type=tipo), texto]
    try:
        try:
            r = client.models.generate_content(
                model=MODELO_GEMINI, contents=contenido,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=ESQUEMA),
            )
        except errors.ClientError as e:
            if e.code != 400 or "schema" not in str(e).lower():
                raise
            contenido[1] = texto + "\nResponde SOLO con un JSON con este esquema:\n" + json.dumps(ESQUEMA)
            r = client.models.generate_content(
                model=MODELO_GEMINI, contents=contenido,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
    except errors.ClientError as e:
        app.logger.warning("Gemini %s: %s", e.code, e)
        if e.code == 429:
            return error("limite", "Se alcanzó el límite gratuito de Gemini. Intenta más tarde.", 429)
        if e.code in (401, 403) or "api key" in str(e).lower():
            return error("config", "La API key de Gemini no es válida.", 503)
        if e.code == 404:
            return error("config", f"El modelo {MODELO_GEMINI} no existe; revisa GEMINI_MODEL.", 503)
        return error("lectura", f"Gemini respondió error {e.code}.", 502)
    except errors.ServerError as e:
        return error("lectura", f"Gemini no está disponible ahora ({e.code}).", 502)
    except OSError:
        return error("lectura", "El servidor no pudo conectarse con Gemini.", 502)
    try:
        return jsonify(json.loads(r.text or ""))
    except ValueError:
        return error("lectura", "No se pudo interpretar la lectura de la foto. Intenta con una foto más clara.", 502)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
