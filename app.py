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
from modules import smspool, tempmail

st.set_page_config(page_title="OLIMPO", page_icon="🔥", layout="centered")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.app")

SESSION_TTL_SECONDS = 60 * 60
BANNER_PATH = Path(__file__).parent / "assets" / "banner.jpg"

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
                    st.error("Código incorrecto o expirado.")
        with col2:
            if st.button("Cancelar"):
                st.session_state.pop("login_stage", None)
                st.session_state.pop("pending_tg_id", None)
                st.rerun()


def _tempmail_screen(user_id: int) -> None:
    st.subheader("📧 Correo temporal")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tempmail_cuentas WHERE user_id = ?", (user_id,)
        ).fetchone()

    if row is None:
        st.write("Todavía no tienes un correo temporal.")
        if st.button("Crear correo"):
            with _api_errors("No se pudo crear el correo"):
                with st.spinner("Creando correo..."):
                    cuenta = tempmail.crear_cuenta(user_id)
                st.success(f"Listo: {cuenta['email']}")
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
                st.caption(campo.capitalize())
                st.code(valor, language=None)
        if st.button("Regenerar identidad"):
            with _api_errors("No se pudo generar la identidad"):
                st.session_state["identidad"] = tempmail.generar_identidad(user_id)
                st.rerun()

    st.divider()
    if st.button("Borrar correo"):
        with _api_errors("No se pudo borrar el correo"):
            tempmail.eliminar_cuenta(user_id)
            st.rerun()


def _sms_refund(user_id: int, credits: int, motivo: str) -> None:
    if credits > 0:
        creditos.asignar(user_id, credits, motivo)


def _sms_screen(user_id: int) -> None:
    st.subheader("📱 Números SMS")
    st.caption(f"Tienes {creditos.saldo(user_id)} crédito(s)")

    order = st.session_state.get("sms_order")

    if order is None:
        servicios = []
        with _api_errors("No se pudo cargar la lista de servicios"):
            servicios = smspool.listar_servicios()
        if not servicios:
            return

        servicio = st.selectbox("Servicio", servicios, format_func=lambda s: s["nombre"])

        paises = []
        with _api_errors("No se pudieron cargar los países para este servicio"):
            paises = smspool.listar_paises_servicio(servicio["id"])
        if not paises:
            st.info("Sin disponibilidad para este servicio por ahora.")
            return

        pais = st.selectbox(
            "País", paises,
            format_func=lambda p: f"{p['nombre']} — {p['creditos']} crédito(s)",
        )
        st.caption(f"Costo: {pais['creditos']} crédito(s)")

        if st.button("Obtener número", type="primary"):
            credits = pais["creditos"]
            if creditos.saldo(user_id) < credits:
                st.error("No tienes créditos suficientes. Pídele a un admin que te asigne más.")
            elif not creditos.descontar(user_id, credits, f"Compra SMS: {servicio['nombre']} {pais['nombre']}"):
                st.error("No tienes créditos suficientes. Pídele a un admin que te asigne más.")
            else:
                # Cobramos antes de llamar a la API: si SMSPool falla, se
                # reembolsa de inmediato para no dejar créditos en el aire.
                try:
                    with st.spinner("Comprando número..."):
                        compra = smspool.comprar_numero(pais["id"], servicio["id"])
                    smspool.registrar_pedido(
                        user_id, compra["order_id"], compra["number"],
                        servicio["nombre"], pais["nombre"], credits, compra["expires_in"],
                    )
                except Exception as exc:
                    _sms_refund(user_id, credits, f"Reembolso — error al comprar: {exc}")
                    st.error(f"No se pudo comprar el número. Créditos devueltos. ({exc})")
                    return

                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=compra["expires_in"])
                ).isoformat()
                st.session_state["sms_order"] = {
                    "order_id": compra["order_id"],
                    "number": compra["number"],
                    "service_name": servicio["nombre"],
                    "country_name": pais["nombre"],
                    "credits": credits,
                    "expires_at": expires_at,
                    "cancel_deadline": time.time() + smspool.CANCEL_WINDOW_SECONDS,
                    "sms": None,
                }
                st.rerun()
        return

    # Pedido expirado sin código: intento final y reembolso automático.
    if not order.get("sms") and smspool.esta_expirado(order["expires_at"]):
        resultado = {}
        with _api_errors("No se pudo verificar el código"):
            resultado = smspool.check_sms(order["order_id"])
        if resultado.get("sms"):
            order["sms"] = resultado["sms"]
            st.session_state["sms_order"] = order
        else:
            smspool.marcar_fallido(order["order_id"])
            _sms_refund(
                user_id, order["credits"],
                f"Reembolso — código no recibido a tiempo ({order['order_id']})",
            )
            st.session_state.pop("sms_order", None)
            st.warning("El número expiró sin recibir código. Tus créditos fueron devueltos.")
            st.rerun()
            return

    st.caption(f"{order['service_name']} · {order['country_name']}")
    st.caption("Número asignado")
    st.code(order["number"], language=None)

    if order.get("sms"):
        st.success("Código recibido")
        st.code(order["sms"], language=None)
        if st.button("Nuevo pedido"):
            st.session_state.pop("sms_order", None)
            st.rerun()
        return

    puede_cancelar = time.time() <= order.get("cancel_deadline", 0)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Revisar código"):
            with _api_errors("No se pudo revisar el código"):
                resultado = smspool.check_sms(order["order_id"])
                if resultado.get("sms"):
                    order["sms"] = resultado["sms"]
                    st.session_state["sms_order"] = order
                    st.rerun()
                else:
                    st.info("Aún no llega el código. Sigue esperando.")
    with col2:
        if puede_cancelar and st.button("Cancelar pedido"):
            with _api_errors("No se pudo cancelar el pedido"):
                smspool.cancelar(order["order_id"])
                _sms_refund(
                    user_id, order["credits"],
                    f"Reembolso — cancelación manual ({order['order_id']})",
                )
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
    st.markdown("**Créditos (para Números SMS — el correo temporal es gratis)**")
    st.caption(
        "El costo por número se calcula automáticamente según el precio del país "
        "en SMSPool: ≤$5 MXN → 10 cr · ≤$10 → 20 cr · ≤$15 → 30 cr · ≤$20 → 40 cr. "
        "Países más caros no aparecen en el panel."
    )
    tasa_actual = smspool.get_config("usd_to_mxn", "18.5")
    nueva_tasa = st.text_input("Tasa USD → MXN", value=tasa_actual, key="admin_tasa_mxn")
    if st.button("Guardar tasa"):
        try:
            float(nueva_tasa)
        except ValueError:
            st.error("Ingresa un número válido para la tasa.")
        else:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO smspool_config (key, value) VALUES ('usd_to_mxn', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (nueva_tasa.strip(),),
                )
            st.success("Tasa actualizada.")
            st.rerun()

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

    nombres = ["Inicio", "Correo temporal", "Números SMS"]
    if auth.is_admin(user_id):
        nombres.append("Admin")

    tabs = st.tabs(nombres)

    with tabs[0]:
        _home_screen()
    with tabs[1]:
        _tempmail_screen(user_id)
    with tabs[2]:
        _sms_screen(user_id)
    if auth.is_admin(user_id):
        with tabs[3]:
            _admin_screen(user_id)


if __name__ == "__main__":
    main()
