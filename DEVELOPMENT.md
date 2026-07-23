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

Abre `http://localhost:8501`. Como usas el bot real, los OTP llegan a tu
Telegram de verdad.

Para probar el bot de auth (`/start` mostrando el Telegram ID) hace falta
correrlo aparte, en otra terminal:

```bash
python bot_auth.py
```

## 4. Estructura del proyecto

| Archivo | Qué hace |
|---|---|
| `app.py` | UI de Streamlit: login, TempMail, SMS Pool, Admin |
| `auth.py` | OTP, whitelist, admins |
| `db.py` | Schema de SQLite e inicialización |
| `bot_auth.py` | Bot de Telegram que responde `/start` con el Telegram ID |
| `modules/tempmail.py` | Wrapper de api.mail.tm |
| `modules/smspool.py` | Wrapper de api.smspool.net |
| `modules/_template.py` | Plantilla para módulos nuevos |

## 5. Agregar un módulo nuevo

1. Copia `modules/_template.py` a `modules/<nombre>.py` y adapta las funciones.
2. Si necesita guardar datos, agrega una tabla en `db.py` (dentro de `SCHEMA`).
3. En `app.py`:
   - Importa el módulo: `from modules import <nombre>`
   - Escribe una función `_<nombre>_screen(user_id)` con la UI (mismo
     patrón que `_tempmail_screen` / `_sms_screen`)
   - Agrégala a la lista `opciones` del sidebar en `main()` y al
     `if/elif` que decide qué pantalla mostrar
4. Envuelve las llamadas a APIs externas con `_api_errors("mensaje")`
   (ya definido en `app.py`), para que un fallo de red muestre un error
   legible en vez de un traceback crudo.

## 6. Antes de subir un cambio

```bash
python -m py_compile app.py auth.py db.py bot_auth.py modules/*.py
```

No hay tests automatizados todavía — probá el flujo a mano en
`localhost:8501` antes de hacer push. Si el cambio toca `auth.py` o
`db.py`, entra como admin y confirma que el login y el panel de Admin
siguen funcionando.
