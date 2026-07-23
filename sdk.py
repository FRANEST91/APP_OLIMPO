"""SDK para módulos de OLIMPO.

Todo lo que un módulo necesita para cobrar créditos, guardar datos y
hacer llamadas HTTP con proxy pasa por acá — así un módulo nunca toca
`creditos.py` ni `db.py` directamente ni sabe cómo está armada la base
de datos de otro módulo. La guía completa para escribir un módulo nuevo
está en MODULOS.md; este archivo es la implementación de esa guía.
"""

import importlib
import importlib.util
import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

import creditos
from db import get_conn

log = logging.getLogger("olimpo.sdk")

BASE_DIR = Path(__file__).parent
MODULES_DIR = BASE_DIR / "modules"
EXTERNAL_DIR = BASE_DIR / "external_modules"
USER_DB_DIR = BASE_DIR / "data" / "modulos"

REQUIRED_ATTRS = ("MODULE_ID", "MODULE_NAME", "render")

# Módulos ya importados en este proceso: module_id -> objeto módulo.
# Evita reimportar (y re-crear tablas) en cada rerun de Streamlit.
_loaded: dict = {}


# ---------------------------------------------------------------------------
# Créditos — único punto de acceso al saldo del usuario. Ver MODULOS.md
# sección "Créditos" para el patrón recomendado (cobrar antes, reembolsar
# si la operación falla).
# ---------------------------------------------------------------------------

def charge(user_id: int, amount: int, reason: str) -> bool:
    """Descuenta créditos de forma atómica. False si el saldo no alcanza."""
    return creditos.descontar(user_id, amount, reason)


def refund(user_id: int, amount: int, reason: str) -> None:
    if amount > 0:
        creditos.asignar(user_id, amount, reason)


def balance(user_id: int) -> int:
    return creditos.saldo(user_id)


@contextmanager
def api_errors(mensaje: str):
    """Envuelve una operación que puede fallar (típicamente una llamada a
    una API externa): si falla, la registra en el log y muestra un
    st.error en vez de tumbar la pestaña entera. Mismo patrón que usa el
    resto de OLIMPO — úsalo en tu render() para cualquier llamada externa."""
    try:
        yield
    except Exception:
        log.exception(mensaje)
        st.error(f"{mensaje}. Intenta de nuevo en un momento.")


# ---------------------------------------------------------------------------
# Persistencia — ver MODULOS.md sección "Datos" para los 3 patrones.
# ---------------------------------------------------------------------------

def db_conn():
    """Conexión a la base de datos compartida de OLIMPO. Úsala para tablas
    propias de tu módulo (nombralas con tu MODULE_ID de prefijo) o para
    guardar filas con una columna user_id — los dos primeros patrones de
    datos. Créala vos mismo con CREATE TABLE IF NOT EXISTS, normalmente en
    on_activar()."""
    return get_conn()


def get_config(module_id: str, key: str, default: str = "") -> str:
    """Config compartida y editable en caliente (igual para todos los
    usuarios) — tasas, flags, límites, etc."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM sdk_modulo_config WHERE module_id = ? AND key = ?",
            (module_id, key),
        ).fetchone()
    return row["value"] if row else default


def set_config(module_id: str, key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sdk_modulo_config (module_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(module_id, key) DO UPDATE SET value = excluded.value
            """,
            (module_id, key, value),
        )


@contextmanager
def user_db(module_id: str, user_id: int):
    """Conexión a un archivo SQLite exclusivo de un usuario para este
    módulo (data/modulos/<module_id>/<user_id>.db). Usalo solo cuando cada
    usuario necesita su propio conjunto de datos aislado — no para guardar
    unas pocas filas (para eso alcanza con una tabla + columna user_id en
    la base compartida, vía db_conn())."""
    folder = USER_DB_DIR / module_id
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / f"{user_id}.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP con proxy — ver MODULOS.md sección "Proxies".
# ---------------------------------------------------------------------------

def _proxies(module_id: str) -> dict | None:
    proxy = os.getenv(f"OLIMPO_PROXY_{module_id.upper()}") or os.getenv("OLIMPO_PROXY")
    return {"http": proxy, "https": proxy} if proxy else None


def http_get(module_id: str, url: str, timeout: int = 15, **kwargs):
    return requests.get(url, proxies=_proxies(module_id), timeout=timeout, **kwargs)


def http_post(module_id: str, url: str, timeout: int = 15, **kwargs):
    return requests.post(url, proxies=_proxies(module_id), timeout=timeout, **kwargs)


# ---------------------------------------------------------------------------
# Registro y carga dinámica de módulos.
# ---------------------------------------------------------------------------

class ModuloInvalido(Exception):
    pass


def _validar(mod) -> None:
    faltan = [a for a in REQUIRED_ATTRS if not hasattr(mod, a)]
    if faltan:
        raise ModuloInvalido(f"Falta definir: {', '.join(faltan)}")


def _importar_interno(module_id: str):
    mod = importlib.import_module(f"modules.{module_id}")
    _validar(mod)
    return mod


def _importar_externo(module_id: str, path: Path):
    if not path.exists():
        raise ModuloInvalido(f"No se encontró el archivo {path}")
    spec = importlib.util.spec_from_file_location(f"olimpo_ext_{module_id}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _validar(mod)
    return mod


def _filas() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM sdk_modulos ORDER BY nombre").fetchall()


def _fila(module_id: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sdk_modulos WHERE module_id = ?", (module_id,)
        ).fetchone()


def _registrar_fila(module_id: str, mod, origen: str, activo: int = 1) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sdk_modulos (module_id, nombre, version, autor, origen, activo, instalado_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(module_id) DO UPDATE SET
                nombre = excluded.nombre, version = excluded.version, autor = excluded.autor
            """,
            (
                module_id, getattr(mod, "MODULE_NAME", module_id),
                getattr(mod, "MODULE_VERSION", "?"), getattr(mod, "MODULE_AUTHOR", "?"),
                origen, activo, now,
            ),
        )


def _on_activar(mod) -> None:
    hook = getattr(mod, "on_activar", None)
    if callable(hook):
        hook()


def descubrir_e_instalar() -> None:
    """Escanea modules/*.py y registra como internos los que todavía no
    están en sdk_modulos. Se llama una vez al arrancar la app — los que ya
    estaban registrados no se tocan (así admin puede desactivarlos y no
    reaparecen solos)."""
    conocidos = {f["module_id"] for f in _filas()}
    for path in sorted(MODULES_DIR.glob("*.py")):
        module_id = path.stem
        if module_id.startswith("_") or module_id in conocidos:
            continue
        try:
            mod = _importar_interno(module_id)
        except Exception:
            continue
        _registrar_fila(module_id, mod, "interno", activo=1)
        _loaded[module_id] = mod
        try:
            _on_activar(mod)
        except Exception:
            pass


def _cargar(fila) -> object:
    module_id = fila["module_id"]
    if module_id in _loaded:
        return _loaded[module_id]
    if fila["origen"] == "interno":
        mod = _importar_interno(module_id)
    else:
        mod = _importar_externo(module_id, EXTERNAL_DIR / f"{module_id}.py")
    _loaded[module_id] = mod
    return mod


def listar_modulos() -> list:
    """Metadata de todos los módulos registrados, activos e inactivos —
    para el panel Admin > Gestión de módulos."""
    return [dict(f) for f in _filas()]


def modulos_activos() -> list:
    """[(metadata_dict, objeto_modulo), ...] de los módulos activos que
    cargaron sin errores. Si uno falla al importar se omite en silencio —
    un módulo roto no debe tumbar el resto de la app."""
    activos = []
    for fila in _filas():
        if not fila["activo"]:
            continue
        try:
            mod = _cargar(fila)
        except Exception:
            continue
        activos.append((dict(fila), mod))
    return activos


def activar(module_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sdk_modulos SET activo = 1 WHERE module_id = ?", (module_id,))
    fila = _fila(module_id)
    if fila is not None:
        try:
            _on_activar(_cargar(fila))
        except Exception:
            pass


def desactivar(module_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sdk_modulos SET activo = 0 WHERE module_id = ?", (module_id,))
    _loaded.pop(module_id, None)


def recargar(module_id: str) -> None:
    """Fuerza reimportar el módulo (por ejemplo tras subir una versión
    nueva de un módulo externo), sin reiniciar el proceso de Streamlit."""
    _loaded.pop(module_id, None)
    sys.modules.pop(f"modules.{module_id}", None)
    sys.modules.pop(f"olimpo_ext_{module_id}", None)


def registrar_externo(module_id: str, contenido: bytes) -> None:
    """Guarda un archivo .py como módulo externo (vive en external_modules/,
    fuera de modules/ y sin versionar en git) y lo valida antes de dejarlo
    registrado — si no cumple el contrato, no se guarda nada."""
    EXTERNAL_DIR.mkdir(exist_ok=True)
    path = EXTERNAL_DIR / f"{module_id}.py"
    path.write_bytes(contenido)
    try:
        recargar(module_id)
        mod = _importar_externo(module_id, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _registrar_fila(module_id, mod, "externo", activo=1)
    _loaded[module_id] = mod
    try:
        _on_activar(mod)
    except Exception:
        pass


def hacer_interno(module_id: str) -> None:
    """'Gradúa' un módulo externo copiando su archivo a modules/, para que
    quede versionado en git como parte oficial de OLIMPO en el próximo
    commit. No borra el archivo externo (queda de respaldo)."""
    fila = _fila(module_id)
    if fila is None or fila["origen"] != "externo":
        raise ValueError("Solo se puede internar un módulo que ya esté registrado como externo")
    origen_path = EXTERNAL_DIR / f"{module_id}.py"
    if not origen_path.exists():
        raise ValueError(f"No se encontró {origen_path}")
    (MODULES_DIR / f"{module_id}.py").write_bytes(origen_path.read_bytes())
    with get_conn() as conn:
        conn.execute("UPDATE sdk_modulos SET origen = 'interno' WHERE module_id = ?", (module_id,))
    recargar(module_id)


def eliminar(module_id: str) -> None:
    """Da de baja un módulo externo (borra registro y archivo). Los
    internos son parte del código versionado — no se eliminan desde el
    panel, solo se desactivan."""
    fila = _fila(module_id)
    if fila is None:
        return
    if fila["origen"] == "interno":
        raise ValueError("Los módulos internos no se eliminan desde el panel, solo se desactivan")
    with get_conn() as conn:
        conn.execute("DELETE FROM sdk_modulos WHERE module_id = ?", (module_id,))
    (EXTERNAL_DIR / f"{module_id}.py").unlink(missing_ok=True)
    _loaded.pop(module_id, None)
