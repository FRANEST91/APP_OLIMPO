import asyncio
import base64
import csv
import html
import io
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

# Debe cargarse antes de importar auth/db: leen variables de entorno al
# importarse. En Railway no hace nada (no hay .env, ya vienen del entorno).
load_dotenv()

import streamlit as st
from telegram import Bot

import auth
import canal
import carrusel
import creditos
import db
import paginas
import pestanas
import sdk

st.set_page_config(page_title="OLIMPO", page_icon="🔥", layout="centered")

# Streamlit no tiene un modo "tabs abajo" nativo — esto reposiciona el
# tablist real (data-testid="stTabs") como una barra de navegación fija al
# pie, estilo app mobile, y anima el emoji de fuego del título.
#
# Se probó primero reforzar el position:fixed con JS (MutationObserver +
# reaplicar estilos), porque el CSS solo a veces perdía la pulseada de
# especificidad contra el estilo propio de Streamlit tras un rerender.
# Se descartó: bajo ciertas combinaciones de mutaciones esa solución podía
# entrar en un bucle que colgaba la pestaña — un riesgo inaceptable frente
# a, en el peor caso, que la barra tarde un instante en reposicionarse.
# Por eso queda solo CSS con !important, que en las pruebas se sostuvo
# bien en el uso normal (cambiar de pestaña, scrollear contenido largo).
# Probado contra el DOM real de streamlit==1.60.0; si se actualiza
# Streamlit y esto deja de verse bien, lo primero a revisar son los
# data-testid, que podrían renombrarse entre versiones.
st.markdown(
    """
    <style>
    @keyframes olimpo-flame-flicker {
        0%   { transform: scale(1) rotate(-3deg); filter: brightness(1) drop-shadow(0 0 4px #FF6030); }
        20%  { transform: scale(1.08) rotate(2deg); filter: brightness(1.2) drop-shadow(0 0 8px #FF6030); }
        40%  { transform: scale(0.95) rotate(-2deg); filter: brightness(0.9) drop-shadow(0 0 3px #FF6030); }
        60%  { transform: scale(1.05) rotate(3deg); filter: brightness(1.15) drop-shadow(0 0 7px #FF6030); }
        80%  { transform: scale(0.98) rotate(-1deg); filter: brightness(1.05) drop-shadow(0 0 5px #FF6030); }
        100% { transform: scale(1) rotate(-3deg); filter: brightness(1) drop-shadow(0 0 4px #FF6030); }
    }
    .olimpo-flame {
        display: inline-block;
        animation: olimpo-flame-flicker 1.6s ease-in-out infinite;
        transform-origin: 50% 90%;
    }

    /* Menos margen arriba del todo y lugar abajo para la barra fija */
    div[data-testid="stMainBlockContainer"] {
        padding-top: 1.2rem;
        padding-bottom: 5.5rem;
    }
    div[data-testid="stHeader"] { height: 0; }

    /* --- Tabs como barra de navegación inferior fija --- */
    div[data-testid="stTabs"] div[role="tablist"] {
        position: fixed !important;
        bottom: 0 !important;
        left: 0 !important;
        right: 0 !important;
        top: auto !important;
        z-index: 999;
        background: #0E0600;
        border-top: 1px solid #6B1800;
        display: flex !important;
        justify-content: space-around;
        padding: 8px 4px calc(8px + env(safe-area-inset-bottom));
        gap: 0;
        overflow: visible !important;
        margin: 0 !important;
    }
    div[data-testid="stTabs"] div[role="tablist"] [data-testid="stTab"] {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 2px;
        background: transparent !important;
        border: none !important;
        padding: 4px 2px !important;
    }
    div[data-testid="stTabs"] div[role="tablist"] [data-testid="stTab"] p {
        white-space: pre-line;
        text-align: center;
        font-size: 0.68rem;
        line-height: 1.15;
        margin: 0;
        color: #8A6A50;
    }
    div[data-testid="stTabs"] div[role="tablist"] [data-testid="stTab"][aria-selected="true"] p {
        color: #FF6030;
        font-weight: 700;
    }
    div[data-testid="stTabs"] .react-aria-SelectionIndicator {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.app")

SESSION_TTL_SECONDS = 60 * 60
BANNER_PATH = Path(__file__).parent / "assets" / "banner.jpg"

db.init_db()
sdk.descubrir_e_instalar()


async def _crear_solicitud_login(tg_id: int) -> str:
    # Bot debe usarse como context manager: si no, el cliente HTTP interno
    # nunca se cierra y cada login deja una conexión abierta (leak).
    async with Bot(token=os.environ["OLIMPO_BOT_TOKEN"]) as bot:
        return await auth.crear_solicitud(tg_id, bot)


async def _notificar_moderacion(tg_id: int, mensaje: str) -> None:
    async with Bot(token=os.environ["OLIMPO_BOT_TOKEN"]) as bot:
        await auth.alertar_moderacion(bot, tg_id, mensaje)


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def _api_errors(mensaje: str):
    try:
        yield
    except Exception:
        logger.exception(mensaje)
        st.error(f"{mensaje}. Intenta de nuevo en un momento.")


def _client_ip() -> str | None:
    # nginx pasa la IP real en X-Forwarded-For (ver deploy/olimpo.nginx.conf)
    # — sin proxy delante (dev local), Streamlit no expone la IP del cliente
    # de otra forma más confiable que esta.
    try:
        headers = st.context.headers
    except Exception:
        return None
    if not headers:
        return None
    forwarded = headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return headers.get("X-Real-IP") or None


def _logged_in() -> bool:
    expires_at = st.session_state.get("session_expires_at")
    if not (expires_at and time.time() < expires_at):
        return False

    tg_id = st.session_state.get("tg_id")
    session_id = st.session_state.get("session_id")
    if not tg_id or not session_id or not auth.sesion_vigente(tg_id, session_id):
        # Otra sesión de la misma cuenta la reemplazó, o un admin la cerró
        # a mano desde el bot — no seguimos con una sesión que ya no vale.
        # Sin este mensaje, la pestaña vuelve al login sin ninguna
        # explicación y parece que la app falló.
        motivo = (
            "Tu sesión se cerró — iniciaste sesión desde otro lugar, o un "
            "admin la cerró. Vuelve a entrar."
            if tg_id else None
        )
        st.session_state.clear()
        if motivo:
            st.session_state["_logout_reason"] = motivo
        return False

    ip_actual = _client_ip()
    if ip_actual and auth.registrar_ip_si_cambio(tg_id, session_id, ip_actual):
        try:
            _run(_notificar_moderacion(
                tg_id,
                "🌐 <b>Cambio de IP en una sesión activa</b>\n"
                f"👤 <code>{tg_id}</code>\n"
                f"Nueva IP: <code>{ip_actual}</code>\n"
                "Si no sos vos en otra red, revisa la cuenta."
            ))
        except Exception:
            logger.exception("No se pudo avisar del cambio de IP")

    return True


def _login_screen() -> None:
    motivo_logout = st.session_state.pop("_logout_reason", None)
    if motivo_logout:
        st.warning(motivo_logout)

    st.markdown("## 🔥 OLIMPO")
    st.caption("Ingresa tu Telegram ID para continuar")

    stage = st.session_state.get("login_stage", "id")

    if stage == "id":
        tg_id_input = st.text_input("Telegram ID", key="tg_id_input")
        if st.button("Continuar", type="primary"):
            if not tg_id_input.strip().isdigit():
                st.error("Ingresa un Telegram ID numérico válido.")
                return
            tg_id = int(tg_id_input.strip())
            if not auth.is_whitelisted(tg_id):
                sdk.alertar(
                    f"🔒 <b>Acceso denegado</b>\n"
                    f"👤 <code>{tg_id}</code> intentó entrar sin estar en la whitelist."
                )
                st.error("Todavía no tienes acceso a Olimpo. Escríbele al bot para más info.")
                return
            try:
                token = _run(_crear_solicitud_login(tg_id))
            except auth.LoginCooldown:
                # Ya hay una solicitud vigente para este tg_id (pedida hace
                # poco) — no hace falta mandar otra, seguimos esperando la
                # que ya está pendiente en Telegram.
                token = st.session_state.get("login_token")
                if not token:
                    st.error("Ya te mandamos una solicitud hace un momento. Espera unos segundos.")
                    return
            except Exception:
                logger.exception("No se pudo enviar la solicitud de acceso")
                st.error("No pudimos enviarte la solicitud. Intenta de nuevo en un momento.")
                return
            st.session_state["login_token"] = token
            st.session_state["pending_tg_id"] = tg_id
            st.session_state["login_stage"] = "esperando"
            st.rerun()

    elif stage == "esperando":
        st.info(
            "Abre Telegram y toca **✅ Fui yo, entrar** en el mensaje que te "
            "mandamos. Esta pantalla se actualiza sola."
        )
        token = st.session_state.get("login_token")
        estado = auth.estado_solicitud(token) if token else "not_found"

        if estado == "confirmed":
            tg_id = st.session_state.pop("pending_tg_id")
            session_id = secrets.token_urlsafe(16)
            habia_otra = auth.abrir_sesion(tg_id, session_id, _client_ip(), SESSION_TTL_SECONDS)

            st.session_state["session_expires_at"] = time.time() + SESSION_TTL_SECONDS
            st.session_state["tg_id"] = tg_id
            st.session_state["session_id"] = session_id
            st.session_state.pop("login_stage", None)
            st.session_state.pop("login_token", None)

            if habia_otra:
                # Una sola sesión activa por cuenta: si había otra vigente,
                # se reemplaza — pero que quede visible para el dueño real.
                try:
                    _run(_notificar_moderacion(
                        tg_id,
                        "🔁 <b>Se cerró tu otra sesión</b>\n"
                        "Iniciaste sesión desde otro lugar — la sesión anterior de esta cuenta quedó cerrada."
                    ))
                except Exception:
                    logger.exception("No se pudo avisar del cierre de la sesión anterior")

            st.rerun()
            return

        if estado in ("denied", "expired", "not_found"):
            st.session_state.pop("login_stage", None)
            st.session_state.pop("login_token", None)
            st.session_state.pop("pending_tg_id", None)
            if estado == "denied":
                st.error("Se rechazó el acceso desde Telegram.")
            else:
                st.error("La solicitud venció. Vuelve a intentar.")
            return

        if st.button("Cancelar"):
            st.session_state.pop("login_stage", None)
            st.session_state.pop("login_token", None)
            st.session_state.pop("pending_tg_id", None)
            st.rerun()
            return

        time.sleep(2)
        st.rerun()


def _modulos_admin_screen(user_id: int) -> None:
    st.markdown("**Gestión de módulos**")
    st.caption(
        "Cada pestaña de usuario (aparte de Inicio y Admin) es un módulo. "
        "Ver MODULOS.md para la guía de cómo construir uno nuevo."
    )

    modulos = sdk.listar_modulos()
    activos_cargados = {f["module_id"]: mo for f, mo in sdk.modulos_activos()}
    for m in modulos:
        with st.container(border=True):
            col_info, col_origen, col_toggle = st.columns([3, 1, 1])
            col_info.markdown(f"**{m['nombre']}** `{m['module_id']}` · v{m['version']} · {m['autor']}")
            col_origen.caption("🌐 externo" if m["origen"] == "externo" else "📦 interno")
            etiqueta = "Desactivar" if m["activo"] else "Activar"
            if col_toggle.button(etiqueta, key=f"admin_mod_toggle_{m['module_id']}", width="stretch"):
                with _api_errors("No se pudo actualizar el módulo"):
                    if m["activo"]:
                        sdk.desactivar(m["module_id"])
                    else:
                        sdk.activar(m["module_id"])
                    st.rerun()

            if m["origen"] == "externo":
                col_a, col_b, col_c = st.columns(3)
                if col_a.button("Hacer interno", key=f"admin_mod_internar_{m['module_id']}", width="stretch"):
                    with _api_errors("No se pudo internar el módulo"):
                        sdk.hacer_interno(m["module_id"])
                        st.success(f"{m['nombre']} ahora es interno. Falta commitear modules/{m['module_id']}.py.")
                        st.rerun()
                if col_b.button("Recargar", key=f"admin_mod_recargar_{m['module_id']}", width="stretch"):
                    sdk.recargar(m["module_id"])
                    st.rerun()
                if col_c.button("Eliminar", key=f"admin_mod_eliminar_{m['module_id']}", width="stretch"):
                    with _api_errors("No se pudo eliminar el módulo"):
                        sdk.eliminar(m["module_id"])
                        st.rerun()
            elif st.button("Recargar", key=f"admin_mod_recargar_{m['module_id']}"):
                sdk.recargar(m["module_id"])
                st.rerun()

            mod = activos_cargados.get(m["module_id"])
            render_admin = getattr(mod, "render_admin", None) if mod else None
            if callable(render_admin):
                with st.expander(f"Configuración de {m['nombre']}"):
                    with _api_errors(f"Error en la configuración de {m['nombre']}"):
                        render_admin(user_id)

            with st.expander(f"Datos de {m['nombre']}"):
                st.caption(
                    "Archivos de referencia propios del módulo (por ejemplo un .db que "
                    "solo consulta) — sdk.module_dir() en MODULOS.md."
                )
                archivos = sdk.listar_archivos_modulo(m["module_id"])
                if not archivos:
                    st.caption("Sin archivos todavía.")
                for nombre_archivo in archivos:
                    col_nombre, col_borrar = st.columns([3, 1])
                    col_nombre.text(nombre_archivo)
                    if col_borrar.button("Eliminar", key=f"admin_mod_data_del_{m['module_id']}_{nombre_archivo}"):
                        sdk.eliminar_archivo_modulo(m["module_id"], nombre_archivo)
                        st.rerun()
                nuevo_archivo = st.file_uploader(
                    "Agregar archivo", key=f"admin_mod_data_upload_{m['module_id']}",
                )
                if nuevo_archivo is not None and st.button(
                    "Guardar archivo", key=f"admin_mod_data_guardar_{m['module_id']}",
                ):
                    with _api_errors("No se pudo guardar el archivo"):
                        sdk.guardar_archivo_modulo(m["module_id"], nuevo_archivo.name, nuevo_archivo.getvalue())
                        st.success(f"'{nuevo_archivo.name}' guardado.")
                        st.rerun()

    st.divider()
    st.markdown("**Agregar módulo externo**")
    st.caption(
        "Subí un archivo .py que cumpla el contrato de MODULOS.md (MODULE_ID, "
        "MODULE_NAME, render()). Se valida antes de activarlo — si falta algo "
        "requerido, no se guarda nada. Alternativa sin pasar por acá: subir el "
        ".py directo a la carpeta modules/ del repo por GitHub — se registra solo "
        "como interno la próxima vez que arranque la app."
    )
    archivo_mod = st.file_uploader("Archivo del módulo (.py)", type="py", key="admin_mod_upload")
    if archivo_mod is not None and st.button("Agregar módulo"):
        try:
            module_id_real = sdk.registrar_externo(archivo_mod.getvalue())
        except Exception as exc:
            logger.exception("No se pudo agregar el módulo desde %s", archivo_mod.name)
            st.error(f"No se pudo agregar el módulo: {exc}")
        else:
            st.success(f"Módulo '{module_id_real}' agregado como externo.")
            st.rerun()

    st.caption(
        "El ID del módulo lo define el propio archivo (MODULE_ID) — no hace falta "
        "escribirlo acá. ¿El selector de archivos no te deja elegir el .py? Abrilo "
        "con cualquier app de texto, copiá todo el código y pegalo acá abajo."
    )
    codigo_pegado = st.text_area("Pegar código del módulo", key="admin_mod_texto", height=150)
    if codigo_pegado.strip() and st.button("Agregar módulo desde texto pegado"):
        try:
            module_id_real = sdk.registrar_externo(codigo_pegado.encode("utf-8"))
        except Exception as exc:
            logger.exception("No se pudo agregar el módulo desde texto pegado")
            st.error(f"No se pudo agregar el módulo: {exc}")
        else:
            st.success(f"Módulo '{module_id_real}' agregado como externo.")
            st.rerun()


def _bases_compartidas_admin_screen(user_id: int) -> None:
    st.markdown("**Bases de datos compartidas**")
    st.caption(
        "Subí acá una base SQLite (por ejemplo, usuarios activos traídos de otro "
        "sistema). Queda disponible en modo solo lectura para cualquier módulo, "
        "vía sdk.bd_compartida(nombre) — ver MODULOS.md."
    )

    for nombre in sdk.listar_bd_compartidas():
        with st.container(border=True):
            col_nombre, col_borrar = st.columns([3, 1])
            col_nombre.markdown(f"**{nombre}**")
            if col_borrar.button("Eliminar", key=f"admin_bd_del_{nombre}"):
                sdk.eliminar_bd_compartida(nombre)
                st.rerun()
            with _api_errors("No se pudo leer la base de datos"):
                tablas = sdk.inspeccionar_bd_compartida(nombre)
                if tablas:
                    for t in tablas:
                        st.text(f"{t['tabla']} · {t['filas']} fila(s)")
                else:
                    st.caption("Sin tablas.")

    archivo_bd = st.file_uploader("Archivo .db", type=["db", "sqlite", "sqlite3"], key="admin_bd_upload")
    nombre_bd = st.text_input(
        "Nombre para guardarla", value=archivo_bd.name if archivo_bd else "", key="admin_bd_nombre",
    )
    if archivo_bd is not None and st.button("Subir base de datos"):
        with _api_errors("No se pudo guardar la base de datos"):
            sdk.registrar_bd_compartida(nombre_bd.strip() or archivo_bd.name, archivo_bd.getvalue())
            st.success("Base de datos guardada.")
            st.rerun()


def _admin_screen(user_id: int) -> None:
    st.subheader("🛡️ Administrar accesos")

    st.markdown("**Importar CSV**")
    st.caption("Columnas esperadas: tg_id, username (opcional), active (opcional)")
    archivo = st.file_uploader("Archivo CSV", type="csv", key="admin_csv")
    if archivo is not None and st.button("Importar archivo"):
        with _api_errors("No se pudo importar el CSV"):
            contenido = archivo.getvalue().decode("utf-8")
            filas = list(csv.DictReader(io.StringIO(contenido)))
            importados, omitidos = auth.import_csv(filas, user_id)
            st.success(f"Importados: {importados} · Omitidos: {omitidos}")
            st.rerun()

    st.caption(
        "¿El selector de archivos no te deja elegir el CSV? Abrilo con cualquier "
        "app de texto, copiá todo el contenido (incluida la primera línea con "
        "los nombres de columna) y pegalo acá abajo."
    )
    texto_csv = st.text_area("Pegar contenido del CSV", key="admin_csv_texto", height=150)
    if texto_csv.strip() and st.button("Importar texto pegado"):
        with _api_errors("No se pudo importar el CSV pegado"):
            filas = list(csv.DictReader(io.StringIO(texto_csv)))
            importados, omitidos = auth.import_csv(filas, user_id)
            st.success(f"Importados: {importados} · Omitidos: {omitidos}")
            st.rerun()

    st.divider()
    st.markdown("**Agregar usuario manual**")
    nuevo_id = st.text_input("Telegram ID", key="admin_new_id")
    nuevo_username = st.text_input("Username (opcional)", key="admin_new_username")
    if st.button("Agregar usuario"):
        if not nuevo_id.strip().isdigit():
            st.error("Ingresa un Telegram ID numérico válido.")
        else:
            with _api_errors("No se pudo agregar el usuario"):
                auth.add_user(int(nuevo_id.strip()), nuevo_username.strip() or None, user_id)
                st.success("Usuario agregado.")
                st.rerun()

    st.divider()

    filtro = st.text_input("Buscar (ID o username)", key="admin_filter")
    usuarios = auth.list_users()
    if filtro.strip():
        f = filtro.strip().lower()
        usuarios = [
            u for u in usuarios
            if f in str(u["tg_id"]) or f in (u["username"] or "").lower()
        ]

    st.caption(f"{len(usuarios)} usuario(s)")
    for u in usuarios:
        col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
        col1.text(str(u["tg_id"]))
        col2.text(u["username"] or "—")
        col3.text("✅" if u["active"] else "❌")
        accion = "Eliminar" if u["active"] else "Reactivar"
        if col4.button(accion, key=f"toggle_{u['tg_id']}"):
            with _api_errors("No se pudo actualizar el usuario"):
                if u["active"]:
                    auth.remove_user(u["tg_id"])
                else:
                    auth.add_user(u["tg_id"], u["username"], user_id)
                st.rerun()

    st.divider()
    st.markdown("**Créditos (los cobra cada módulo según su propia lógica)**")

    col_cred_id, col_cred_cant = st.columns(2)
    cred_id = col_cred_id.text_input("Telegram ID", key="admin_cred_id")
    cred_cant = col_cred_cant.number_input(
        "Créditos a agregar", min_value=1, value=10, key="admin_cred_cant"
    )
    if st.button("Asignar créditos"):
        if not cred_id.strip().isdigit():
            st.error("Ingresa un Telegram ID numérico válido.")
        else:
            with _api_errors("No se pudieron asignar los créditos"):
                creditos.asignar(int(cred_id.strip()), int(cred_cant))
                st.success("Créditos asignados.")
                st.rerun()

    st.caption("Saldos actuales")
    for row in creditos.listar_saldos():
        st.text(f"{row['tg_id']} · {row['username'] or '—'} · {row['saldo']} créditos")

    st.divider()
    st.markdown("**Banner rotativo (Inicio)**")
    st.caption("Formatos: PNG, JPEG, GIF")

    nuevas = st.file_uploader(
        "Agregar imágenes",
        type=["png", "jpg", "jpeg", "gif"],
        accept_multiple_files=True,
        key="admin_carrusel_upload",
    )
    duracion_seg = st.number_input(
        "Segundos en pantalla (para las que agregues ahora)",
        min_value=1, max_value=60, value=4, key="admin_carrusel_duracion",
    )
    if nuevas and st.button("Agregar al carrusel"):
        with _api_errors("No se pudo agregar la imagen"):
            for archivo in nuevas:
                carrusel.agregar_imagen(
                    archivo.name, archivo.getvalue(), archivo.type, int(duracion_seg * 1000)
                )
            st.success(f"{len(nuevas)} imagen(es) agregada(s).")
            st.rerun()

    imagenes = carrusel.listar_imagenes(solo_activas=False)
    st.caption(f"{len(imagenes)} imagen(es) en el carrusel")
    for img in imagenes:
        with st.container(border=True):
            col_img, col_datos = st.columns([1, 3])
            col_img.image(bytes(img["contenido"]), width=80)
            with col_datos:
                st.text(img["nombre"])
                nueva_dur = st.number_input(
                    "Segundos en pantalla", min_value=1, max_value=60,
                    value=img["duracion_ms"] // 1000, key=f"dur_{img['id']}",
                )
                if nueva_dur * 1000 != img["duracion_ms"]:
                    carrusel.actualizar_duracion(img["id"], int(nueva_dur * 1000))

            nuevo_arriba = st.text_input(
                "Texto arriba de la imagen", value=img["texto_arriba"] or "",
                key=f"arriba_{img['id']}",
            )
            nuevo_abajo = st.text_input(
                "Texto abajo de la imagen", value=img["texto_abajo"] or "",
                key=f"abajo_{img['id']}",
            )
            if nuevo_arriba != (img["texto_arriba"] or "") or nuevo_abajo != (img["texto_abajo"] or ""):
                carrusel.actualizar_texto(img["id"], nuevo_arriba or None, nuevo_abajo or None)

            col_a, col_b = st.columns(2)
            accion_img = "Ocultar" if img["active"] else "Mostrar"
            if col_a.button(accion_img, key=f"toggle_img_{img['id']}", width="stretch"):
                carrusel.toggle_activo(img["id"], not img["active"])
                st.rerun()
            if col_b.button("Eliminar", key=f"del_img_{img['id']}", width="stretch"):
                carrusel.eliminar_imagen(img["id"])
                st.rerun()

    st.divider()
    st.markdown("**Sonido de éxito**")
    st.caption(
        "Se reproduce cuando un módulo señala que algo salió bien (código "
        "SMS recibido, correo nuevo, etc — sdk.sonido_exito() en MODULOS.md)."
    )
    sonido_actual, sonido_mime = sdk.get_sonido_exito()
    st.audio(sonido_actual, format=sonido_mime)

    nuevo_sonido = st.file_uploader(
        "Reemplazar sonido", type=["mp3", "wav", "ogg"], key="admin_sonido_upload",
    )
    col_guardar, col_restablecer = st.columns(2)
    if nuevo_sonido is not None and col_guardar.button("Guardar sonido", width="stretch"):
        with _api_errors("No se pudo guardar el sonido"):
            sdk.set_sonido_exito(nuevo_sonido.getvalue(), nuevo_sonido.type)
            st.success("Sonido actualizado.")
            st.rerun()
    if col_restablecer.button("Restablecer al de fábrica", width="stretch"):
        sdk.restablecer_sonido_exito()
        st.success("Sonido restablecido.")
        st.rerun()

    st.divider()
    _bases_compartidas_admin_screen(user_id)

    st.divider()
    _modulos_admin_screen(user_id)


def _carrusel_html(imagenes: list) -> str:
    slides_html = []
    duraciones = []
    for img in imagenes:
        b64 = base64.b64encode(bytes(img["contenido"])).decode()
        src = f"data:{img['mime_type']};base64,{b64}"
        arriba = html.escape(img["texto_arriba"] or "")
        abajo = html.escape(img["texto_abajo"] or "")
        texto_estilo = (
            "font-family:ui-monospace,monospace; font-size:.85rem; color:#D4B89A;"
        )
        slides_html.append(f"""
        <div style="flex:0 0 100%; box-sizing:border-box; padding:0 6px; text-align:center;">
          {f'<div style="{texto_estilo} margin-bottom:8px;">{arriba}</div>' if arriba else ''}
          <img src="{src}" style="max-width:100%; max-height:240px; border-radius:8px; display:block; margin:0 auto;" />
          {f'<div style="{texto_estilo} margin-top:8px;">{abajo}</div>' if abajo else ''}
        </div>
        """)
        duraciones.append(img["duracion_ms"])

    duraciones_json = json.dumps(duraciones)
    return f"""
    <div style="overflow:hidden; width:100%;">
      <div id="olimpo-track" style="display:flex; transition: transform .7s ease-in-out;">
        {''.join(slides_html)}
      </div>
    </div>
    <script>
      const duraciones = {duraciones_json};
      let idx = 0;
      const track = document.getElementById('olimpo-track');
      function avanzar() {{
        if (!duraciones.length) return;
        track.style.transform = 'translateX(-' + (idx * 100) + '%)';
        const espera = duraciones[idx] || 4000;
        idx = (idx + 1) % duraciones.length;
        setTimeout(avanzar, espera);
      }}
      avanzar();
    </script>
    """


def _home_screen() -> None:
    imagenes_carrusel = carrusel.listar_imagenes()
    if imagenes_carrusel:
        st.iframe(_carrusel_html(imagenes_carrusel), height=300)
    elif BANNER_PATH.exists():
        st.image(str(BANNER_PATH), width="stretch")
    st.markdown(
        """
        <div style="text-align:center; padding: 16px 10px 24px;">
          <div style="font-family: ui-monospace, 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
                      font-weight:900; font-size:2.8rem; letter-spacing:.15em;
                      text-transform:uppercase; color:#FF6030; line-height:1;
                      text-shadow: 0 0 8px #D42000, 0 0 20px #FF6030, 0 0 50px rgba(212,32,0,.4);">
            OLIMPO
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.fragment(run_every="5s")
def _anuncios_fragment() -> None:
    # Fragmento aislado: se refresca solo (cada 5s) sin re-ejecutar el
    # resto de la app — así no interrumpe a alguien completando una
    # compra de SMS o escribiendo en otra pestaña.
    mensajes = canal.listar_mensajes()
    if not mensajes:
        st.caption("Todavía no hay anuncios.")
        return
    for m in mensajes:
        with st.container(border=True):
            # st.text y no st.write: un post de texto plano con un guion
            # bajo o un asterisco suelto ("reunión_importante") no debería
            # interpretarse como sintaxis Markdown.
            st.text(m["texto"])
            st.caption(m["posted_at"])


def _anuncios_screen() -> None:
    st.subheader("📢 Anuncios")
    _anuncios_fragment()


def _pagina_screen(pagina: dict) -> None:
    st.markdown(pagina["contenido"])


def _icono_y_texto(nombre: str, emoji: str) -> str:
    """Arma la etiqueta de una pestaña con el emoji en su propia línea —
    la barra de navegación inferior (CSS de arriba) fuerza el salto de
    línea para que quede ícono arriba, texto abajo, como una app mobile."""
    if emoji:
        return f"{emoji}\n{nombre}".strip()
    # Los módulos ya traen su propio emoji embebido en el nombre (p.ej.
    # "📱 Números SMS", ver MODULE_NAME en MODULOS.md) — se separa del
    # resto por la primera palabra no-ascii.
    partes = nombre.split(" ", 1)
    if len(partes) == 2 and not partes[0].isascii():
        return f"{partes[0]}\n{partes[1]}"
    return nombre


def _tabs_meta(user_id: int, modulos_activos: list) -> list[dict]:
    """Metadata de fábrica de cada pestaña candidata: key estable, nombre,
    emoji y un orden por defecto. No incluye la función que la renderiza —
    eso lo resuelve main() por separado, así esta lista también sirve para
    armar el editor de orden en Admin sin tener que instanciar nada."""
    metas = [{"key": "inicio", "nombre": "Inicio", "emoji": "🏠", "orden": 0}]
    for i, (fila, _mod) in enumerate(modulos_activos):
        metas.append({
            "key": f"modulo:{fila['module_id']}",
            "nombre": fila["nombre"], "emoji": "", "orden": 10 + i,
        })
    metas.append({"key": "anuncios", "nombre": "Anuncios", "emoji": "📢", "orden": 100})
    for p in paginas.listar_paginas():
        metas.append({
            "key": f"pagina:{p['id']}",
            "nombre": p["titulo"], "emoji": p["emoji"], "orden": 200 + p["id"],
        })
    if auth.is_admin(user_id):
        metas.append({"key": "admin", "nombre": "Admin", "emoji": "🛡️", "orden": 999})
    return metas


def _aplicar_config_pestanas(metas: list[dict]) -> list[dict]:
    config = pestanas.get_all_config()
    resultado = []
    for m in metas:
        ov = config.get(m["key"], {})
        resultado.append({
            **m,
            "nombre": ov.get("nombre") or m["nombre"],
            "emoji": ov.get("emoji") if ov.get("emoji") else m["emoji"],
            "orden": ov["orden"] if ov.get("orden") is not None else m["orden"],
        })
    resultado.sort(key=lambda m: m["orden"])
    return resultado


def _pestanas_admin_screen(user_id: int, modulos_activos: list) -> None:
    st.markdown("**Páginas informativas**")
    st.caption("Pestañas de contenido estático (reglas, FAQ, lo que necesites).")

    for p in paginas.listar_paginas(solo_activas=False):
        with st.container(border=True):
            col_emoji, col_titulo = st.columns([1, 4])
            nuevo_emoji = col_emoji.text_input(
                "Emoji", value=p["emoji"], key=f"pag_emoji_{p['id']}", max_chars=4,
            )
            nuevo_titulo = col_titulo.text_input(
                "Título", value=p["titulo"], key=f"pag_titulo_{p['id']}",
            )
            nuevo_contenido = st.text_area(
                "Contenido (markdown)", value=p["contenido"],
                key=f"pag_contenido_{p['id']}", height=150,
            )
            if (nuevo_emoji, nuevo_titulo, nuevo_contenido) != (p["emoji"], p["titulo"], p["contenido"]):
                paginas.actualizar_pagina(p["id"], nuevo_titulo, nuevo_emoji, nuevo_contenido)

            col_a, col_b = st.columns(2)
            etiqueta = "Ocultar" if p["active"] else "Mostrar"
            if col_a.button(etiqueta, key=f"pag_toggle_{p['id']}", width="stretch"):
                paginas.toggle_activo(p["id"], not p["active"])
                st.rerun()
            if col_b.button("Eliminar", key=f"pag_del_{p['id']}", width="stretch"):
                paginas.eliminar_pagina(p["id"])
                st.rerun()

    st.markdown("**Agregar página nueva**")
    nuevo_emoji_n = st.text_input("Emoji", value="📄", key="pag_nuevo_emoji", max_chars=4)
    nuevo_titulo_n = st.text_input("Título", key="pag_nuevo_titulo")
    nuevo_contenido_n = st.text_area("Contenido (markdown)", key="pag_nuevo_contenido", height=150)
    if st.button("Agregar página"):
        if not nuevo_titulo_n.strip():
            st.error("Ponele un título.")
        else:
            paginas.agregar_pagina(nuevo_titulo_n.strip(), nuevo_emoji_n.strip(), nuevo_contenido_n)
            st.success("Página agregada.")
            st.rerun()

    st.divider()
    st.markdown("**Orden de pestañas**")
    st.caption(
        "Nombre y emoji en blanco = usar el de fábrica. La pestaña con el "
        "número de orden más chico aparece primero."
    )
    metas = _tabs_meta(user_id, modulos_activos)
    config = pestanas.get_all_config()
    metas_ordenadas = sorted(metas, key=lambda m: config.get(m["key"], {}).get("orden", m["orden"]))
    for m in metas_ordenadas:
        ov = config.get(m["key"], {})
        with st.container(border=True):
            col1, col2, col3 = st.columns([1, 3, 1])
            emoji_in = col1.text_input(
                "Emoji", value=ov.get("emoji") or "", key=f"pest_emoji_{m['key']}",
                max_chars=4, placeholder=m["emoji"] or "—",
            )
            nombre_in = col2.text_input(
                "Nombre", value=ov.get("nombre") or "", key=f"pest_nombre_{m['key']}",
                placeholder=m["nombre"],
            )
            orden_in = col3.number_input(
                "Orden", value=int(ov["orden"]) if ov.get("orden") is not None else m["orden"],
                key=f"pest_orden_{m['key']}",
            )
            if st.button("Guardar", key=f"pest_guardar_{m['key']}"):
                pestanas.set_config(m["key"], nombre_in.strip(), emoji_in.strip(), int(orden_in))
                st.success("Guardado.")
                st.rerun()


def main() -> None:
    if not _logged_in():
        _login_screen()
        return

    user_id = st.session_state["tg_id"]

    col_titulo, col_salir = st.columns([4, 1])
    with col_titulo:
        st.markdown('<h2><span class="olimpo-flame">🔥</span> OLIMPO</h2>', unsafe_allow_html=True)
    with col_salir:
        if st.button("Salir"):
            auth.cerrar_sesion(user_id)
            st.session_state.clear()
            st.rerun()

    modulos_activos = sdk.modulos_activos()
    mod_por_id = {fila["module_id"]: mod for fila, mod in modulos_activos}
    nombre_por_modulo_id = {fila["module_id"]: fila["nombre"] for fila, _mod in modulos_activos}
    paginas_por_id = {p["id"]: p for p in paginas.listar_paginas()}

    metas = _aplicar_config_pestanas(_tabs_meta(user_id, modulos_activos))
    etiquetas = [_icono_y_texto(m["nombre"], m["emoji"]) for m in metas]
    tabs = st.tabs(etiquetas)

    for tab, m in zip(tabs, metas):
        key = m["key"]
        with tab:
            if key == "inicio":
                _home_screen()
            elif key == "anuncios":
                _anuncios_screen()
            elif key == "admin":
                _admin_screen(user_id)
                st.divider()
                _pestanas_admin_screen(user_id, modulos_activos)
            elif key.startswith("modulo:"):
                module_id = key.split(":", 1)[1]
                with _api_errors(f"Error en el módulo {nombre_por_modulo_id[module_id]}"):
                    mod_por_id[module_id].render(user_id)
            elif key.startswith("pagina:"):
                pagina_id = int(key.split(":", 1)[1])
                pagina = paginas_por_id.get(pagina_id)
                if pagina is not None:
                    _pagina_screen(pagina)


if __name__ == "__main__":
    main()
