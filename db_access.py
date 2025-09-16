# SQLite backend para Gestión Textil (CC dual, cheques, caja, reportes)
import sqlite3
from pathlib import Path
import os
from datetime import datetime

DB_PATH = Path("gestion_textil.db")


def get_conn():
    # timeout alto + WAL + busy_timeout para minimizar "database is locked"
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    # conn.set_trace_callback(print)  # ← descomentá si querés log de cada SQL
    return conn

def _t_exists(cur, name: str) -> bool:
    return bool(cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone())

def _col_exists(cur, table: str, col: str) -> bool:
    try:
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        return col in cols
    except Exception:
        return False

def cc_cli_reset_por_ids(ids):
    """
    Elimina movimientos/materializaciones de CC de clientes SOLO para los IDs dados,
    cubriendo ambos esquemas posibles (cc_clientes_* y cc_cli_*).
    Devuelve un dict con métricas para debug.
    """
    # Normalizar ids a ints
    try:
        ids = [int(str(x).strip()) for x in (ids or []) if str(x).strip() != ""]
    except Exception:
        ids = []

    if not ids:
        return {"tables": 0, "rows": 0}

    conn = get_conn()
    cur = conn.cursor()

    # Tabla, columna del id
    candidates = [
        ("cc_clientes_cuenta1", "cliente_id"),
        ("cc_clientes_cuenta2", "cliente_id"),
        ("cc_clientes",        "cliente_id"),
        ("cc_cli_cuenta1",     "ent_id"),
        ("cc_cli_cuenta2",     "ent_id"),
        ("cc_cli",             "ent_id"),
    ]

    tables_touched = 0
    rows_deleted = 0
    placeholders = ",".join(["?"] * len(ids))
    params = tuple(ids)

    for t, col in candidates:
        if _t_exists(cur, t) and _col_exists(cur, t, col):
            tables_touched += 1
            try:
                cnt = cur.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE {col} IN ({placeholders})",
                    params
                ).fetchone()[0]
                rows_deleted += int(cnt or 0)
                cur.execute(
                    f"DELETE FROM {t} WHERE {col} IN ({placeholders})",
                    params
                )
            except Exception as ex:
                # No frenamos si una tabla falla
                print(f"WARN borrar en {t}:", ex)

    conn.commit()
    conn.close()
    return {"tables": tables_touched, "rows": rows_deleted}


def _fmt_dmy(s):
    if not s:
        return ""
    s = str(s).strip()
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d.strftime("%d/%m/%Y")
        except Exception:
            pass
    return s


# --- Normalizador universal de parámetros para sqlite ---


def _as_params(data, cols):
    """
    Acepta:
      - tuple/list ya en orden
      - dict con las keys de 'cols'
      - tuple/list con un único dict adentro
    Devuelve siempre una tupla en el orden de 'cols'.
    """
    if isinstance(data, dict):
        return tuple(data.get(k) for k in cols)
    if isinstance(data, (list, tuple)) and len(data) == 1 and isinstance(data[0], dict):
        d = data[0]
        return tuple(d.get(k) for k in cols)
    return tuple(data)


# -------------------- INIT / MIGRACIONES --------------------


def _table_info(conn, table):
    try:
        rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
        return {r[1]: r for r in rows}
    except Exception:
        return {}


def _ensure_numeradores(conn):
    """
    Asegura tabla numeradores (tipo TEXT PK, valor INTEGER).
    Si existe con columnas raras, la migra limpiamente.
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS numeradores (
            tipo   TEXT PRIMARY KEY,
            valor  INTEGER NOT NULL DEFAULT 0
        )
    """
    )
    cols = _table_info(conn, "numeradores").keys()
    ok = "tipo" in cols and "valor" in cols and len(cols) == 2
    if not ok:
        # Migración: renombro, creo bien, intento copiar si hay columnas compatibles
        try:
            cur.execute("ALTER TABLE numeradores RENAME TO numeradores_bak")
        except Exception:
            pass
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS numeradores (
                tipo   TEXT PRIMARY KEY,
                valor  INTEGER NOT NULL DEFAULT 0
            )
        """
        )
        try:
            bcols = _table_info(conn, "numeradores_bak").keys()
            if "tipo" in bcols and "valor" in bcols:
                cur.execute(
                    "INSERT INTO numeradores(tipo,valor) SELECT tipo, valor FROM numeradores_bak"
                )
            cur.execute("DROP TABLE IF EXISTS numeradores_bak")
        except Exception:
            cur.execute("DROP TABLE IF EXISTS numeradores_bak")

    # Semillas necesarias
    for t in ("recibo", "op"):
        cur.execute(
            "INSERT OR IGNORE INTO numeradores (tipo, valor) VALUES (?, 0)", (t,)
        )


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Clientes
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT,
        razon_social TEXT,
        condicion_iva TEXT,
        cuit_dni TEXT,
        tel1 TEXT, cont1 TEXT,
        tel2 TEXT, cont2 TEXT,
        email TEXT,
        calle TEXT, nro TEXT, entre TEXT,
        localidad TEXT, cp TEXT, provincia TEXT,
        estado TEXT
    )
    """
    )

    # Divisas (compra/venta de USD) — linkeado a movimientos de caja
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS divisas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha      TEXT,
        tipo       TEXT,      -- 'compra' | 'venta'
        usd        REAL,
        tc         REAL,      -- tipo de cambio (pesos por USD)
        pesos      REAL,      -- importe en pesos
        cuenta     TEXT,      -- cuenta1 | cuenta2 | ambas | NULL
        mov_caja_id INTEGER,  -- vínculo a movimientos_caja.id
        obs        TEXT
    )
    """
    )

    # Proveedores
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT,
        razon_social TEXT,
        condicion_iva TEXT,
        cuit_dni TEXT,
        tel1 TEXT, cont1 TEXT,
        tel2 TEXT, cont2 TEXT,
        email TEXT,
        calle TEXT, nro TEXT, entre TEXT,
        localidad TEXT, cp TEXT, provincia TEXT,
        estado TEXT
    )
    """
    )

    # Empleados (según tu dump)
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        apellido TEXT,
        dni_cuil TEXT,
        telefono TEXT,
        email TEXT,
        calle TEXT,
        numero TEXT,
        entre_calles TEXT,
        localidad TEXT,
        cp TEXT,
        provincia TEXT,
        puesto TEXT,
        fecha_ingreso TEXT,
        tel_emergencias TEXT,
        contacto_emergencias TEXT,
        estado TEXT,
        fecha_egreso TEXT
    )
    """
    )

    # Caja (con 'cuenta')
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS movimientos_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        tipo TEXT,                 -- ingreso | egreso
        medio TEXT,                -- efectivo | cheque | banco | otro
        concepto TEXT,
        detalle TEXT,
        monto REAL,
        tercero_tipo TEXT,         -- cliente | proveedor | ''
        tercero_id INTEGER,
        estado TEXT,               -- confirmado | pendiente
        origen_tipo TEXT,          -- recibo | orden_pago | factura | etc
        origen_id INTEGER,
        categoria_id INTEGER,
        centro_costo_id INTEGER,
        cuenta TEXT                -- cuenta1 | cuenta2 | ambas | NULL
    )
    """
    )

    # Cheques (con 'cuenta' opcional y estado)
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cheques (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT,
        banco TEXT,
        importe REAL,
        fecha_recibido TEXT,
        fecha_cobro TEXT,
        cliente_id INTEGER,
        firmante_nombre TEXT,
        firmante_cuit TEXT,
        estado TEXT,               -- en_cartera | depositado | endosado | rechazado
        fecha_estado TEXT,
        obs TEXT,
        mov_caja_id INTEGER,
        proveedor_id INTEGER,
        cuenta_banco TEXT,
        gastos_bancarios REAL,
        cuenta TEXT                -- cuenta1 | cuenta2 | NULL
    )
    """
    )

    # Cuentas corrientes separadas: CLIENTES
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cc_clientes_c1 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        cliente_id INTEGER,
        doc TEXT,
        numero TEXT,
        concepto TEXT,
        medio TEXT,
        debe REAL,
        haber REAL,
        caja_mov_id INTEGER,
        cheque_id INTEGER,
        obs TEXT
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cc_clientes_c2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        cliente_id INTEGER,
        doc TEXT,
        numero TEXT,
        concepto TEXT,
        medio TEXT,
        debe REAL,
        haber REAL,
        caja_mov_id INTEGER,
        cheque_id INTEGER,
        obs TEXT
    )
    """
    )

    # Cuentas corrientes separadas: PROVEEDORES
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cc_proveedores_c1 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        proveedor_id INTEGER,
        doc TEXT,
        numero TEXT,
        concepto TEXT,
        medio TEXT,
        debe REAL,
        haber REAL,
        caja_mov_id INTEGER,
        cheque_id INTEGER,
        obs TEXT
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cc_proveedores_c2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        proveedor_id INTEGER,
        doc TEXT,
        numero TEXT,
        concepto TEXT,
        medio TEXT,
        debe REAL,
        haber REAL,
        caja_mov_id INTEGER,
        cheque_id INTEGER,
        obs TEXT
    )
    """
    )

    # Numeradores (para next_num)
    _ensure_numeradores(conn)

    conn.commit()
    conn.close()


init_db()


# -------- Numeradores / IDs --------


def next_num(tipo: str, sucursal: str = "0001") -> str:
    """
    Devuelve un número tipo '0001-00000001' por cada 'tipo' (p.ej. 'recibo', 'op').
    Persistente en tabla numeradores(tipo, valor).
    """
    tipo = (tipo or "").strip().lower()
    if tipo not in ("recibo", "op"):
        # Permitimos otros tipos, pero sembramos si no existe
        pass
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO numeradores (tipo, valor) VALUES (?, 0)", (tipo,)
    )
    cur.execute("UPDATE numeradores SET valor = valor + 1 WHERE tipo = ?", (tipo,))
    cur.execute("SELECT valor FROM numeradores WHERE tipo = ?", (tipo,))
    n = int(cur.fetchone()[0])
    conn.commit()
    conn.close()
    return f"{sucursal}-{n:08d}"


def next_id(tabla: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COALESCE(MAX(id),0)+1 FROM {tabla}")
        nid = int(cur.fetchone()[0] or 1)
    except Exception:
        nid = 1
    conn.close()
    return nid


# -------------------- CLIENTES / PROVEEDORES --------------------


def listar_clientes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id,           -- 0
            tipo,         -- 1
            razon_social, -- 2
            condicion_iva,-- 3
            cuit_dni,     -- 4
            tel1,         -- 5
            cont1,        -- 6
            tel2,         -- 7
            cont2,        -- 8
            email,        -- 9
            calle,        -- 10
            nro,          -- 11
            entre,        -- 12
            localidad,    -- 13
            cp,           -- 14
            provincia,    -- 15
            estado        -- 16
        FROM clientes
        ORDER BY id
    """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def obtener_cliente(cid: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id,tipo,razon_social,condicion_iva,cuit_dni,tel1,cont1,tel2,cont2,email,
               calle,nro,entre,localidad,cp,provincia,estado
        FROM clientes WHERE id=?
    """,
        (cid,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def listar_clientes_id_nombre():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, razon_social FROM clientes ORDER BY razon_social")
    rows = cur.fetchall()
    conn.close()
    return rows


def agregar_cliente(data, id_manual=None):
    # data = (tipo, razon_social, condicion_iva, cuit_dni, tel1, cont1, tel2, cont2,
    #         email, calle, nro, entre, localidad, cp, provincia, estado)
    conn = get_conn()
    cur = conn.cursor()
    if id_manual is not None:
        cur.execute(
            """
            INSERT OR REPLACE INTO clientes
            (id,tipo,razon_social,condicion_iva,cuit_dni,tel1,cont1,tel2,cont2,email,
             calle,nro,entre,localidad,cp,provincia,estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (id_manual, *data),
        )
    else:
        cur.execute(
            """
            INSERT INTO clientes
            (tipo,razon_social,condicion_iva,cuit_dni,tel1,cont1,tel2,cont2,email,
             calle,nro,entre,localidad,cp,provincia,estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            data,
        )
    conn.commit()
    conn.close()


def editar_cliente(cid, data):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE clientes SET
            tipo=?, razon_social=?, condicion_iva=?, cuit_dni=?, tel1=?, cont1=?,
            tel2=?, cont2=?, email=?, calle=?, nro=?, entre=?, localidad=?, cp=?, provincia=?, estado=?
        WHERE id=?
    """,
        (*data, cid),
    )
    conn.commit()
    conn.close()


def borrar_cliente(cid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM clientes WHERE id=?", (cid,))
    conn.commit()
    conn.close()


# --- PROVEEDORES: CRUD básico y listados ---


def listar_proveedores():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, tipo, razon_social, condicion_iva, cuit_dni,
               tel1, cont1, tel2, cont2, email,
               calle, nro, entre, localidad, cp, provincia, estado
        FROM proveedores
        ORDER BY id ASC
    """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def listar_proveedores_id_nombre():
    # Para combos / selección rápida (usa en Endosar cheque)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, razon_social FROM proveedores ORDER BY razon_social COLLATE NOCASE"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def obtener_proveedor(proveedor_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, tipo, razon_social, condicion_iva, cuit_dni,
               tel1, cont1, tel2, cont2, email,
               calle, nro, entre, localidad, cp, provincia, estado
        FROM proveedores
        WHERE id=?
    """,
        (proveedor_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def agregar_proveedor(data, id_manual=None):
    """
    data = (tipo, razon_social, condicion_iva, cuit_dni,
            tel1, cont1, tel2, cont2, email,
            calle, nro, entre, localidad, cp, provincia, estado)
    """
    conn = get_conn()
    cur = conn.cursor()
    if id_manual is None:
        cur.execute(
            """
            INSERT INTO proveedores
            (tipo, razon_social, condicion_iva, cuit_dni,
             tel1, cont1, tel2, cont2, email,
             calle, nro, entre, localidad, cp, provincia, estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            tuple(data),
        )
    else:
        # Permití alta con ID manual
        cur.execute(
            """
            INSERT OR REPLACE INTO proveedores
            (id, tipo, razon_social, condicion_iva, cuit_dni,
             tel1, cont1, tel2, cont2, email,
             calle, nro, entre, localidad, cp, provincia, estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (id_manual,) + tuple(data),
        )
    conn.commit()
    conn.close()


def editar_proveedor(proveedor_id: int, data):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE proveedores
        SET tipo=?, razon_social=?, condicion_iva=?, cuit_dni=?,
            tel1=?, cont1=?, tel2=?, cont2=?, email=?,
            calle=?, nro=?, entre=?, localidad=?, cp=?, provincia=?, estado=?
        WHERE id=?
    """,
        tuple(data) + (proveedor_id,),
    )
    conn.commit()
    conn.close()


def borrar_proveedor(pid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM proveedores WHERE id=?", (pid,))
    conn.commit()
    conn.close()


# -------------------- EMPLEADOS (NUEVO) --------------------


def listar_empleados():
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT id, nombre, apellido, dni_cuil, telefono, email,
               calle, numero, entre_calles, localidad, cp, provincia,
               puesto, fecha_ingreso, tel_emergencias, contacto_emergencias,
               estado, fecha_egreso
        FROM empleados
        ORDER BY id ASC
    """
    ).fetchall()
    conn.close()
    return rows


def obtener_empleado(emp_id: int):
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT id, nombre, apellido, dni_cuil, telefono, email,
               calle, numero, entre_calles, localidad, cp, provincia,
               puesto, fecha_ingreso, tel_emergencias, contacto_emergencias,
               estado, fecha_egreso
        FROM empleados
        WHERE id=?
    """,
        (emp_id,),
    ).fetchone()
    conn.close()
    return row


def agregar_empleado(data_tuple, id_manual: int | None = None):
    """
    data_tuple = (
      nombre, apellido, dni_cuil, telefono, email,
      calle, numero, entre_calles, localidad, cp, provincia,
      puesto, fecha_ingreso, tel_emergencias, contacto_emergencias,
      estado, fecha_egreso
    )
    """
    conn = get_conn()
    cur = conn.cursor()
    if id_manual is not None:
        cur.execute(
            """
            INSERT OR REPLACE INTO empleados(
              id, nombre, apellido, dni_cuil, telefono, email,
              calle, numero, entre_calles, localidad, cp, provincia,
              puesto, fecha_ingreso, tel_emergencias, contacto_emergencias,
              estado, fecha_egreso
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (id_manual, *data_tuple),
        )
    else:
        cur.execute(
            """
            INSERT INTO empleados(
              nombre, apellido, dni_cuil, telefono, email,
              calle, numero, entre_calles, localidad, cp, provincia,
              puesto, fecha_ingreso, tel_emergencias, contacto_emergencias,
              estado, fecha_egreso
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            data_tuple,
        )
    conn.commit()
    conn.close()


def editar_empleado(emp_id: int, data_tuple):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE empleados SET
          nombre=?, apellido=?, dni_cuil=?, telefono=?, email=?,
          calle=?, numero=?, entre_calles=?, localidad=?, cp=?, provincia=?,
          puesto=?, fecha_ingreso=?, tel_emergencias=?, contacto_emergencias=?,
          estado=?, fecha_egreso=?
        WHERE id=?
    """,
        (*data_tuple, emp_id),
    )
    conn.commit()
    conn.close()


def borrar_empleado(emp_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM empleados WHERE id=?", (emp_id,))
    conn.commit()
    conn.close()


# -------------------- CAJA --------------------


def agregar_movimiento(data, id_manual=None):
    """
    data: (fecha, tipo, medio, concepto, detalle, monto,
           tercero_tipo, tercero_id, estado, origen_tipo, origen_id,
           categoria_id, centro_costo_id, cuenta)
    También acepta dict con esas keys, o ([dict],).
    """
    cols = (
        "fecha",
        "tipo",
        "medio",
        "concepto",
        "detalle",
        "monto",
        "tercero_tipo",
        "tercero_id",
        "estado",
        "origen_tipo",
        "origen_id",
        "categoria_id",
        "centro_costo_id",
        "cuenta",
    )

    params = _as_params(data, cols)

    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(cols))
    if id_manual is not None:
        cur.execute(
            f"INSERT INTO movimientos_caja (id,{','.join(cols)}) VALUES ({id_manual},{placeholders})",
            params,
        )
    else:
        cur.execute(
            f"INSERT INTO movimientos_caja ({','.join(cols)}) VALUES ({placeholders})",
            params,
        )
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


# ===================== CAJA: helpers =====================


def caja_agregar(
    fecha,
    tipo,
    medio,
    concepto,
    detalle,
    monto,
    tercero_tipo,
    tercero_id,
    cuenta,
    origen_tipo=None,
    origen_id=None,
    id_manual=None,
) -> int:
    """
    Crea un movimiento en 'movimientos_caja' y devuelve su ID.
    tipo: 'ingreso' | 'egreso'
    tercero_tipo: 'cliente' | 'proveedor'
    cuenta: 'cuenta1' | 'cuenta2' | 1 | 2  (se normaliza a 1/2)
    """
    cuenta_n = 1 if str(cuenta).strip().lower().endswith("1") else 2
    conn = get_conn()
    cur = conn.cursor()

    cols = [
        "fecha",
        "tipo",
        "medio",
        "concepto",
        "detalle",
        "monto",
        "tercero_tipo",
        "tercero_id",
        "estado",
        "origen_tipo",
        "origen_id",
        "categoria_id",
        "centro_costo_id",
        "cuenta",
    ]
    placeholders = ",".join(["?"] * len(cols))
    params = (
        str(fecha or ""),
        str(tipo or "").lower(),
        str(medio or ""),
        str(concepto or ""),
        str(detalle or ""),
        float(monto or 0),
        str(tercero_tipo or "").lower(),
        int(tercero_id or 0),
        "ok",  # estado por defecto
        origen_tipo,
        origen_id,
        None,
        None,
        cuenta_n,
    )

    if id_manual is not None:
        cur.execute(
            f"INSERT INTO movimientos_caja (id,{','.join(cols)}) VALUES ({int(id_manual)},{placeholders})",
            params,
        )
    else:
        cur.execute(
            f"INSERT INTO movimientos_caja ({','.join(cols)}) VALUES ({placeholders})",
            params,
        )

    conn.commit()
    mov_id = cur.lastrowid
    conn.close()
    return mov_id


def caja_set_origen(mov_id: int, origen_tipo: str, origen_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE movimientos_caja SET origen_tipo=?, origen_id=? WHERE id=?",
        (str(origen_tipo or ""), int(origen_id or 0), int(mov_id)),
    )
    conn.commit()
    conn.close()


def caja_listar():
    """
    Devuelve filas para listar caja en UI.
    (id, fecha, tipo, tercero_tipo, tercero_id, concepto, detalle, monto, medio, origen_tipo, origen_id, cuenta)
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, fecha, tipo, tercero_tipo, tercero_id, concepto, detalle, monto, medio,
               origen_tipo, origen_id, cuenta
        FROM movimientos_caja
        ORDER BY date(fecha) DESC, id DESC
    """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def listar_movimientos():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, fecha, tipo, medio, concepto, detalle, monto,
                          tercero_tipo, tercero_id, estado, origen_tipo, origen_id,
                          categoria_id, centro_costo_id, cuenta
                   FROM movimientos_caja
                   ORDER BY date(fecha) DESC, id DESC"""
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def borrar_mov_caja(mov_id: int):
    """Borra un movimiento de caja y desengancha todas las referencias (cheques y CC)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        # Desvincular cheques que apuntan a este movimiento de caja
        cur.execute(
            "UPDATE cheques SET mov_caja_id=NULL WHERE mov_caja_id=?", (mov_id,)
        )
        # Desvincular CC (clientes y proveedores) que apuntan a este movimiento de caja
        for t in (
            "cc_clientes_c1",
            "cc_clientes_c2",
            "cc_proveedores_c1",
            "cc_proveedores_c2",
        ):
            cur.execute(
                f"UPDATE {t} SET caja_mov_id=NULL WHERE caja_mov_id=?", (mov_id,)
            )
        # Borrar el movimiento de caja
        cur.execute("DELETE FROM movimientos_caja WHERE id=?", (mov_id,))
        conn.commit()
    finally:
        conn.close()


# -------------------- CHEQUES --------------------


def agregar_cheque(data, id_manual=None):
    """
    data = (
        numero, banco, importe, fecha_recibido, fecha_cobro, cliente_id,
        firmante_nombre, firmante_cuit, estado, fecha_estado, obs,
        mov_caja_id, proveedor_id, cuenta_banco, gastos_bancarios, cuenta
    )
    También acepta dict con esas keys, o ([dict],).
    """
    cols = (
        "numero",
        "banco",
        "importe",
        "fecha_recibido",
        "fecha_cobro",
        "cliente_id",
        "firmante_nombre",
        "firmante_cuit",
        "estado",
        "fecha_estado",
        "obs",
        "mov_caja_id",
        "proveedor_id",
        "cuenta_banco",
        "gastos_bancarios",
        "cuenta",
    )

    params = _as_params(data, cols)

    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(cols))
    if id_manual is not None:
        cur.execute(
            f"INSERT INTO cheques (id,{','.join(cols)}) VALUES ({id_manual},{placeholders})",
            params,
        )
    else:
        cur.execute(
            f"INSERT INTO cheques ({','.join(cols)}) VALUES ({placeholders})", params
        )
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


def listar_cheques():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, numero, banco, importe, fecha_recibido, fecha_cobro, cliente_id,
               firmante_nombre, firmante_cuit, estado, fecha_estado, obs,
               mov_caja_id, proveedor_id, cuenta_banco, gastos_bancarios, cuenta
        FROM cheques
        ORDER BY
            CASE LOWER(COALESCE(estado,'')) WHEN 'en_cartera' THEN 0 ELSE 1 END,
            date(fecha_cobro) ASC,
            id ASC
    """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def listar_cheques_por_estado(estado: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, numero, banco, importe, fecha_recibido, fecha_cobro, cliente_id,
               firmante_nombre, firmante_cuit, estado, fecha_estado, obs,
               mov_caja_id, proveedor_id, cuenta_banco, gastos_bancarios, cuenta
        FROM cheques
        WHERE LOWER(COALESCE(estado,'')) = LOWER(?)
        ORDER BY date(fecha_cobro) ASC, id ASC
    """,
        (estado,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def listar_cheques_en_cartera():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, numero, banco, importe, fecha_cobro, cliente_id, firmante_nombre, firmante_cuit, cuenta
                   FROM cheques
                   WHERE LOWER(COALESCE(estado,''))='en_cartera'
                   ORDER BY date(fecha_cobro) ASC, id ASC"""
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def obtener_cheque(cid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, numero, banco, importe, fecha_recibido, fecha_cobro, cliente_id,
                          firmante_nombre, firmante_cuit, estado, fecha_estado, obs,
                          mov_caja_id, proveedor_id, cuenta_banco, gastos_bancarios, cuenta
                   FROM cheques WHERE id=?""",
        (cid,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def actualizar_estado_cheque(
    cid,
    nuevo_estado,
    fecha_estado,
    proveedor_id=None,
    cuenta_banco=None,
    gastos_bancarios=None,
):
    conn = get_conn()
    cur = conn.cursor()
    sets = ["estado=?", "fecha_estado=?"]
    params = [nuevo_estado, fecha_estado]
    if proveedor_id is not None:
        sets.append("proveedor_id=?")
        params.append(proveedor_id)
    if cuenta_banco is not None:
        sets.append("cuenta_banco=?")
        params.append(cuenta_banco)
    if gastos_bancarios is not None:
        sets.append("gastos_bancarios=?")
        params.append(gastos_bancarios)
    params.append(cid)
    cur.execute(f"UPDATE cheques SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    conn.close()


def set_mov_caja_en_cheque(cheque_id, mov_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.execute("UPDATE cheques SET mov_caja_id=? WHERE id=?", (mov_id, cheque_id))
    conn.commit()
    conn.close()


# -------------------- CC CLIENTES (C1/C2) --------------------


def _cli_table(cuenta):
    return "cc_clientes_c1" if str(cuenta).lower().endswith("1") else "cc_clientes_c2"


def cc_cli_agregar_mov(
    cuenta,
    fecha,
    cliente_id,
    doc,
    numero,
    concepto,
    medio,
    debe,
    haber,
    caja_mov_id=None,
    cheque_id=None,
    obs=None,
):
    table = _cli_table(cuenta)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {table}
        (fecha, cliente_id, doc, numero, concepto, medio, debe, haber, caja_mov_id, cheque_id, obs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,
        (
            fecha,
            int(cliente_id),
            doc,
            numero,
            concepto,
            medio,
            float(debe or 0),
            float(haber or 0),
            caja_mov_id,
            cheque_id,
            obs,
        ),
    )
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last

def cc_cli_agregar_con_caja(
    cuenta,
    fecha,
    cliente_id,
    doc,
    numero,
    concepto,
    medio,
    debe,
    haber,
    cuenta_caja=None,
    cheque_id=None,
    obs=None,
):
    """
    Crea CC Clientes + movimiento de Caja vinculado.
    Regla: si HABER > 0 -> ingreso; si DEBE > 0 -> egreso (casos raros como devolución).
    Devuelve (cc_id, caja_id).
    """
    # 1) Creo Caja primero
    monto = float(haber or 0) if float(haber or 0) > 0 else float(debe or 0)
    tipo_caja = "ingreso" if float(haber or 0) > 0 else "egreso"
    caja_id = caja_agregar(
        fecha=fecha,
        tipo=tipo_caja,
        medio=medio,
        concepto=concepto,
        detalle=f"{str(doc or '').upper()} {str(numero or '')}".strip(),
        monto=monto,
        tercero_tipo="cliente",
        tercero_id=cliente_id,
        cuenta=(cuenta_caja if cuenta_caja is not None else cuenta),
        origen_tipo=None,
        origen_id=None,
    )

    # 2) Inserto CC con el id de caja ya asignado
    table = "cc_clientes_c1" if str(cuenta).lower().endswith("1") else "cc_clientes_c2"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {table}
        (fecha, cliente_id, doc, numero, concepto, medio, debe, haber, caja_mov_id, cheque_id, obs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,
        (
            fecha,
            int(cliente_id),
            doc,
            numero,
            concepto,
            medio,
            float(debe or 0),
            float(haber or 0),
            int(caja_id),
            cheque_id,
            obs,
        ),
    )
    conn.commit()
    cc_id = cur.lastrowid
    conn.close()

    # 3) Actualizo el origen en Caja
    caja_set_origen(caja_id, "cc_cli", cc_id)
    return cc_id, caja_id


def cc_cli_listar(cliente_id, cuenta):
    conn = get_conn()
    cur = conn.cursor()
    if cuenta == "ambas":
        cur.execute(
            """
            SELECT 'C1' AS cta, id, fecha, cliente_id, doc, numero, concepto, medio, debe, haber
            FROM cc_clientes_c1 WHERE cliente_id=?
            UNION ALL
            SELECT 'C2' AS cta, id, fecha, cliente_id, doc, numero, concepto, medio, debe, haber
            FROM cc_clientes_c2 WHERE cliente_id=?
            ORDER BY date(fecha) DESC, id DESC
        """,
            (cliente_id, cliente_id),
        )
    else:
        table = _cli_table(cuenta)
        cur.execute(
            f"""
            SELECT '{'C1' if table.endswith('c1') else 'C2'}' AS cta, id, fecha, cliente_id, doc, numero, concepto, medio, debe, haber
            FROM {table} WHERE cliente_id=?
            ORDER BY date(fecha) DESC, id DESC
        """,
            (cliente_id,),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def _dmy_to_iso(s: str) -> str:
    try:
        d, m, y = s.strip().split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return s  # si ya viene ISO o algo raro, lo dejo pasar


def cc_cli_saldo(cliente_id, cuenta):
    conn = get_conn()
    cur = conn.cursor()

    def _sum(table):
        cur.execute(
            f"SELECT COALESCE(SUM(debe)-SUM(haber),0) FROM {table} WHERE cliente_id=?",
            (cliente_id,),
        )
        r = cur.fetchone()
        return float(r[0] or 0)

    if cuenta == "ambas":
        s = _sum("cc_clientes_c1") + _sum("cc_clientes_c2")
    else:
        s = _sum(_cli_table(cuenta))
    conn.close()
    return s


# -------- CC CLIENTES: editar / borrar / obtener por id --------


def _cli_table_by_flag(cuenta_flag: str):
    c = (cuenta_flag or "").strip().lower()
    return "cc_clientes_c1" if c.endswith("1") else "cc_clientes_c2"


def cc_cli_obtener(cuenta_flag: str, mov_id: int):
    table = _cli_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, fecha, cliente_id, doc, numero, concepto, medio,
               COALESCE(debe,0), COALESCE(haber,0),
               caja_mov_id, cheque_id, obs
        FROM {table} WHERE id=?
    """,
        (mov_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


# --- CLIENTES ---


def cc_cli_actualizar(cuenta_flag: str, mov_id: int, data):
    """
    data = (fecha, doc, numero, concepto, medio, debe, haber, obs)
    """
    table = _cli_table_by_flag(cuenta_flag)  # -> "cc_clientes_c1" / "cc_clientes_c2"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE {table}
        SET fecha=?, doc=?, numero=?, concepto=?, medio=?, debe=?, haber=?, obs=?
        WHERE id=?
    """,
        (*data, mov_id),
    )
    conn.commit()
    conn.close()


def cc_cli_borrar(cuenta_flag: str, mov_id: int):
    table = _cli_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {table} WHERE id=?", (mov_id,))
    conn.commit()
    conn.close()


def cc_cli_borrar_cascada(cuenta_flag: str, mov_id: int):
    """Borra una fila de CC Clientes y, si corresponde, su mov. de caja y/o cheque."""
    table = _cli_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT caja_mov_id, cheque_id FROM {table} WHERE id=?", (mov_id,))
        row = cur.fetchone()
        caja_id = row[0] if row else None
        cheque_id = row[1] if row else None
    finally:
        conn.close()

    # Borrar primero caja (si existe) porque ya se encargará de desenganchar referencias
    if caja_id:
        borrar_mov_caja(int(caja_id))

    # Borrar cheque (si quedó alguno suelto)
    conn = get_conn()
    cur = conn.cursor()
    try:
        if cheque_id:
            cur.execute("DELETE FROM cheques WHERE id=?", (cheque_id,))
        cur.execute(f"DELETE FROM {table} WHERE id=?", (mov_id,))
        conn.commit()
    finally:
        conn.close()


# -------- CC PROVEEDORES: editar / borrar / obtener por id --------


def _prov_table_by_flag(cuenta_flag: str):
    c = (cuenta_flag or "").strip().lower()
    return "cc_proveedores_c1" if c.endswith("1") else "cc_proveedores_c2"


def cc_prov_obtener(cuenta_flag: str, mov_id: int):
    table = _prov_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, fecha, proveedor_id, doc, numero, concepto, medio,
               COALESCE(debe,0), COALESCE(haber,0),
               caja_mov_id, cheque_id, obs
        FROM {table} WHERE id=?
    """,
        (mov_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


# --- PROVEEDORES ---


def cc_prov_actualizar(cuenta_flag: str, mov_id: int, data):
    """
    data = (fecha, doc, numero, concepto, medio, debe, haber, obs)
    """
    table = _prov_table_by_flag(
        cuenta_flag
    )  # -> "cc_proveedores_c1" / "cc_proveedores_c2"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE {table}
        SET fecha=?, doc=?, numero=?, concepto=?, medio=?, debe=?, haber=?, obs=?
        WHERE id=?
    """,
        (*data, mov_id),
    )
    conn.commit()
    conn.close()


def cc_prov_borrar(cuenta_flag: str, mov_id: int):
    table = _prov_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {table} WHERE id=?", (mov_id,))
    conn.commit()
    conn.close()


def cc_prov_borrar_cascada(cuenta_flag: str, mov_id: int):
    """Borra una fila de CC Proveedores y, si corresponde, su mov. de caja y/o cheque."""
    table = _prov_table_by_flag(cuenta_flag)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT caja_mov_id, cheque_id FROM {table} WHERE id=?", (mov_id,))
        row = cur.fetchone()
        caja_id = row[0] if row else None
        cheque_id = row[1] if row else None
    finally:
        conn.close()

    if caja_id:
        borrar_mov_caja(int(caja_id))

    conn = get_conn()
    cur = conn.cursor()
    try:
        if cheque_id:
            cur.execute("DELETE FROM cheques WHERE id=?", (cheque_id,))
        cur.execute(f"DELETE FROM {table} WHERE id=?", (mov_id,))
        conn.commit()
    finally:
        conn.close()


# -------- CAJA: borrado forzado (desvincula CC y Cheques) --------


def borrar_movimiento_caja_forzado(mov_id: int):
    """
    - Setea a NULL los vínculos hacia este mov:
      * cheques.mov_caja_id
      * cc_clientes_c1/2.caja_mov_id
      * cc_proveedores_c1/2.caja_mov_id
    - Borra el movimiento de caja.
    Nota: NO cambia estado de cheques ni borra líneas de CC (solo los desvincula).
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=ON;")
        # Desvincular cheques
        cur.execute(
            "UPDATE cheques SET mov_caja_id=NULL WHERE mov_caja_id=?", (mov_id,)
        )
        # Desvincular CC
        for t in (
            "cc_clientes_c1",
            "cc_clientes_c2",
            "cc_proveedores_c1",
            "cc_proveedores_c2",
        ):
            cur.execute(
                f"UPDATE {t} SET caja_mov_id=NULL WHERE caja_mov_id=?", (mov_id,)
            )
        # Borrar movimiento
        cur.execute("DELETE FROM movimientos_caja WHERE id=?", (mov_id,))
        conn.commit()
    finally:
        conn.close()


# -------------------- CC PROVEEDORES (C1/C2) --------------------


def _prov_table(cuenta):
    return (
        "cc_proveedores_c1"
        if str(cuenta).lower().endswith("1")
        else "cc_proveedores_c2"
    )


def cc_prov_agregar_mov(
    cuenta,
    fecha,
    proveedor_id,
    doc,
    numero,
    concepto,
    medio,
    debe,
    haber,
    caja_mov_id=None,
    cheque_id=None,
    obs=None,
):
    table = _prov_table(cuenta)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {table}
        (fecha, proveedor_id, doc, numero, concepto, medio, debe, haber, caja_mov_id, cheque_id, obs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,
        (
            fecha,
            int(proveedor_id),
            doc,
            numero,
            concepto,
            medio,
            float(debe or 0),
            float(haber or 0),
            caja_mov_id,
            cheque_id,
            obs,
        ),
    )
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


def cc_prov_agregar_con_caja(
    cuenta,
    fecha,
    proveedor_id,
    doc,
    numero,
    concepto,
    medio,
    debe,
    haber,
    cuenta_caja=None,
    cheque_id=None,
    obs=None,
):
    """
    Crea CC Proveedores + movimiento de Caja vinculado.
    Regla: si HABER > 0 -> egreso (pago); si DEBE > 0 -> ingreso (nota de crédito recibida, etc).
    Devuelve (cc_id, caja_id).
    """
    monto = float(haber or 0) if float(haber or 0) > 0 else float(debe or 0)
    tipo_caja = "egreso" if float(haber or 0) > 0 else "ingreso"
    caja_id = caja_agregar(
        fecha=fecha,
        tipo=tipo_caja,
        medio=medio,
        concepto=concepto,
        detalle=f"{str(doc or '').upper()} {str(numero or '')}".strip(),
        monto=monto,
        tercero_tipo="proveedor",
        tercero_id=proveedor_id,
        cuenta=(cuenta_caja if cuenta_caja is not None else cuenta),
        origen_tipo=None,
        origen_id=None,
    )

    table = (
        "cc_proveedores_c1"
        if str(cuenta).lower().endswith("1")
        else "cc_proveedores_c2"
    )
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {table}
        (fecha, proveedor_id, doc, numero, concepto, medio, debe, haber, caja_mov_id, cheque_id, obs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,
        (
            fecha,
            int(proveedor_id),
            doc,
            numero,
            concepto,
            medio,
            float(debe or 0),
            float(haber or 0),
            int(caja_id),
            cheque_id,
            obs,
        ),
    )
    conn.commit()
    cc_id = cur.lastrowid
    conn.close()

    caja_set_origen(caja_id, "cc_prov", cc_id)
    return cc_id, caja_id


def cc_prov_listar(proveedor_id, cuenta):
    conn = get_conn()
    cur = conn.cursor()
    if cuenta == "ambas":
        cur.execute(
            """
            SELECT 'C1' AS cta, id, fecha, proveedor_id, doc, numero, concepto, medio, debe, haber
            FROM cc_proveedores_c1 WHERE proveedor_id=?
            UNION ALL
            SELECT 'C2' AS cta, id, fecha, proveedor_id, doc, numero, concepto, medio, debe, haber
            FROM cc_proveedores_c2 WHERE proveedor_id=?
            ORDER BY date(fecha) DESC, id DESC
        """,
            (proveedor_id, proveedor_id),
        )
    else:
        table = _prov_table(cuenta)
        cur.execute(
            f"""
            SELECT '{'C1' if table.endswith('c1') else 'C2'}' AS cta, id, fecha, proveedor_id, doc, numero, concepto, medio, debe, haber
            FROM {table} WHERE proveedor_id=?
            ORDER BY date(fecha) DESC, id DESC
        """,
            (proveedor_id,),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def cc_prov_saldo(proveedor_id, cuenta):
    conn = get_conn()
    cur = conn.cursor()

    def _sum(table):
        cur.execute(
            f"SELECT COALESCE(SUM(debe)-SUM(haber),0) FROM {table} WHERE proveedor_id=?",
            (proveedor_id,),
        )
        r = cur.fetchone()
        return float(r[0] or 0)

    if cuenta == "ambas":
        s = _sum("cc_proveedores_c1") + _sum("cc_proveedores_c2")
    else:
        s = _sum(_prov_table(cuenta))
    conn.close()
    return s


# ================== Helpers extra / Resets ==================


def _filtrar_por_fecha(movs, fecha_desde=None, fecha_hasta=None):
    """
    movs: lista de tuplas (id, fecha, doc, numero, concepto, medio, debe, haber)
    fecha_desde / fecha_hasta: 'YYYY-MM-DD' o None
    Devuelve los movimientos dentro del rango (inclusive).
    """
    if not (fecha_desde or fecha_hasta):
        return movs

    def ok(fecha):
        f = (str(fecha) or "").strip()
        if fecha_desde and f < fecha_desde:
            return False
        if fecha_hasta and f > fecha_hasta:
            return False
        return True

    return [m for m in movs if ok(m[1])]


# -------------------- DIVISAS (USD) --------------------


def agregar_divisa_compra(
    fecha, usd, tc, pesos, cuenta=None, mov_caja_id=None, obs=None
):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO divisas (fecha, tipo, usd, tc, pesos, cuenta, mov_caja_id, obs)
        VALUES (?, 'compra', ?, ?, ?, ?, ?, ?)
    """,
        (
            fecha,
            float(usd or 0),
            float(tc or 0),
            float(pesos or 0),
            cuenta,
            mov_caja_id,
            obs,
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def listar_divisas(tipo=None):
    conn = get_conn()
    cur = conn.cursor()
    if tipo:
        cur.execute(
            """
            SELECT id, fecha, tipo, usd, tc, pesos, cuenta, mov_caja_id, obs
            FROM divisas
            WHERE LOWER(tipo)=LOWER(?)
            ORDER BY date(fecha) DESC, id DESC
        """,
            (tipo,),
        )
    else:
        cur.execute(
            """
            SELECT id, fecha, tipo, usd, tc, pesos, cuenta, mov_caja_id, obs
            FROM divisas
            ORDER BY date(fecha) DESC, id DESC
        """
        )
    rows = cur.fetchall()
    conn.close()
    return rows


# (OJO) No duplicar obtener_proveedor/obtener_cliente – ya definidos arriba con columnas explícitas


def reset_tablas(que="todo"):
    conn = get_conn()
    cur = conn.cursor()
    if que in ("todo", "caja"):
        cur.execute("DELETE FROM movimientos_caja")
    if que in ("todo", "cheques"):
        cur.execute("DELETE FROM cheques")
    if que in ("todo", "cc_clientes"):
        cur.execute("DELETE FROM cc_clientes_c1")
        cur.execute("DELETE FROM cc_clientes_c2")
    if que in ("todo", "cc_proveedores"):
        cur.execute("DELETE FROM cc_proveedores_c1")
        cur.execute("DELETE FROM cc_proveedores_c2")
    conn.commit()
    conn.close()


def reset_cc_cliente(cliente_id: int, cuenta: str = "ambas") -> int:
    """
    Borra los movimientos de CC del cliente indicado.
    cuenta: 'cuenta1' | 'cuenta2' | 'ambas' (default)
    Devuelve la cantidad de filas borradas.
    """
    conn = get_conn()
    cur = conn.cursor()
    total = 0
    c = (cuenta or "ambas").strip().lower()
    try:
        if c in ("cuenta1", "1"):
            cur.execute("DELETE FROM cc_clientes_c1 WHERE cliente_id=?", (cliente_id,))
            total += cur.rowcount if cur.rowcount != -1 else 0
        elif c in ("cuenta2", "2"):
            cur.execute("DELETE FROM cc_clientes_c2 WHERE cliente_id=?", (cliente_id,))
            total += cur.rowcount if cur.rowcount != -1 else 0
        else:
            cur.execute("DELETE FROM cc_clientes_c1 WHERE cliente_id=?", (cliente_id,))
            total += cur.rowcount if cur.rowcount != -1 else 0
            cur.execute("DELETE FROM cc_clientes_c2 WHERE cliente_id=?", (cliente_id,))
            total += cur.rowcount if cur.rowcount != -1 else 0
        conn.commit()
        return int(total)
    finally:
        conn.close()


def reset_cc_proveedor(proveedor_id: int, cuenta: str = "ambas") -> int:
    """
    Borra los movimientos de CC del proveedor indicado.
    cuenta: 'cuenta1' | 'cuenta2' | 'ambas' (default)
    Devuelve la cantidad de filas borradas.
    """
    conn = get_conn()
    cur = conn.cursor()
    total = 0
    c = (cuenta or "ambas").strip().lower()
    try:
        if c in ("cuenta1", "1"):
            cur.execute(
                "DELETE FROM cc_proveedores_c1 WHERE proveedor_id=?", (proveedor_id,)
            )
            total += cur.rowcount if cur.rowcount != -1 else 0
        elif c in ("cuenta2", "2"):
            cur.execute(
                "DELETE FROM cc_proveedores_c2 WHERE proveedor_id=?", (proveedor_id,)
            )
            total += cur.rowcount if cur.rowcount != -1 else 0
        else:
            cur.execute(
                "DELETE FROM cc_proveedores_c1 WHERE proveedor_id=?", (proveedor_id,)
            )
            total += cur.rowcount if cur.rowcount != -1 else 0
            cur.execute(
                "DELETE FROM cc_proveedores_c2 WHERE proveedor_id=?", (proveedor_id,)
            )
            total += cur.rowcount if cur.rowcount != -1 else 0
        conn.commit()
        return int(total)
    finally:
        conn.close()


# ====== REPORTES CC: OBTENER MOVS, CORTAR POR SALDO Y PDF ======


def ensure_folder(path: str):
    os.makedirs(path, exist_ok=True)
    return path


def get_cliente_nombre(conn, cliente_id: int) -> str:
    try:
        row = conn.execute(
            "SELECT razon_social FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
        return row[0] if row and row[0] else f"Cliente {cliente_id}"
    except Exception:
        return f"Cliente {cliente_id}"


def get_proveedor_nombre(conn, proveedor_id: int) -> str:
    try:
        row = conn.execute(
            "SELECT razon_social FROM proveedores WHERE id = ?", (proveedor_id,)
        ).fetchone()
        return row[0] if row and row[0] else f"Proveedor {proveedor_id}"
    except Exception:
        return f"Proveedor {proveedor_id}"


def _cc_rows_for_cliente(conn, cliente_id: int, cuenta: int):
    table = "cc_clientes_c1" if int(cuenta) == 1 else "cc_clientes_c2"
    q = f"""
        SELECT id, fecha, doc, numero, concepto, medio,
               COALESCE(debe,0) AS debe, COALESCE(haber,0) AS haber
        FROM {table}
        WHERE cliente_id = ?
        ORDER BY date(fecha) ASC, id ASC
    """
    return conn.execute(q, (cliente_id,)).fetchall()


def _cc_rows_for_proveedor(conn, proveedor_id: int, cuenta: int):
    table = "cc_proveedores_c1" if int(cuenta) == 1 else "cc_proveedores_c2"
    q = f"""
        SELECT id, fecha, doc, numero, concepto, medio,
               COALESCE(debe,0) AS debe, COALESCE(haber,0) AS haber
        FROM {table}
        WHERE proveedor_id = ?
        ORDER BY date(fecha) ASC, id ASC
    """
    return conn.execute(q, (proveedor_id,)).fetchall()


def get_saldo_actual_cc_cliente(conn, cliente_id: int, cuenta: int) -> float:
    table = "cc_clientes_c1" if int(cuenta) == 1 else "cc_clientes_c2"
    row = conn.execute(
        f"SELECT COALESCE(SUM(debe)-SUM(haber),0) FROM {table} WHERE cliente_id=?",
        (cliente_id,),
    ).fetchone()
    return float(row[0] or 0.0)


def get_saldo_actual_cc_proveedor(conn, proveedor_id: int, cuenta: int) -> float:
    table = "cc_proveedores_c1" if int(cuenta) == 1 else "cc_proveedores_c2"
    row = conn.execute(
        f"SELECT COALESCE(SUM(debe)-SUM(haber),0) FROM {table} WHERE proveedor_id=?",
        (proveedor_id,),
    ).fetchone()
    return float(row[0] or 0.0)


def slice_movs_desde_debe_que_cubre_saldo(movs, saldo_objetivo: float):
    """
    movs esperado: lista de tuplas con (id, fecha, doc, numero, concepto, medio, debe, haber)
    Devuelve sublista desde el primer DEBE acumulado que cubre 'saldo_objetivo' hasta el final.
    """
    if not movs:
        return []

    if saldo_objetivo <= 0:
        # Si no hay saldo, devolvemos vacío o solo el día actual como contexto
        hoy = datetime.now().strftime("%Y-%m-%d")
        tail = [m for m in movs if (str(m[1]) or "").startswith(hoy)]
        return tail if tail else []

    acumulado = 0.0
    for idx in range(len(movs) - 1, -1, -1):
        debe = float(movs[idx][6] or 0)
        acumulado += debe
        if acumulado + 1e-9 >= saldo_objetivo:
            return movs[idx:]
    return movs


def _pdf_table_story_for_movs(movs):
    # Convierte filas CC en data para reportlab (con saldo parcial)
    data = [["Fecha", "Comprobante", "Detalle", "Debe", "Haber", "Saldo"]]
    saldo_parcial = 0.0
    for _id, fecha, doc, numero, concepto, medio, debe, haber in movs:
        saldo_parcial += float(debe or 0) - float(haber or 0)
        comp = f"{doc.upper()} {numero or ''}".strip()
        data.append(
            [
                _fmt_dmy(fecha) or "",
                comp,
                str(concepto or ""),
                f"{float(debe or 0):,.2f}",
                f"{float(haber or 0):,.2f}",
                f"{saldo_parcial:,.2f}",
            ]
        )

    return data


def crear_pdf_cc_cliente(
    conn, cliente_id: int, cuenta: int = 1, output_base: str = "reportes/cc_clientes"
) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError as e:
        raise RuntimeError(
            "Falta 'reportlab'. Instalá con: pip install reportlab"
        ) from e

    saldo = get_saldo_actual_cc_cliente(conn, cliente_id, cuenta)
    movs = _cc_rows_for_cliente(conn, cliente_id, cuenta)
    sub = slice_movs_desde_debe_que_cubre_saldo(movs, saldo)

    ensure_folder(output_base)
    fecha_str = datetime.now().strftime("%Y%m%d")
    filename = os.path.join(output_base, f"{cliente_id}-{cuenta}-{fecha_str}.pdf")

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=25,
        bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Resumen Cuenta Corriente (Cliente)", styles["Title"]))
    story.append(
        Paragraph(
            f"<b>Cliente ID:</b> {cliente_id} &nbsp;&nbsp; <b>Cuenta:</b> {cuenta}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Generado:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            styles["Normal"],
        )
    )
    story.append(Paragraph(f"<b>Saldo actual:</b> {saldo:,.2f}", styles["Normal"]))
    story.append(Spacer(1, 10))

    data = _pdf_table_story_for_movs(sub)
    #            Fecha  Comp.  Detalle  Debe Haber Saldo
    tbl = Table(data, repeatRows=1, colWidths=[55, 130, 135, 65, 65, 65])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )
    story.append(tbl)

    story.append(Spacer(1, 10))
    tot_debe = sum(float(m[6] or 0) for m in sub)
    tot_haber = sum(float(m[7] or 0) for m in sub)
    story.append(
        Paragraph(
            f"<b>Total Debe (período):</b> {tot_debe:,.2f} &nbsp;&nbsp; "
            f"<b>Total Haber (período):</b> {tot_haber:,.2f}",
            styles["Normal"],
        )
    )

    doc.build(story)
    return filename


def crear_pdf_cc_proveedor(
    conn,
    proveedor_id: int,
    cuenta: int = 1,
    output_base: str = "reportes/cc_proveedores",
) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError as e:
        raise RuntimeError(
            "Falta 'reportlab'. Instalá con: pip install reportlab"
        ) from e

    saldo = get_saldo_actual_cc_proveedor(conn, proveedor_id, cuenta)
    movs = _cc_rows_for_proveedor(conn, proveedor_id, cuenta)
    sub = slice_movs_desde_debe_que_cubre_saldo(movs, saldo)

    ensure_folder(output_base)
    fecha_str = datetime.now().strftime("%Y%m%d")
    filename = os.path.join(output_base, f"{proveedor_id}-{cuenta}-{fecha_str}.pdf")

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=25,
        bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Resumen Cuenta Corriente (Proveedor)", styles["Title"]))
    story.append(
        Paragraph(
            f"<b>Proveedor ID:</b> {proveedor_id} &nbsp;&nbsp; <b>Cuenta:</b> {cuenta}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Generado:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            styles["Normal"],
        )
    )
    story.append(Paragraph(f"<b>Saldo actual:</b> {saldo:,.2f}", styles["Normal"]))
    story.append(Spacer(1, 10))

    data = _pdf_table_story_for_movs(sub)
    tbl = Table(data, repeatRows=1, colWidths=[55, 130, 135, 65, 65, 65])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )
    story.append(tbl)

    story.append(Spacer(1, 10))
    tot_debe = sum(float(m[6] or 0) for m in sub)
    tot_haber = sum(float(m[7] or 0) for m in sub)
    story.append(
        Paragraph(
            f"<b>Total Debe (período):</b> {tot_debe:,.2f} &nbsp;&nbsp; "
            f"<b>Total Haber (período):</b> {tot_haber:,.2f}",
            styles["Normal"],
        )
    )

    doc.build(story)
    return filename


def crear_pdf_cc_cliente_rango(
    conn,
    cliente_id: int,
    cuenta: int = 1,
    fecha_desde: str = None,
    fecha_hasta: str = None,
    output_base: str = "reportes/cc_clientes",
) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError as e:
        raise RuntimeError(
            "Falta 'reportlab'. Instalá con: pip install reportlab"
        ) from e

    movs = _cc_rows_for_cliente(conn, cliente_id, cuenta)  # todos (asc)
    movs = _filtrar_por_fecha(movs, fecha_desde, fecha_hasta)

    ensure_folder(output_base)
    fecha_str = datetime.now().strftime("%Y%m%d")
    suf_rango = f"{(fecha_desde or 'ini').replace('-', '')}-{(fecha_hasta or 'hoy').replace('-', '')}"
    filename = os.path.join(
        output_base, f"{cliente_id}-{cuenta}-hist-{suf_rango}-{fecha_str}.pdf"
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=25,
        bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(
        Paragraph("Resumen CC (Cliente) — Histórico por fechas", styles["Title"])
    )
    story.append(
        Paragraph(
            f"<b>Cliente ID:</b> {cliente_id} &nbsp;&nbsp; <b>Cuenta:</b> {cuenta}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Rango:</b> {fecha_desde or '—'} a {fecha_hasta or '—'}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Generado:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 10))

    data = _pdf_table_story_for_movs(movs)
    tbl = Table(data, repeatRows=1, colWidths=[55, 130, 135, 65, 65, 65])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )
    story.append(tbl)

    story.append(Spacer(1, 10))
    tot_debe = sum(float(m[6] or 0) for m in movs)
    tot_haber = sum(float(m[7] or 0) for m in movs)
    story.append(
        Paragraph(
            f"<b>Total Debe (rango):</b> {tot_debe:,.2f} &nbsp;&nbsp; "
            f"<b>Total Haber (rango):</b> {tot_haber:,.2f}",
            styles["Normal"],
        )
    )

    doc.build(story)
    return filename


def crear_pdf_cc_proveedor_rango(
    conn,
    proveedor_id: int,
    cuenta: int = 1,
    fecha_desde: str = None,
    fecha_hasta: str = None,
    output_base: str = "reportes/cc_proveedores",
) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError as e:
        raise RuntimeError(
            "Falta 'reportlab'. Instalá con: pip install reportlab"
        ) from e

    movs = _cc_rows_for_proveedor(conn, proveedor_id, cuenta)  # todos (asc)
    movs = _filtrar_por_fecha(movs, fecha_desde, fecha_hasta)

    ensure_folder(output_base)
    fecha_str = datetime.now().strftime("%Y%m%d")
    suf_rango = f"{(fecha_desde or 'ini').replace('-', '')}-{(fecha_hasta or 'hoy').replace('-', '')}"
    filename = os.path.join(
        output_base, f"{proveedor_id}-{cuenta}-hist-{suf_rango}-{fecha_str}.pdf"
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=25,
        bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(
        Paragraph("Resumen CC (Proveedor) — Histórico por fechas", styles["Title"])
    )
    story.append(
        Paragraph(
            f"<b>Proveedor ID:</b> {proveedor_id} &nbsp;&nbsp; <b>Cuenta:</b> {cuenta}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Rango:</b> {fecha_desde or '—'} a {fecha_hasta or '—'}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Generado:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 10))

    data = _pdf_table_story_for_movs(movs)
    tbl = Table(data, repeatRows=1, colWidths=[55, 130, 135, 65, 65, 65])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )
    story.append(tbl)

    story.append(Spacer(1, 10))
    tot_debe = sum(float(m[6] or 0) for m in movs)
    tot_haber = sum(float(m[7] or 0) for m in movs)
    story.append(
        Paragraph(
            f"<b>Total Debe (rango):</b> {tot_debe:,.2f} &nbsp;&nbsp; "
            f"<b>Total Haber (rango):</b> {tot_haber:,.2f}",
            styles["Normal"],
        )
    )

    doc.build(story)
    return filename
