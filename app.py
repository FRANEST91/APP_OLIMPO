import asyncio
import os
import time

import streamlit as st
from telegram import Bot

import auth
import db
from modules import smspool, tempmail

st.set_page_config(page_title="OLIMPO", page_icon="🔥", layout="centered")

SESSION_TTL_SECONDS = 60 * 60

db.init_db()


def _bot() -> Bot:
    return Bot(token=os.environ["OLIMPO_BOT_TOKEN"])


def _run(coro):
    return asyncio.run(coro)


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
                _run(auth.send_otp(tg_id, _bot()))
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
        with st.spinner("Cargando mensajes..."):
            mensajes = tempmail.ver_bandeja(user_id)
        if not mensajes:
            st.write("Bandeja vacía.")
        for m in mensajes:
            icono = "📨" if m["seen"] else "📩"
            with st.expander(f"{icono} {m['subject']} — {m['from']}"):
                if st.button("Leer", key=f"leer_{m['id']}"):
                    detalle = tempmail.leer_mensaje(user_id, m["id"])
                    st.write(detalle["body"])
                if st.button("Eliminar", key=f"del_{m['id']}"):
                    tempmail.eliminar_mensaje(user_id, m["id"])
                    st.rerun()

    with tab_identidad:
        if "identidad" not in st.session_state:
            st.session_state["identidad"] = tempmail.generar_identidad(user_id)
        identidad = st.session_state["identidad"]
        for campo, valor in identidad.items():
            st.text_input(campo.capitalize(), value=valor, disabled=True, key=f"id_{campo}")
        if st.button("Regenerar identidad"):
            st.session_state["identidad"] = tempmail.generar_identidad(user_id)
            st.rerun()

    st.divider()
    if st.button("Eliminar cuenta"):
        tempmail.eliminar_cuenta(user_id)
        st.rerun()


def _sms_screen(user_id: int) -> None:
    st.subheader("📱 SMS Pool")

    order = st.session_state.get("sms_order")

    if order is None:
        paises = smspool.listar_paises()
        servicios = smspool.listar_servicios()

        pais = st.selectbox("País", paises, format_func=lambda p: p["nombre"])
        servicio = st.selectbox("Servicio", servicios, format_func=lambda s: s["nombre"])

        if st.button("Obtener número", type="primary"):
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
            resultado = smspool.check_sms(order["order_id"])
            if str(resultado["status"]) == "3" and resultado["sms"]:
                order["sms"] = resultado["sms"]
                st.session_state["sms_order"] = order
                st.rerun()
            else:
                st.info("Aún no llega el código. Sigue esperando.")
    with col2:
        if st.button("Cancelar pedido"):
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


def main() -> None:
    if not _logged_in():
        _login_screen()
        return

    user_id = st.session_state["tg_id"]

    st.sidebar.markdown("## 🔥 OLIMPO")
    seccion = st.sidebar.radio("Navegación", ["TempMail", "SMS Pool"])
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.clear()
        st.rerun()

    if seccion == "TempMail":
        _tempmail_screen(user_id)
    else:
        _sms_screen(user_id)


if __name__ == "__main__":
    main()
