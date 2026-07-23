from datetime import datetime, timezone

from db import get_conn


def saldo(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT saldo FROM creditos WHERE user_id = ?", (user_id,)).fetchone()
    return row["saldo"] if row else 0


def _registrar_movimiento(conn, user_id: int, delta: int, motivo: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO creditos_movimientos (user_id, delta, motivo, created_at) VALUES (?, ?, ?, ?)",
        (user_id, delta, motivo, now),
    )


def asignar(user_id: int, cantidad: int, motivo: str = "Asignado por admin") -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO creditos (user_id, saldo) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET saldo = saldo + excluded.saldo
            """,
            (user_id, cantidad),
        )
        _registrar_movimiento(conn, user_id, cantidad, motivo)


def descontar(user_id: int, cantidad: int, motivo: str) -> bool:
    """Descuenta créditos de forma atómica. False si el saldo no alcanza."""
    with get_conn() as conn:
        row = conn.execute("SELECT saldo FROM creditos WHERE user_id = ?", (user_id,)).fetchone()
        saldo_actual = row["saldo"] if row else 0
        if saldo_actual < cantidad:
            return False
        conn.execute("UPDATE creditos SET saldo = saldo - ? WHERE user_id = ?", (cantidad, user_id))
        _registrar_movimiento(conn, user_id, -cantidad, motivo)
    return True


def historial(user_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM creditos_movimientos WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def listar_saldos() -> list:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT w.tg_id, w.username, COALESCE(c.saldo, 0) AS saldo
            FROM whitelist w
            LEFT JOIN creditos c ON c.user_id = w.tg_id
            ORDER BY w.username
            """
        ).fetchall()
