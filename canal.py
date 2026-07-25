from db import get_conn

MAX_MENSAJES = 30


def guardar_mensaje(message_id: int, texto: str, posted_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO canal_mensajes (message_id, texto, posted_at)
            VALUES (?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET texto = excluded.texto
            """,
            (message_id, texto, posted_at),
        )
        # Sin cron ni proceso aparte que pode esto — se recorta acá mismo,
        # en cada inserción, para no guardar historial sin límite.
        conn.execute(
            """
            DELETE FROM canal_mensajes WHERE id NOT IN (
                SELECT id FROM canal_mensajes ORDER BY id DESC LIMIT ?
            )
            """,
            (MAX_MENSAJES,),
        )


def listar_mensajes(limit: int = MAX_MENSAJES) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM canal_mensajes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
