# Cómo construir un módulo para OLIMPO

Un módulo es una pestaña de la app (correo temporal, números SMS, o lo
que se te ocurra). Este documento es el contrato exacto que tiene que
cumplir un archivo `.py` para que OLIMPO lo acepte, lo cargue y lo
muestre como pestaña — y cómo usar el SDK (`sdk.py`) para cobrar
créditos, guardar datos y hacer llamadas HTTP sin reinventar nada.

Los dos módulos reales que ya usa OLIMPO (`modules/tempmail.py` y
`modules/smspool.py`) siguen exactamente este contrato — ante la duda,
mirá cómo lo resolvieron ellos.

---

## 1. Contrato obligatorio

Tu archivo tiene que definir:

```python
MODULE_ID   = "mimodulo"          # slug único, minúsculas y "_", sin espacios
MODULE_NAME = "🎲 Mi módulo"       # texto de la pestaña — empieza con un emoji

def render(user_id: int) -> None:
    """Dibuja la UI de tu pestaña. Se llama en cada rerun de Streamlit."""
    ...
```

Si falta `MODULE_ID`, `MODULE_NAME` o `render`, el módulo se rechaza —
no se registra, no se activa, no rompe nada del resto de la app.

### Opcionales

```python
MODULE_VERSION    = "1.0.0"   # default "?" si no lo ponés
MODULE_AUTHOR     = "Tu nombre"  # default "?"
MODULE_DATA_SCOPE = "per_user"   # "shared" | "per_user" | "own_db" — ver sección 4, es solo documentación

def render_admin(user_id: int) -> None:
    """Controles extra que aparecen en Admin > Gestión de módulos, dentro
    de un expander con el nombre de tu módulo. Para configuración global
    (tasas, límites, flags) — no para datos de un usuario puntual."""
    ...

def on_activar() -> None:
    """Se llama una vez cuando el módulo se activa (al instalarse por
    primera vez o al reactivarlo desde el panel). Usalo para crear tus
    propias tablas con CREATE TABLE IF NOT EXISTS — ver sección 4."""
    ...
```

### Ejemplo mínimo que ya es válido

```python
import streamlit as st

MODULE_ID = "saludo"
MODULE_NAME = "👋 Saludo"

def render(user_id: int) -> None:
    st.subheader(MODULE_NAME)
    st.write(f"Hola, usuario {user_id}")
```

Esto ya es un módulo aceptado: se puede subir tal cual desde Admin >
Gestión de módulos y aparece como pestaña.

---

## 2. Botones, formularios y estado

Streamlit comparte `st.session_state` entre **todas** las pestañas del
mismo usuario. Si dos módulos usan la misma `key` en un widget, o la
misma clave de `session_state`, se pisan entre sí y aparecen errores
raros de "widget duplicado".

Regla: **toda key lleva el prefijo de tu `MODULE_ID`.**

```python
if st.button("Comprar", key=f"{MODULE_ID}_comprar"):
    ...

st.session_state[f"{MODULE_ID}_pedido"] = {...}
```

Así se ven los dos módulos reales: `smspool.py` guarda el pedido activo
en `st.session_state[f"{MODULE_ID}_order"]` y cada botón lleva su
propia key (`f"{MODULE_ID}_revisar"`, `f"{MODULE_ID}_cancelar"`, etc.).

Para mostrar errores de red sin tumbar la pestaña, envolvé la llamada
externa con el helper del SDK:

```python
import sdk

with sdk.api_errors("No se pudo cargar la lista"):
    datos = mi_funcion_que_llama_a_una_api()
```

Si `mi_funcion_que_llama_a_una_api()` lanza una excepción, se loguea y
se muestra un `st.error(...)` — el resto de la pestaña sigue
funcionando. Es el mismo patrón que usa el resto de OLIMPO.

---

## 3. UI de paneles — qué podés y qué no podés tocar

- **Tu pestaña es 100% tuya.** Adentro de `render()` podés usar
  cualquier widget de Streamlit: botones, tabs, expanders, columnas,
  `st.file_uploader`, lo que necesites.
- **Admin es compartido.** No podés escribir directamente en la
  pestaña Admin. El único punto de extensión es `render_admin()`, que
  se muestra dentro de un `st.expander` propio, en la sección
  "Gestión de módulos" del panel Admin — no se mezcla con los
  controles de otros módulos ni con los del núcleo (usuarios, carrusel).
- **Inicio no se puede modificar** desde un módulo — es la pantalla de
  bienvenida del núcleo de OLIMPO.
- **No accedas a tablas de otro módulo.** Si tu módulo necesita saber
  el saldo de un usuario, usá `sdk.balance(user_id)` — nunca leas la
  tabla `creditos` a mano. Lo mismo para cualquier dato que no sea tuyo.

---

## 4. Datos — los 3 patrones de persistencia

Todo pasa por `sdk.py`, nunca importes `db.py` directamente desde un
módulo nuevo (los módulos internos históricos lo hacen porque son
anteriores al SDK, pero el patrón recomendado de acá en adelante es
`sdk.db_conn()`).

### a) Config compartida (igual para todos los usuarios)

Tasas, límites, flags de encendido/apagado — datos generales que no
son de un usuario en particular:

```python
tasa = sdk.get_config(MODULE_ID, "tasa_cambio", default="18.5")
sdk.set_config(MODULE_ID, "tasa_cambio", "19.2")
```

Guardalo/editalo típicamente desde `render_admin()`.

### b) Filas por usuario en la base compartida (el patrón más común)

Cuando cada usuario tiene algunas filas — pedidos, cuentas, historial
— pero no hace falta un archivo aparte. Creá tu propia tabla (prefijada
con tu `MODULE_ID`) en `on_activar()`:

```python
def on_activar() -> None:
    with sdk.db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mimodulo_pedidos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                detalle    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

def guardar_pedido(user_id: int, detalle: str) -> None:
    with sdk.db_conn() as conn:
        conn.execute(
            "INSERT INTO mimodulo_pedidos (user_id, detalle, created_at) VALUES (?, ?, datetime('now'))",
            (user_id, detalle),
        )
```

Así trabajan `tempmail_cuentas` y `olimpo_sms_orders` — una tabla
compartida con columna `user_id`.

### c) Base de datos propia por usuario (aislamiento total)

Para cuando un usuario acumula un dataset propio y pesado que no tiene
sentido mezclar en filas de una tabla compartida (por ejemplo, un
inventario, notas largas, un historial que cada uno administra a su
manera). El SDK te da un archivo SQLite exclusivo por usuario:

```python
with sdk.user_db(MODULE_ID, user_id) as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS notas (id INTEGER PRIMARY KEY, texto TEXT)")
    conn.execute("INSERT INTO notas (texto) VALUES (?)", (texto,))
```

Esto vive en `data/modulos/<MODULE_ID>/<user_id>.db`, fuera de git y
fuera de la base compartida — cada usuario es completamente
independiente. Usalo solo si de verdad necesitás ese aislamiento; para
la mayoría de los casos alcanza con el patrón (b).

---

## 5. Créditos — cobrar y reembolsar

Nunca importes `creditos.py` directamente. El SDK expone exactamente
lo que necesitás:

```python
sdk.balance(user_id) -> int
sdk.charge(user_id, amount, reason) -> bool   # False si no alcanza el saldo
sdk.refund(user_id, amount, reason) -> None
```

**Patrón recomendado: cobrar antes de la operación externa, reembolsar
si falla.** Así nunca queda un usuario cobrado sin haber recibido nada:

```python
if not sdk.charge(user_id, costo, f"Compra en {MODULE_NAME}"):
    st.error("No tienes créditos suficientes.")
    return

try:
    resultado = llamar_api_externa()
except Exception as exc:
    sdk.refund(user_id, costo, f"Reembolso — error: {exc}")
    st.error(f"Falló la compra, créditos devueltos. ({exc})")
    return

# éxito: guardar resultado, mostrar al usuario, etc.
```

`modules/smspool.py::render()` es el ejemplo real de este patrón
completo, incluyendo reembolso por cancelación del usuario y por
expiración sin código.

---

## 6. Proxies

Si tu módulo llama a una API externa que necesita pasar por un proxy
(geo-restricciones, IP fija, etc.), usá los helpers HTTP del SDK en vez
de `requests`/`aiohttp` directo:

```python
resp = sdk.http_get(MODULE_ID, "https://api.ejemplo.com/algo")
resp = sdk.http_post(MODULE_ID, "https://api.ejemplo.com/algo", json={"x": 1})
```

El proxy se configura por variable de entorno, sin tocar código:

- `OLIMPO_PROXY_<MODULE_ID EN MAYÚSCULAS>` — proxy específico de tu módulo.
- `OLIMPO_PROXY` — proxy genérico, usado si no hay uno específico.

Si tu módulo ya usa `aiohttp` directamente (como `tempmail.py` y
`smspool.py`, que son anteriores a este helper), podés seguir así,
pero entonces el manejo de proxy corre por tu cuenta (leer la env var
vos mismo y pasarla a la sesión de `aiohttp`).

---

## 7. Notificaciones — Telegram y auditoría

Además de mostrar cosas en la UI, tu módulo puede escribirle a un
usuario puntual por Telegram, y dejar un rastro de auditoría para los
admins. Los dos van por el bot de OLIMPO (`OLIMPO_BOT_TOKEN`), así que
no necesitás manejar ningún token vos mismo.

### DM a un usuario

```python
sdk.enviar_telegram(user_id, "📱 Tu número: <code>+52...</code>")
```

Úsalo para entregarle al usuario algo que no querés que dependa de que
tenga la pestaña del navegador abierta: el número asignado, el costo
cobrado, el código OTP cuando llega, la confirmación de un reembolso.
Si el envío falla (por ejemplo, nunca le escribió al bot) no rompe tu
módulo — se loguea y listo.

### Alerta de auditoría (a los admins)

```python
sdk.alertar(
    f"📱 SMS — nueva solicitud\n"
    f"👤 {user_id}\n"
    f"💳 {credits} crédito(s) cobrados\n"
    f"🎫 {order_id}"
)
```

Va a `OLIMPO_LOG_CHANNEL_ID` (un canal/grupo dedicado) si está
configurado, o por DM a todos los admins (`OLIMPO_ADMINS`) si no hay
canal. Es la prueba que evita discusiones más adelante — usala en
**todo evento que mueva créditos o que alguien pueda después negar**:

- Se cobró y se entregó un número → queda registrado qué se cobró y qué se entregó.
- Se cobró pero la API externa nunca entregó nada → queda registrado el reembolso automático (protege al usuario: "sí me reembolsaron").
- Llegó el código/resultado final → queda registrado con el dato real entregado (protege a OLIMPO: si el usuario dice "nunca me llegó" habiendo llegado, está el log).
- Cancelación manual del usuario → queda registrado que fue el usuario quien canceló, no un error del módulo.

`modules/smspool.py` implementa los cuatro casos (`_notificar_compra`,
`_notificar_reembolso`, `_notificar_codigo`) — es la referencia a
copiar para cualquier módulo que cobre créditos.

También se usa para intentos de acceso fallidos a la app en sí (ver
`app.py::_login_screen` — Telegram ID no autorizado, código OTP
incorrecto), aunque eso no es parte de ningún módulo puntual.

---

## 8. Cómo se gestiona un módulo (panel Admin)

Todo esto vive en **Admin > Gestión de módulos**:

- **Activar / Desactivar** — un módulo desactivado no aparece como
  pestaña para nadie, pero sus datos y su código quedan intactos.
- **Agregar módulo externo** — subís el archivo `.py` con un
  `st.file_uploader`, le ponés un `MODULE_ID`, y el sistema lo valida
  (que tenga `MODULE_ID`, `MODULE_NAME` y `render`) antes de guardarlo.
  Si no cumple el contrato, no se guarda nada. Vive en
  `external_modules/`, fuera de git — así se puede probar sin tocar el
  código versionado.
- **Hacer interno** — "gradúa" un módulo externo copiando su archivo a
  `modules/`, para que quede versionado como parte oficial de OLIMPO.
  Después de esto, alguien con acceso al repo tiene que hacer
  `git add modules/<id>.py` y commitear — el panel no hace commits.
- **Recargar** — reimporta el archivo del módulo sin reiniciar el
  proceso de Streamlit. Útil si subiste una versión nueva de un
  externo y querés que se refleje sin bajar la app.
- **Eliminar** — solo para externos (borra el archivo y el registro).
  Los internos no se eliminan desde el panel — son código del repo, se
  borran editándolo.

---

## 9. Checklist antes de subir tu módulo

- [ ] `MODULE_ID` es único, en minúsculas, sin espacios.
- [ ] `MODULE_NAME` empieza con un emoji (así se ve bien como pestaña).
- [ ] `render(user_id)` no lanza excepciones de control de flujo — solo
      errores reales (que quedan atrapados por `sdk.api_errors` o por
      el wrapper del panel, mostrando un mensaje en vez de romper la app).
- [ ] Todas las `key=` de tus widgets llevan el prefijo `MODULE_ID`.
- [ ] Todas las claves de `st.session_state` llevan el prefijo `MODULE_ID`.
- [ ] Si cobrás créditos: usás `sdk.charge`/`sdk.refund`, nunca tocás
      `creditos.py` directo, y reembolsás en cualquier camino de fallo
      (error de API, cancelación del usuario, expiración).
- [ ] Si cobrás créditos: cada cobro, reembolso y entrega de resultado
      final pasa por `sdk.alertar(...)` — sin eso, un reclamo del
      usuario no tiene forma de resolverse.
- [ ] Si guardás datos: elegiste el patrón correcto de la sección 4 y
      creás tus tablas en `on_activar()` (no asumís que ya existen).
- [ ] Si llamás una API externa: usás `sdk.http_get`/`sdk.http_post` (o
      manejás vos mismo el proxy si seguís con `aiohttp`).
- [ ] No leés ni escribís tablas de otro módulo ni del núcleo
      (`whitelist`, `creditos`, `carrusel`) directamente.
- [ ] Si agregás una sección a Admin, es vía `render_admin()`, no
      metida en otro lado.
