# OEE — aplicación web

Lee la foto del "Reporte diario de producción" con Gemini, calcula el OEE por proceso
(disponibilidad × rendimiento × calidad), guarda los reportes y muestra las gráficas.
Las velocidades estándar solo se cambian con usuario y contraseña.

## Publicarla en internet (gratis)

### 1. Base de datos (Supabase)
1. Entra a https://supabase.com y crea una cuenta (puedes usar tu GitHub).
2. **New project**: nombre `oee`, escribe una contraseña para la base de datos y **guárdala**.
3. Cuando el proyecto esté listo, pulsa **Connect** (arriba) y copia la dirección de
   **Transaction pooler**. Se ve así:
   `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-xx.pooler.supabase.com:6543/postgres`
4. Cambia `[YOUR-PASSWORD]` por la contraseña del paso 2. Esa dirección completa es tu `DATABASE_URL`.

### 2. Código (GitHub)
1. Entra a https://github.com y crea una cuenta.
2. **New repository**: nombre `oeehugorestrepo`, privado. Créalo.
3. **Add file → Upload files** y arrastra todo el contenido de esta carpeta
   (`app.py`, `requirements.txt`, `render.yaml`, la carpeta `static`). Pulsa **Commit changes**.

### 3. Servidor (Render)
1. Entra a https://render.com y crea la cuenta con **GitHub**.
2. **New → Blueprint** y elige el repositorio `oeehugorestrepo`.
3. Render lee `render.yaml` y te pide cuatro valores:
   - `GEMINI_API_KEY`: tu clave de Google AI Studio.
   - `DATABASE_URL`: la dirección de Supabase del paso 1.
   - `APP_USUARIO`: el usuario que usarán operarios y supervisores para entrar (por ejemplo `planta`).
   - `APP_CLAVE`: la contraseña de ese usuario. Elige una que no sea la de las velocidades.
4. Pulsa **Apply**. En unos minutos la app queda en
   **https://oeehugorestrepo.onrender.com** (si el nombre ya estuviera ocupado, Render agrega unas letras al final).

Las claves solo se escriben en Render; nunca van en el código ni en un chat.

## Uso
- **Entrar**: la app pide usuario y contraseña. Con `APP_USUARIO`/`APP_CLAVE` se usa todo menos cambiar
  velocidades; con el usuario de velocidades (1006) se puede todo. La sesión dura 30 días en cada equipo.
- **Cargar foto**: toma o sube la foto; se lee, se calcula y se guarda sola.
- **Tablero**: OEE de planta, por proceso, tendencia diaria y paradas por causa. Filtra por fechas.
- **Reportes**: historial; "Corregir datos" si algo se leyó mal.
- **Velocidades**: usuario y contraseña para cambiarlas (la verificación se hace en el servidor).
- **Descargar Excel**: datos y resumen en .xlsx.

## Notas
- En el plan gratuito, Render apaga la app tras 15 minutos sin uso; la primera visita después tarda ~50 s.
- Para cambiar el usuario o la contraseña general, edítalos en Render → tu servicio → Environment.
- Para cambiar el modelo de Gemini, agrega en Render la variable `GEMINI_MODEL`.
- Para probarla en tu PC: `pip install -r requirements.txt`, define `GEMINI_API_KEY` y ejecuta `python app.py`
  (sin `DATABASE_URL` guarda en un archivo local `oee.db`). Abre http://localhost:5000
