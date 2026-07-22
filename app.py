import asyncio
import csv
import io
import os
import time
from contextlib import contextmanager

from dotenv import load_dotenv

# Debe cargarse antes de importar auth/db: leen variables de entorno al
# importarse. En Railway no hace nada (no hay .env, ya vienen del entorno).
load_dotenv()

import streamlit as st
from telegram import Bot

import auth
import db
from modules import smspool, tempmail

st.set_page_config(page_title="OLIMPO", page_icon="🔥", layout="centered")

SESSION_TTL_SECONDS = 60 * 60

db.init_db()


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
    except Exception as exc:
        st.error(f"{mensaje}: {exc}")


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
                st.error("Acceso denegado. Este ID no está autorizado.")
                return
            try:
                _run(_send_otp(tg_id))
            except Exception as exc:
                st.error(f"No se pudo enviar el código: {exc}")
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
                    st.error("Código incorrecto o expirado.")
        with col2:
            if st.button("Cancelar"):
                st.session_state.pop("login_stage", None)
                st.session_state.pop("pending_tg_id", None)
                st.rerun()


def _tempmail_screen(user_id: int) -> None:
    st.subheader("📧 TempMail")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tempmail_cuentas WHERE user_id = ?", (user_id,)
        ).fetchone()

    if row is None:
        st.write("No tienes una cuenta de correo temporal activa.")
        if st.button("Crear cuenta"):
            with _api_errors("No se pudo crear la cuenta"):
                with st.spinner("Creando cuenta..."):
                    cuenta = tempmail.crear_cuenta(user_id)
                st.success(f"Cuenta creada: {cuenta['email']}")
                st.rerun()
        return

    st.code(row["email"], language=None)

    tab_bandeja, tab_identidad = st.tabs(["Bandeja", "Identidad"])

    with tab_bandeja:
        if st.button("Actualizar bandeja"):
            st.rerun()
        mensajes = []
        with _api_errors("No se pudo cargar la bandeja"):
            with st.spinner("Cargando mensajes..."):
                mensajes = tempmail.ver_bandeja(user_id)
        if not mensajes:
            st.write("Bandeja vacía.")
        for m in mensajes:
            icono = "📨" if m["seen"] else "📩"
            with st.expander(f"{icono} {m['subject']} — {m['from']}"):
                if st.button("Leer", key=f"leer_{m['id']}"):
                    with _api_errors("No se pudo leer el mensaje"):
                        detalle = tempmail.leer_mensaje(user_id, m["id"])
                        st.write(detalle["body"])
                if st.button("Eliminar", key=f"del_{m['id']}"):
                    with _api_errors("No se pudo eliminar el mensaje"):
                        tempmail.eliminar_mensaje(user_id, m["id"])
                        st.rerun()

    with tab_identidad:
        if "identidad" not in st.session_state:
            with _api_errors("No se pudo generar la identidad"):
                st.session_state["identidad"] = tempmail.generar_identidad(user_id)
        identidad = st.session_state.get("identidad")
        if identidad:
            for campo, valor in identidad.items():
                st.text_input(campo.capitalize(), value=valor, disabled=True, key=f"id_{campo}")
        if st.button("Regenerar identidad"):
            with _api_errors("No se pudo generar la identidad"):
                st.session_state["identidad"] = tempmail.generar_identidad(user_id)
                st.rerun()

    st.divider()
    if st.button("Eliminar cuenta"):
        with _api_errors("No se pudo eliminar la cuenta"):
            tempmail.eliminar_cuenta(user_id)
            st.rerun()


def _sms_screen(user_id: int) -> None:
    st.subheader("📱 SMS Pool")

    order = st.session_state.get("sms_order")

    if order is None:
        paises, servicios = [], []
        with _api_errors("No se pudieron cargar países/servicios"):
            paises = smspool.listar_paises()
            servicios = smspool.listar_servicios()
        if not paises or not servicios:
            return

        pais = st.selectbox("País", paises, format_func=lambda p: p["nombre"])
        servicio = st.selectbox("Servicio", servicios, format_func=lambda s: s["nombre"])

        if st.button("Obtener número", type="primary"):
            with _api_errors("No se pudo comprar el número"):
                with st.spinner("Comprando número..."):
                    nuevo_pedido = smspool.comprar_numero(user_id, pais["id"], servicio["id"])
                st.session_state["sms_order"] = nuevo_pedido
                st.rerun()
        return

    st.write(f"Número asignado: **{order['number']}**")

    if order.get("sms"):
        st.success("Código recibido")
        st.code(order["sms"], language=None)
        if st.button("Nuevo pedido"):
            st.session_state.pop("sms_order", None)
            st.rerun()
        return

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Revisar código"):
            with _api_errors("No se pudo revisar el código"):
                resultado = smspool.check_sms(order["order_id"])
                if str(resultado["status"]) == "3" and resultado["sms"]:
                    order["sms"] = resultado["sms"]
                    st.session_state["sms_order"] = order
                    st.rerun()
                else:
                    st.info("Aún no llega el código. Sigue esperando.")
    with col2:
        if st.button("Cancelar pedido"):
            with _api_errors("No se pudo cancelar el pedido"):
                smspool.cancelar(order["order_id"])
                st.session_state.pop("sms_order", None)
                st.rerun()

    st.divider()
    st.caption("Historial de pedidos")
    with db.get_conn() as conn:
        historial = conn.execute(
            """
            SELECT * FROM olimpo_sms_orders
            WHERE user_id = ? ORDER BY requested_at DESC LIMIT 10
            """,
            (user_id,),
        ).fetchall()
    for h in historial:
        st.text(f"{h['service_name']} · {h['phone_number']} · {h['status']}")


def _admin_screen(user_id: int) -> None:
    st.subheader("🛡️ Admin — Whitelist")

    with st.expander("Importar CSV"):
        st.caption("Columnas esperadas: tg_id, username (opcional), active (opcional)")
        archivo = st.file_uploader("Archivo CSV", type="csv", key="admin_csv")
        if archivo is not None and st.button("Importar"):
            with _api_errors("No se pudo importar el CSV"):
                contenido = archivo.getvalue().decode("utf-8")
                filas = list(csv.DictReader(io.StringIO(contenido)))
                importados, omitidos = auth.import_csv(filas, user_id)
                st.success(f"Importados: {importados} · Omitidos: {omitidos}")
                st.rerun()

    with st.expander("Agregar usuario manual"):
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


def main() -> None:
    if not _logged_in():
        _login_screen()
        return

    user_id = st.session_state["tg_id"]

    st.sidebar.markdown("## 🔥 OLIMPO")
    opciones = ["TempMail", "SMS Pool"]
    if auth.is_admin(user_id):
        opciones.append("Admin")
    seccion = st.sidebar.radio("Navegación", opciones)
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.clear()
        st.rerun()

    if seccion == "TempMail":
        _tempmail_screen(user_id)
    elif seccion == "SMS Pool":
        _sms_screen(user_id)
    else:
        _admin_screen(user_id)


if __name__ == "__main__":
    main()
