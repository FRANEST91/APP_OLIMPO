import asyncio
import base64
import csv
import html
import io
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Debe cargarse antes de importar auth/db: leen variables de entorno al
# importarse. En Railway no hace nada (no hay .env, ya vienen del entorno).
load_dotenv()

import streamlit as st
from telegram import Bot

import auth
import carrusel
import creditos
import db
import sdk

st.set_page_config(page_title="OLIMPO", page_icon="🔥", layout="centered")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.app")

SESSION_TTL_SECONDS = 60 * 60
BANNER_PATH = Path(__file__).parent / "assets" / "banner.jpg"

db.init_db()
sdk.descubrir_e_instalar()


async def _send_otp(tg_id: int) -> None:
    # Bot debe usarse como context manager: si no, el cliente HTTP interno
    # nunca se cierra y cada login deja una conexión abierta (leak).
    async with Bot(token=os.environ["OLIMPO_BOT_TOKEN"]) as bot:
        await auth.send_otp(tg_id, bot)


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def _api_errors(mensaje: str):
    try:
        yield
    except Exception:
        logger.exception(mensaje)
        st.error(f"{mensaje}. Intenta de nuevo en un momento.")


def _logged_in() -> bool:
    expires_at = st.session_state.get("session_expires_at")
    return bool(expires_at and time.time() < expires_at)


def _login_screen() -> None:
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
                _run(_send_otp(tg_id))
            except Exception:
                logger.exception("No se pudo enviar el código OTP")
                st.error("No pudimos enviarte el código. Intenta de nuevo en un momento.")
                return
            st.session_state["pending_tg_id"] = tg_id
            st.session_state["login_stage"] = "otp"
            st.rerun()

    elif stage == "otp":
        st.info("Revisa tu Telegram. Te enviamos un código de 6 dígitos.")
        otp_input = st.text_input("Código OTP", key="otp_input", max_chars=6)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Verificar", type="primary"):
                tg_id = st.session_state["pending_tg_id"]
                if auth.verify_otp(tg_id, otp_input):
                    st.session_state["session_expires_at"] = time.time() + SESSION_TTL_SECONDS
                    st.session_state["tg_id"] = tg_id
                    st.session_state.pop("login_stage", None)
                    st.session_state.pop("pending_tg_id", None)
                    st.rerun()
                else:
                    sdk.alertar(
                        f"🔒 <b>Código OTP inválido</b>\n"
                        f"👤 <code>{tg_id}</code> ingresó un código incorrecto o vencido."
                    )
                    st.error("Código incorrecto o expirado.")
        with col2:
            if st.button("Cancelar"):
                st.session_state.pop("login_stage", None)
                st.session_state.pop("pending_tg_id", None)
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

    st.divider()
    st.markdown("**Agregar módulo externo**")
    st.caption(
        "Subí un archivo .py que cumpla el contrato de MODULOS.md (MODULE_ID, "
        "MODULE_NAME, render()). Se valida antes de activarlo — si falta algo "
        "requerido, no se guarda nada."
    )
    archivo_mod = st.file_uploader("Archivo del módulo (.py)", type="py", key="admin_mod_upload")
    id_sugerido = archivo_mod.name[:-3] if archivo_mod else ""
    module_id_input = st.text_input(
        "ID del módulo (minúsculas, sin espacios)", value=id_sugerido, key="admin_mod_id",
    )
    if archivo_mod is not None and st.button("Agregar módulo"):
        module_id = module_id_input.strip().lower()
        if not module_id or not module_id.replace("_", "").isalnum():
            st.error("El ID del módulo debe ser alfanumérico (guiones bajos permitidos).")
        else:
            with _api_errors("No se pudo agregar el módulo"):
                sdk.registrar_externo(module_id, archivo_mod.getvalue())
                st.success(f"Módulo '{module_id}' agregado como externo.")
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


def main() -> None:
    if not _logged_in():
        _login_screen()
        return

    user_id = st.session_state["tg_id"]

    col_titulo, col_salir = st.columns([4, 1])
    with col_titulo:
        st.markdown("## 🔥 OLIMPO")
    with col_salir:
        if st.button("Salir"):
            st.session_state.clear()
            st.rerun()

    modulos_activos = sdk.modulos_activos()

    nombres = ["Inicio"] + [fila["nombre"] for fila, _mod in modulos_activos]
    if auth.is_admin(user_id):
        nombres.append("Admin")

    tabs = st.tabs(nombres)

    with tabs[0]:
        _home_screen()
    for i, (fila, mod) in enumerate(modulos_activos, start=1):
        with tabs[i]:
            with _api_errors(f"Error en el módulo {fila['nombre']}"):
                mod.render(user_id)
    if auth.is_admin(user_id):
        with tabs[-1]:
            _admin_screen(user_id)


if __name__ == "__main__":
    main()
