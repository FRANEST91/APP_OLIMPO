# Desarrollo local de OLIMPO

Guía rápida para correr la app en tu máquina y agregar módulos o pantallas
nuevas sin depender de Railway.

## 1. Requisitos

- Python 3.11
- El token real del bot OLIMPO (`OLIMPO_BOT_TOKEN`, el mismo que ya tienes en Railway)
- Tu Telegram ID en `OLIMPO_ADMINS` para poder ver la pestaña Admin

## 2. Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y completa al menos:

```
OLIMPO_BOT_TOKEN=...        # el mismo token real que usas en Railway
OLIMPO_ADMINS=6060544328    # tu Telegram ID, para ver la pestaña Admin
SMSPOOL_API_KEY=...
```

`OLIMPO_DB_PATH` es opcional: si no lo defines se crea `olimpo.db` en la
carpeta del proyecto — una base local separada de la de Railway, para
probar sin tocar producción ni a los usuarios reales.

`OLIMPO_LOG_CHANNEL_ID` es opcional: el chat/canal donde llegan las
alertas de auditoría (`sdk.alertar`, ver MODULOS.md) — cobros,
reembolsos, entregas de código, accesos fallidos. Si no lo defines, las
alertas se mandan por DM a todos los IDs en `OLIMPO_ADMINS`.

## 3. Correr la app

```bash
streamlit run app.py
```

Abre `http://localhost:8501`. El login manda un mensaje con botones a tu
Telegram real ("✅ Fui yo, entrar" / "🚫 No fui yo") en vez de un código
para tipear.

`bot_auth.py` ya no es opcional: es el proceso que escucha cuándo tocas
ese botón, sin él el login se queda esperando para siempre. Corrélo
aparte, en otra terminal, antes de intentar entrar a la app:

```bash
python bot_auth.py
```

Como admin (tu ID en `OLIMPO_ADMINS`) tenés dos comandos extra en el bot:

- `/admin` — lista las sesiones activas en este momento, con botones para
  cerrar cualquiera o revocar la membresía de esa cuenta.
- `/usuario <telegram_id>` — lo mismo para un usuario puntual, aunque no
  haya saltado ninguna alerta todavía.

Mandarle un archivo al bot como admin también funciona (evita depender del
selector de archivos del navegador, poco confiable con `.py`/`.csv` en
varios celulares): un `.py` se agrega como módulo externo, un `.csv` se
importa a la whitelist. El nombre del archivo define el ID del módulo
(minúsculas, solo letras/números/guion bajo).

Las alertas de seguridad (login rechazado, alguien reenvió el link de
acceso, cambio de IP en una sesión activa) también traen esos mismos
botones pegados, para actuar directo desde la alerta.

### Anuncios, páginas y orden de pestañas

- **Anuncios**: si definís `OLIMPO_ANNOUNCE_CHANNEL_ID` en `.env` (ID
  numérico o `@usuario` de un canal de Telegram donde el bot sea
  **administrador**), sus posts de texto aparecen en la pestaña
  "Anuncios", con un refresco automático cada 5 segundos que no
  interrumpe a nadie usando otra pestaña (`st.fragment`).
- **Páginas**: desde Admin > Páginas informativas se crean pestañas de
  contenido estático (markdown) — reglas, FAQ, lo que haga falta.
- **Orden de pestañas**: también desde Admin, se puede reordenar
  cualquier pestaña (Inicio, cada módulo, Anuncios, cada página, Admin)
  y sobreescribir su nombre/emoji, sin tocar código.

## 4. Estructura del proyecto

| Archivo | Qué hace |
|---|---|
| `app.py` | UI de Streamlit: login, tabs de módulos/páginas/anuncios, Admin |
| `auth.py` | Solicitudes de login por botón, sesiones, whitelist, admins |
| `db.py` | Schema de SQLite e inicialización |
| `bot_auth.py` | Bot de Telegram: `/start`, confirmación de login, panel de moderación (`/admin`, `/usuario`), espejo del canal de anuncios |
| `canal.py` | Mensajes espejados del canal de Telegram (Anuncios) |
| `paginas.py` | Páginas de contenido estático (pestañas informativas) |
| `pestanas.py` | Orden/nombre/emoji configurable de cada pestaña |
| `modules/tempmail.py` | Wrapper de api.mail.tm |
| `modules/smspool.py` | Wrapper de api.smspool.net |
| `modules/_template.py` | Plantilla para módulos nuevos |

## 5. Agregar un módulo nuevo

1. Copia `modules/_template.py` a `modules/<nombre>.py` y adapta las funciones.
2. Si necesita guardar datos, agrega una tabla en `db.py` (dentro de `SCHEMA`).
3. Definí `MODULE_ID`, `MODULE_NAME` y `render(user_id)` (ver
   MODULOS.md) — `sdk.descubrir_e_instalar()` lo detecta y registra solo
   al arrancar la app, no hace falta tocar `app.py` para nada. La pestaña
   nueva aparece sola (se puede reordenar después desde Admin).
4. Envolvé las llamadas a APIs externas con `sdk.api_errors("mensaje")`,
   para que un fallo de red muestre un error legible en vez de un
   traceback crudo.

## 6. Antes de subir un cambio

```bash
python -m py_compile app.py auth.py db.py bot_auth.py canal.py paginas.py pestanas.py modules/*.py
```

No hay tests automatizados todavía — probá el flujo a mano en
`localhost:8501` antes de hacer push. Si el cambio toca `auth.py` o
`db.py`, entra como admin y confirma que el login y el panel de Admin
siguen funcionando.
