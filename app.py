# app.py â€” GestiÃ³n Textil (operativa v2.4 puntual)
# -------------------------------------------------
# Cambios clave:
# - CC Clientes/Proveedores: SIEMPRE generar PDF (REC/OP) desde el flujo de CC.
# - Cheques de REC: guardan obs="REC {numero}" para poder ver y borrar por recibo.
# - Selector de cheques para OP: lista â€œen carteraâ€ aunque el texto varÃ­e.
# - ChequesTab: (en 3/6) agrega columna "recibo" usando obs.
# - Enviar CC y demÃ¡s pestaÃ±as quedan compatibles con tu esquema.
# -------------------------------------------------

# -*- coding: utf-8 -*-

import os
import csv
from datetime import date
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import db_access as db
import tkinter.messagebox as mbox   # alias seguro para evitar sombras
from tkinter import filedialog      # ya lo usás en otros lados
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

APP_TITLE = "textil LAMADRID"

try:
    from db_access import DB_PATH
except Exception:
    from pathlib import Path

    DB_PATH = Path("gestion_textil.db")

LOGO_FILE = os.path.join(os.getcwd(), "logo.png")

# ------------------------------- DEBUG SQL ------------------------------
DEBUG_SQL = False
if DEBUG_SQL:
    try:
        _orig_get_conn = db.get_conn

        def _dbg_get_conn():
            c = _orig_get_conn()
            try:
                c.set_trace_callback(print)
            except Exception:
                pass
            return c

        db.get_conn = _dbg_get_conn
    except Exception:
        pass

# -------------------------------- utils --------------------------------


def today_str():
    return date.today().strftime("%Y-%m-%d")


def _money(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "0.00"


# --- Helpers robustos para importaciÃ³n CSV ---


def _strip_bom(s: str) -> str:
    return (s or "").lstrip("\ufeff").strip()


def _parse_date_flexible(s: str) -> str:
    """
    Acepta 'YYYY-MM-DD', 'DD/MM/YYYY', 'DD-MM-YYYY', 'YYYY/MM/DD'
    y devuelve siempre 'YYYY-MM-DD'. Si no entiende, devuelve tal cual.
    """
    s = _strip_bom(s)
    if not s:
        return today_str()
    try:
        # ya viene OK
        if len(s) == 10 and s[4] in "-/" and s[7] in "-/":
            y, m, d = s.replace("/", "-").split("-")
            if len(y) == 4:
                return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        pass
    # dd/mm/yyyy
    try:
        if "/" in s or "-" in s:
            p = s.replace("/", "-").split("-")
            if len(p) == 3:
                a, b, c = p
                # heurÃ­stica: si el primer token tiene 4 dÃ­gitos, es aÃ±o
                if len(a) == 4:
                    y, m, d = int(a), int(b), int(c)
                else:
                    d, m, y = int(a), int(b), int(c)
                from datetime import date

                _ = date(y, m, d)  # valida
                return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return s  # fallback


def _parse_float_flexible(x):
    """
    Convierte a float admitiendo: signos, $/espacios, miles con . o , y decimal con . o ,
    TambiÃ©n '(123,45)' como negativo.
    """
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    # quitar sÃ­mbolos
    s = s.replace("$", "").replace(" ", "").replace("\u00a0", "")
    # si tiene coma y punto:
    if "," in s and "." in s:
        # si la coma estÃ¡ despuÃ©s del punto â†’ coma decimal (ej: 1.234,56)
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")  # saca miles
            s = s.replace(",", ".")  # decimal
        else:
            s = s.replace(",", "")  # saca miles
            # el punto queda como decimal
    elif "," in s:
        # solo coma â†’ tratamos como decimal
        s = s.replace(",", ".")
    # cualquier otro caso: queda el punto como decimal
    try:
        val = float(s)
        return -val if neg else val
    except Exception:
        return 0.0


def _norm_header_key(k: str) -> str:
    """
    Normaliza encabezados a claves conocidas.
    Soporta sinÃ³nimos frecuentes.
    """
    k0 = _strip_bom(k).lower().strip()
    mapping = {
        "fecha": "fecha",
        "fch": "fecha",
        "id": "id",
        "cliente": "id",
        "cliente_id": "id",
        "proveedor": "id",
        "proveedor_id": "id",
        "entidad": "id",
        "doc": "doc",
        "documento": "doc",
        "nro": "numero",
        "numero": "numero",
        "comprobante": "numero",
        "concepto": "concepto",
        "detalle": "concepto",
        "medio": "medio",
        "medio_pago": "medio",
        "medio_de_pago": "medio",
        "pago": "medio",
        "debe": "debe",
        "haber": "haber",
        "obs": "obs",
        "observacion": "obs",
        "observaciones": "obs",
    }
    return mapping.get(k0, k0)


def _norm_medio(v: str) -> str:
    v = _strip_bom(v).lower()
    if v in ("efectivo", "cheque", "banco", "otro"):
        return v
    # normalizaciones suaves
    if v in (
        "transferencia",
        "depÃ³sito",
        "deposito",
        "transf",
        "bank",
        "cta cte",
        "ctacte",
        "cbu",
        "cvu",
    ):
        return "banco"
    if v in ("cheques", "ch", "chq"):
        return "cheque"
    if not v:
        return "otro"
    return "otro"


def _es_activo(estado: str) -> bool:
    s = (estado or "").strip().lower()
    return s in ("activo", "activa", "ok", "habilitado", "habilitada", "vigente")


def _is_en_cartera(estado: str) -> bool:
    """
    Normaliza el estado para detectar 'en cartera' aunque venga con espacios/guiones/underscores o mayÃºsculas.
    """
    s = (estado or "").strip().lower()
    s = s.replace(" ", "").replace("_", "").replace("-", "")
    return s in ("encartera", "cartera")


# -------- documento: texto UI â†’ cÃ³digo CC (y viceversa si hiciera falta) -----

_DOC_TO_CODE = {
    "recibo": "REC",
    "orden de pago": "OP",
    "factura": "FAC",
    "remito": "REM",
    "nota de crÃ©dito": "NC",
    "nota de credito": "NC",
    "nota de dÃ©bito": "ND",
    "nota de debito": "ND",
    "ajuste (+)": "AJ+",
    "ajuste (-)": "AJ-",
    "mov": "MOV",
}


def _doc_to_code(doc_ui: str) -> str:
    return _DOC_TO_CODE.get(
        (doc_ui or "").strip().lower(), (doc_ui or "").strip().upper() or "MOV"
    )


def _destino_monto(tipo: str, doc_code: str) -> str:
    """
    Decide si el monto va a Debe/Haber segÃºn tipo de CC y documento.
    - CLIENTES: REC/NC/AJ+ => HABER; resto DEBE
    - PROVEEDORES: OP/NC/AJ+ => HABER; resto DEBE
    """
    d = (doc_code or "").upper()
    if (tipo or "").lower() == "clientes":
        if d in ("REC", "NC", "AJ+"):
            return "haber"
        return "debe"
    else:
        if d in ("OP", "NC", "AJ+"):
            return "haber"
        return "debe"


def _decide_destino_monto(tipo: str, doc_code: str) -> str:
    # Alias por compatibilidad (algunos flujos antiguos lo llaman asÃ­)
    return _destino_monto(tipo, doc_code)


# ---------- PDF helpers (recibo, OP, resumen CC) con fallback reportlab --------


def _emitir_recibo_pdf(path, numero, fecha, cliente, concepto, medio, importe, cheques):
    """
    Intenta usar reports.pdf_recibo; si no existe, genera un PDF simple como fallback.
    """
    try:
        import importlib

        reports = importlib.import_module("reports")
        if hasattr(reports, "pdf_recibo"):
            return reports.pdf_recibo(
                path, numero, fecha, cliente, concepto, medio, importe, cheques=cheques
            )
        else:
            raise AttributeError(
                f"reports sin pdf_recibo (ruta: {getattr(reports,'__file__','?')})"
            )
    except Exception as e:
        # Fallback simple con reportlab
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import (
                SimpleDocTemplate,
                Paragraph,
                Spacer,
                Table,
                TableStyle,
            )
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib import colors

            doc = SimpleDocTemplate(
                path,
                pagesize=A4,
                leftMargin=40,
                rightMargin=40,
                topMargin=40,
                bottomMargin=40,
            )
            styles = getSampleStyleSheet()
            elems = []
            elems.append(Paragraph(f"RECIBO NÂº {numero}", styles["Title"]))
            elems.append(Paragraph(f"Fecha: {fecha}", styles["Normal"]))
            elems.append(Spacer(1, 6))
            elems.append(
                Paragraph(
                    f"Recibimos de: {cliente.get('rs','')} â€” CUIT/DNI: {cliente.get('cuit','')}",
                    styles["Normal"],
                )
            )
            elems.append(
                Paragraph(f"Domicilio: {cliente.get('dir','')}", styles["Normal"])
            )
            elems.append(Spacer(1, 6))
            elems.append(Paragraph(f"Concepto: {concepto}", styles["Normal"]))
            elems.append(Paragraph(f"Medio de pago: {medio}", styles["Normal"]))
            elems.append(
                Paragraph(f"Importe: ${float(importe):,.2f}", styles["Heading3"])
            )

            if cheques:
                data = [["NÂº Cheque", "Banco", "Fecha de pago", "Importe"]]
                for c in cheques:
                    data.append(
                        [
                            c.get("numero", ""),
                            c.get("banco", ""),
                            c.get("fecha", ""),
                            f"{float(c.get('importe', 0) or 0):,.2f}",
                        ]
                    )
                tbl = Table(data, repeatRows=1, colWidths=[120, 160, 120, 100])
                tbl.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                            ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                        ]
                    )
                )
                elems.append(Spacer(1, 8))
                elems.append(tbl)
            doc.build(elems)
            return
        except Exception as e2:
            raise RuntimeError(
                f"No se pudo usar reports.pdf_recibo ni fallback: {e2}"
            ) from e


def _emitir_op_pdf(path, numero, fecha, proveedor, concepto, medio, importe, cheques):
    """Usa reports.pdf_orden_pago si existe; si no, genera un PDF bÃ¡sico."""
    try:
        import importlib

        reports = importlib.import_module("reports")
        if hasattr(reports, "pdf_orden_pago"):
            return reports.pdf_orden_pago(
                path,
                numero,
                fecha,
                proveedor,
                concepto,
                medio,
                importe,
                cheques=cheques,
            )
        else:
            raise AttributeError("reports sin pdf_orden_pago")
    except Exception:
        # Fallback sencillo
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors

        doc = SimpleDocTemplate(
            path,
            pagesize=A4,
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40,
        )
        s = getSampleStyleSheet()
        el = []
        el.append(Paragraph(f"ORDEN DE PAGO NÂº {numero}", s["Title"]))
        el.append(Paragraph(f"Fecha: {fecha}", s["Normal"]))
        el.append(Spacer(1, 6))
        el.append(
            Paragraph(
                f"Pagamos a: {proveedor.get('rs','')} â€” CUIT/DNI: {proveedor.get('cuit','')}",
                s["Normal"],
            )
        )
        el.append(Paragraph(f"Domicilio: {proveedor.get('dir','')}", s["Normal"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph(f"Concepto: {concepto}", s["Normal"]))
        el.append(Paragraph(f"Medio de pago: {medio}", s["Normal"]))
        el.append(Paragraph(f"Importe: ${float(importe):,.2f}", s["Heading3"]))
        if cheques:
            data = [["NÂº Cheque", "Banco", "Fecha de pago", "Importe"]]
            for c in cheques:
                data.append(
                    [
                        c.get("numero", ""),
                        c.get("banco", ""),
                        c.get("fecha", ""),
                        f"{float(c.get('importe',0) or 0):,.2f}",
                    ]
                )
            tbl = Table(data, repeatRows=1, colWidths=[120, 160, 120, 100])
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                    ]
                )
            )
            el.append(Spacer(1, 8))
            el.append(tbl)
        doc.build(el)


def _select_rows_covering_saldo(rows, saldo):
    """
    Elige el mÃ­nimo de movimientos (desde los mÃ¡s recientes hacia atrÃ¡s)
    tales que la suma de importes (mÃ¡x(debe,haber) por fila) sea >= |saldo|.
    rows (de cc_*_listar):
      id=r[1], fecha=r[2], doc=r[4], numero=r[5], concepto=r[6], medio=r[7], debe=r[8], haber=r[9]
    """
    try:
        target = abs(float(saldo or 0))
    except Exception:
        target = 0.0
    if target == 0:
        return []

    # ordenar por fecha DESC y luego id DESC
    def _key(r):
        return ((r[2] or ""), (r[1] or 0))

    ordered = sorted(rows, key=_key, reverse=True)

    acc = 0.0
    selected = []
    for r in ordered:
        try:
            debe = float(r[8] or 0.0)
            haber = float(r[9] or 0.0)
        except Exception:
            debe = haber = 0.0
        amount = max(debe, haber)
        if amount <= 0:
            amount = abs(haber - debe)
        selected.append(r)
        acc += amount
        if acc >= target:
            break

    # devolver en orden cronolÃ³gico (viejoâ†’nuevo)
    return list(reversed(selected)) if selected else list(reversed(ordered))


def _emitir_resumen_cc_pdf(path, titulo, encabezado, cuenta_label, filas, saldo):
    """
    Genera un PDF de resumen de cuenta.
    - filas: lista de tuplas (fecha, docnum, concepto, debe, haber)
    Intenta usar reports.pdf_resumen_cc si existe; si no, usa reportlab.
    """
    try:
        import importlib

        reports = importlib.import_module("reports")
        if hasattr(reports, "pdf_resumen_cc"):
            return reports.pdf_resumen_cc(
                path, titulo, encabezado, cuenta_label, filas, saldo
            )
    except Exception:
        pass

    # Fallback con reportlab
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=32
    )
    s = getSampleStyleSheet()
    el = []

    el.append(Paragraph(titulo, s["Title"]))
    el.append(Paragraph(cuenta_label, s["Heading2"]))
    el.append(Spacer(1, 6))

    for k, v in encabezado.items():
        if v:
            el.append(Paragraph(f"<b>{k}:</b> {v}", s["Normal"]))

    el.append(Spacer(1, 8))

    data = [["Fecha", "Documento", "Concepto", "Debe", "Haber"]]
    for f in filas:
        fecha, docnum, concepto, debe, haber = f
        data.append(
            [
                fecha or "",
                docnum or "",
                concepto or "",
                f"{float(debe or 0):,.2f}",
                f"{float(haber or 0):,.2f}",
            ]
        )

    tbl = Table(data, repeatRows=1, colWidths=[80, 120, 220, 70, 70])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    el.append(tbl)
    el.append(Spacer(1, 8))
    el.append(
        Paragraph(
            f"<b>Saldo {cuenta_label}:</b> {float(saldo or 0):,.2f}", s["Heading3"]
        )
    )

    doc.build(el)


# ---------- MAPAS RÃPIDOS / UPDATES SQL / SELECTOR DE CHEQUES ----------


def _map_clientes_activos():
    out = {}
    try:
        for r in db.listar_clientes():
            if _es_activo(r[16]):
                out[r[0]] = f"{r[0]} - {r[2] or ''}"
    except Exception:
        pass
    return out


def _map_proveedores_activos():
    out = {}
    try:
        for r in db.listar_proveedores():
            if _es_activo(r[16]):
                out[r[0]] = f"{r[0]} - {r[2] or ''}"
    except Exception:
        pass
    return out


def _listar_cheques_por_recibo(recibo_nro: str):
    """
    Lista cheques del REC dado buscando el tag â€œREC <n>â€ en la columna de texto disponible (obs/observaciones/detalle/...).
    Devuelve [{'id':..., 'importe':..., 'fecha':..., 'mov_caja_id':...}, ...]
    """
    if not recibo_nro:
        return []
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cols = [ (r[1] or "").lower() for r in cur.execute("PRAGMA table_info(cheques)") ]
        # columna de texto donde suele quedar el "REC <n>"
        txtcol = None
        for c in ("obs", "observaciones", "detalle", "nota", "comentario"):
            if c in cols:
                txtcol = c
                break

        rows = []
        if txtcol:
            like = f"%REC {str(recibo_nro).strip()}%"
            # traigo lo que exista; no todas las columnas estÃ¡n siempre
            sel = ["id"]
            for c in ("importe","fecha","mov_caja_id"):
                if c in cols: sel.append(c)
            sql = f"SELECT {', '.join(sel)} FROM cheques WHERE {txtcol} LIKE ?"
            rows = cur.execute(sql, (like,)).fetchall()
        elif "recibo_nro" in cols:
            sel = ["id"]
            for c in ("importe","fecha","mov_caja_id"):
                if c in cols: sel.append(c)
            sql = f"SELECT {', '.join(sel)} FROM cheques WHERE recibo_nro=?"
            rows = cur.execute(sql, (str(recibo_nro),)).fetchall()

        out = []
        for r in rows:
            d = {}
            for i, k in enumerate(sel):
                d[k] = r[i]
            out.append(d)

        conn.close()
        return out
    except Exception:
        return []


def _update_cheque_detalle(
    cheque_id: int,
    numero=None,
    banco=None,
    importe=None,
    fecha_cobro=None,
    firmante_nombre=None,
    firmante_cuit=None,
):
    """Actualiza campos frecuentes del cheque (best-effort, ignora columnas inexistentes)."""
    sets, vals = [], []
    if numero is not None:
        sets.append("numero=?")
        vals.append(numero)
    if banco is not None:
        sets.append("banco=?")
        vals.append(banco)
    if importe is not None:
        sets.append("importe=?")
        vals.append(float(importe or 0))
    if fecha_cobro is not None:
        sets.append("fecha_cobro=?")
        vals.append(str(fecha_cobro))
    # Intento ambas convenciones de columnas para firmante
    if firmante_nombre is not None:
        sets.append("firmante_nombre=?")
        vals.append(firmante_nombre)
    if firmante_cuit is not None:
        sets.append("firmante_cuit=?")
        vals.append(firmante_cuit)

    if not sets:
        return

    vals.append(int(cheque_id))
    updated_ok = False

    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute(f"UPDATE cheques SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        conn.close()
        updated_ok = True
    except Exception:
        # fallback: si fallÃ³ por columnas de firmante, reintento sin ellas
        try:
            for col in ("firmante_nombre=?", "firmante_cuit=?"):
                if col in sets:
                    i = sets.index(col)
                    sets.pop(i)
                    vals.pop(i)
            # Si al remover firmante no queda nada por actualizar, salgo
            if not sets:
                return
            conn = db.get_conn()
            cur = conn.cursor()
            cur.execute(f"UPDATE cheques SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
            conn.close()
            updated_ok = True
        except Exception:
            pass

            print("WARN post-ediciÃ³n de cheque:", _e)

# ===================== Helper Ãºnico post-ediciÃ³n de cheque =====================
def recalc_recibo_from_cheque(cheque_id: int):
    """
    Recalcula un REC a partir de un cheque editado:
      - detecta el nÃºmero de REC (recibo_nro u OBS 'REC N')
      - suma cheques del mismo REC
      - ajusta movimientos_caja.monto del mov vinculado
      - ajusta el HABER del REC en la CC del cliente
    Devuelve (ok:bool, msg:str, payload:dict|None) donde payload incluye:
      nro_rec, fecha_doc, cliente(dict), concepto, medio, items(list), total(float)
    """
    import re

    try:
        conn = db.get_conn()
        cur = conn.cursor()

        def _cols(tbl):
            try:
                return [r[1] for r in cur.execute(f"PRAGMA table_info({tbl})")]
            except Exception:
                return []

        # --- 1) Leer recibo_nro u OBS del cheque editado ---
        ch_cols = _cols("cheques")
        if "id" not in ch_cols:
            conn.close()
            return False, "Tabla cheques sin col id", None

        sel = ["id"]
        for c in ("importe", "mov_caja_id", "cliente_id", "obs", "recibo_nro", "numero", "banco", "fecha", "fecha_cobro"):
            if c in ch_cols:
                sel.append(c)
        row = cur.execute(f"SELECT {', '.join(sel)} FROM cheques WHERE id=?", (int(cheque_id),)).fetchone()
        if not row:
            conn.close()
            return False, "Cheque inexistente", None

        rec = dict(zip(sel, row))
        ch_obs = str(rec.get("obs") or "")
        rec_nro = str(rec.get("recibo_nro") or "").strip()

        if not rec_nro:
            m = re.search(r"\bREC\s+(\d+)", ch_obs, flags=re.I)
            if m:
                rec_nro = m.group(1)

        if not rec_nro:
            conn.close()
            return False, "Cheque sin REC asociado (ni recibo_nro ni OBS)", None
        # --- 2) Listar todos los cheques del mismo REC y sumar total ---
        items = []
        total = 0.0
        mov_caja_id = None
        cliente_id = None

        # SELECT dinÃ¡mico segÃºn columnas de 'cheques'
        sel2 = ["id"]
        for c in ("numero", "banco", "fecha", "fecha_cobro", "importe", "mov_caja_id", "cliente_id", "obs", "recibo_nro"):
            if c in ch_cols and c not in sel2:
                sel2.append(c)

        if "recibo_nro" in ch_cols:
            q = f"SELECT {', '.join(sel2)} FROM cheques WHERE recibo_nro=?"
            params = (rec_nro,)
        else:
            # si no hay 'recibo_nro', usamos OBS LIKE
            if "obs" not in ch_cols:
                conn.close()
                return False, "Tabla cheques sin columnas recibo_nro ni obs", None
            q = f"SELECT {', '.join(sel2)} FROM cheques WHERE obs LIKE ?"
            params = (f"%REC {rec_nro}%",)

        for row2 in cur.execute(q, params).fetchall():
            d = dict(zip(sel2, row2))

            # total
            try:
                total += float(d.get("importe") or 0)
            except Exception:
                pass

            # tomar mov_caja_id y cliente_id de la primera fila que lo tenga
            if mov_caja_id is None and d.get("mov_caja_id") is not None:
                mov_caja_id = d.get("mov_caja_id")
            if cliente_id is None and d.get("cliente_id") is not None:
                cliente_id = d.get("cliente_id")

            # fecha para el PDF (usa lo que exista)
            fecha_pdf = d.get("fecha") or d.get("fecha_cobro") or today_str()

            items.append({
                "numero": d.get("numero", "") or "",
                "banco":  d.get("banco", "") or "",
                "fecha":  fecha_pdf,
                "importe": float(d.get("importe") or 0),
            })

        if total <= 0:
            conn.close()
            return False, f"REC {rec_nro}: total = 0", None

            conn.close()
            return False, f"REC {rec_nro}: total = 0", None

            conn.close()
            return False, f"REC {rec_nro}: total = 0", None

        # --- 3) Ajustar movimientos_caja ---
        # 3a) si mov_caja_id vino desde cheques, usarlo
        mid = None
        try:
            mid = int(mov_caja_id) if mov_caja_id else None
        except Exception:
            mid = None

        # 3b) si no lo tenemos, intento encontrarlo por numero/doc/obs
        if not mid:
            mc_cols = _cols("movimientos_caja")
            if mc_cols:
                # match por numero + doc=REC/recibo
                if ("numero" in mc_cols) and any(c in mc_cols for c in ("doc", "documento")):
                    doccol = "doc" if "doc" in mc_cols else "documento"
                    row_mc = cur.execute(
                        f"SELECT id FROM movimientos_caja WHERE numero=? AND UPPER({doccol}) IN ('REC','RECIBO')",
                        (rec_nro,)
                    ).fetchone()
                    if row_mc:
                        mid = row_mc[0]
                # fallback por detalle/obs LIKE
                if not mid and any(c in mc_cols for c in ("detalle", "obs")):
                    detcol = "detalle" if "detalle" in mc_cols else "obs"
                    row_mc = cur.execute(
                        f"SELECT id FROM movimientos_caja WHERE {detcol} LIKE ?",
                        (f"%REC {rec_nro}%",)
                    ).fetchone()
                    if row_mc:
                        mid = row_mc[0]

        # actualizar monto en Caja
        if mid:
            try:
                cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(total), int(mid)))
            except Exception:
                pass

        # --- 4) Ajustar CC del cliente (si lo podemos inferir) ---
        def _exists(t):  # tabla existe
            try:
                return bool(cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (t,)
                ).fetchone())
            except Exception:
                return False

        def _update_cc_tab(tabla: str) -> bool:
            """Devuelve True si pudo actualizar en 'tabla' el HABER/monto del REC."""
            cols = _cols(tabla)
            if not cols:
                return False

            # nÃºmero de recibo
            where = []
            params = []

            # campo numero
            if "numero" in cols:
                where.append("numero=?")
                params.append(str(rec_nro))
            elif "recibo" in cols:
                where.append("recibo=?")
                params.append(str(rec_nro))
            else:
                return False  # no sabemos ubicar el comprobante

            # doc/documento
            doccol = None
            for cdoc in ("doc", "documento"):
                if cdoc in cols:
                    doccol = cdoc
                    break
            if doccol:
                where.append(f"UPPER({doccol}) IN ('REC','RECIBO')")

            # cliente
            idcol = None
            for c in ("cliente_id", "entidad_id", "ent_id", "id_cliente"):
                if c in cols:
                    idcol = c
                    break
            if idcol and cliente_id:
                where.append(f"{idcol}=?")
                params.append(int(cliente_id))

            w = " AND ".join(where)
            # set de importes
            set_sql = None
            if "haber" in cols and "debe" in cols:
                set_sql = "haber=?, debe=0"
                set_params = [float(total)]
            elif "haber" in cols:
                set_sql = "haber=?"
                set_params = [float(total)]
            elif "monto" in cols:
                set_sql = "monto=?"
                set_params = [float(total)]
            elif "credito" in cols:
                set_sql = "credito=?"
                set_params = [float(total)]
            else:
                return False

            try:
                cur.execute(f"UPDATE {tabla} SET {set_sql} WHERE {w}", (*set_params, *params))
                return cur.rowcount > 0
            except Exception:
                return False

        updated_cc = False
        # Probamos en orden (alias habituales)
        for t in (
            "cc_clientes_cuenta1", "cc_clientes_cuenta2", "cc_clientes",
            "cc_cli_cuenta1", "cc_cli_cuenta2", "cc_cli"
        ):
            if _exists(t) and _update_cc_tab(t):
                updated_cc = True
                break

        # Si encontramos un movimiento de caja, leo su fecha para el PDF
        fecha_doc = today_str()
        if mid:
            try:
                row_mc = cur.execute("SELECT fecha FROM movimientos_caja WHERE id=?", (int(mid),)).fetchone()
                if row_mc and row_mc[0]:
                    fecha_doc = row_mc[0]
            except Exception:
                pass

        conn.commit()
        conn.close()

        # --- 5) Armar payload para que la UI emita el PDF una sola vez ---
        # Concepto/medio: defaults simples
        concepto, medio = "Recibo", "cheque"
        cli = {}
        try:
            if cliente_id:
                cli = _cliente_dict_from_id(int(cliente_id)) or {}
        except Exception:
            cli = {}

        payload = {
            "nro_rec": rec_nro,
            "fecha_doc": fecha_doc,
            "cliente": cli,
            "concepto": concepto,
            "medio": medio,
            "items": items,
            "total": float(total),
        }
        return True, ("OK" if updated_cc else "OK (CC no ubicada, pero Caja actualizada)"), payload

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return False, f"Error recalc: {e}", None
# ================== /Helper Ãºnico post-ediciÃ³n de cheque =======================



def _recalcular_caja_y_cc_por_recibo(recibo_nro: str) -> tuple[bool, str]:
    """
    Recalcula total por cheques del REC, actualiza Caja.monto y
    actualiza la CC asociada. No asume nombres fijos como 'cc_cli'.
    Devuelve (ok, msg).
    """
    try:
        chs = _listar_cheques_por_recibo(recibo_nro)
        if not chs:
            return False, f"REC {recibo_nro}: no se encontraron cheques"

        total = 0.0
        mov_caja_id = None
        for c in chs:
            try:
                total += float(c[4] or 0)
            except Exception:
                pass
            if mov_caja_id is None and c[6]:
                mov_caja_id = int(c[6])

        if mov_caja_id is None:
            return False, f"REC {recibo_nro}: no se pudo inferir mov_caja_id"

        conn = db.get_conn()
        cur = conn.cursor()

        # 1) Actualizar Caja
        cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(total), int(mov_caja_id)))
        conn.commit()
        conn.close()

        # 2) Actualizar CC vinculada
        ok_cc, msg_cc = _actualizar_cc_vinculada(mov_caja_id, float(total), recibo_nro)
        if not ok_cc:
            return False, msg_cc

        return True, "ok"
    except Exception as ex:
        return False, f"ExcepciÃ³n recalculando REC: {ex}"


def _actualizar_cc_vinculada(mov_caja_id: int, nuevo_total: float, recibo_nro: str | None = None) -> tuple[bool, str]:
    """
    Best-effort para actualizar la CC vinculada a un movimiento de caja:
      A) Intenta leer en movimientos_caja columnas tipo ('origen_tabla'/'tabla_origen', 'origen_id').
      B) Si no hay A), busca en tablas de CC que tengan columna 'mov_caja_id'.
      C) (opcional) Si viene recibo_nro, intenta por 'recibo'/'numero'/'obs' que contenga el nro.
    Devuelve (ok, msg).
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # ---------- A) mirar referencia guardada en movimientos_caja ----------
        # mapear nombres a Ã­ndice por PRAGMA
        cols_mc = [r[1] for r in cur.execute("PRAGMA table_info(movimientos_caja)")]
        row_mc = cur.execute("SELECT * FROM movimientos_caja WHERE id=?", (int(mov_caja_id),)).fetchone()

        def _idx(nombre: str) -> int | None:
            try:
                return cols_mc.index(nombre)
            except ValueError:
                return None

        cand_tab = ["origen_tabla", "tabla_origen", "cc_tabla", "origen_tipo"]
        cand_id  = ["origen_id", "cc_id", "id_origen"]
        tabla_origen = None
        id_origen = None
        if row_mc:
            for n in cand_tab:
                i = _idx(n)
                if i is not None:
                    tabla_origen = row_mc[i]
                    if tabla_origen:
                        break
            for n in cand_id:
                i = _idx(n)
                if i is not None:
                    id_origen = row_mc[i]
                    if id_origen:
                        break

        if tabla_origen and id_origen:
            t = "".join(ch for ch in str(tabla_origen) if ch.isalnum() or ch == "_")
            try:
                cur.execute(f"UPDATE {t} SET monto=? WHERE id=?", (float(nuevo_total), int(id_origen)))
                conn.commit()
                conn.close()
                return True, "ok"
            except Exception as ex:
                # sigue con B) si falla
                pass

        # ---------- B) buscar tablas de CC con columna mov_caja_id ----------
        tablas_cc = [
            # clientes
            "cc_clientes_cuenta1", "cc_clientes_cuenta2", "cc_clientes",
            "cc_cli_cuenta1", "cc_cli_cuenta2", "cc_cli",
            # proveedores (por si el flujo fuera proveedor)
            "cc_proveedores_cuenta1", "cc_proveedores_cuenta2", "cc_proveedores",
            "cc_prov_cuenta1", "cc_prov_cuenta2", "cc_prov",
        ]

        tocadas = 0
        for t in tablas_cc:
            ex = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not ex:
                continue
            cols_t = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
            if "mov_caja_id" in cols_t:
                try:
                    cur.execute(f"UPDATE {t} SET monto=? WHERE mov_caja_id=?", (float(nuevo_total), int(mov_caja_id)))
                    tocadas += cur.rowcount or 0
                except Exception:
                    pass

        if tocadas:
            conn.commit()
            conn.close()
            return True, "ok"

        # ---------- C) Ãºltimo recurso: buscar por recibo_nro si vino ----------
        if recibo_nro:
            for t in tablas_cc:
                ex = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
                if not ex:
                    continue
                cols_t = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
                # intentos por campos habituales
                if "recibo" in cols_t:
                    try:
                        cur.execute(f"UPDATE {t} SET monto=? WHERE recibo=?", (float(nuevo_total), str(recibo_nro)))
                        if cur.rowcount:
                            conn.commit()
                            conn.close()
                            return True, "ok"
                    except Exception:
                        pass
                for campo_txt in ("numero", "documento", "obs", "detalle", "concepto"):
                    if campo_txt in cols_t:
                        try:
                            cur.execute(
                                f"UPDATE {t} SET monto=? WHERE {campo_txt} LIKE ?",
                                (float(nuevo_total), f"%{recibo_nro}%")
                            )
                            if cur.rowcount:
                                conn.commit()
                                conn.close()
                                return True, "ok"
                        except Exception:
                            pass

        conn.close()
        return False, "No pude ubicar el movimiento en ninguna tabla de CC."
    except Exception as ex:
        return False, f"ExcepciÃ³n al actualizar CC: {ex}"


def _insert_mov_caja_manual(
    fecha: str,
    tipo: str,
    concepto: str,
    monto,
    cuenta: str,
    detalle: str | None = None,
    medio: str | None = None,
    recibo: str | None = None,
    origen_tipo: str | None = "manual",
    origen_id: int | None = None,
) -> int:
    """
    Inserta un movimiento manual en movimientos_caja.
    Hace commit y devuelve el id insertado. Se adapta a las columnas existentes.
    Requisitos mÃ­nimos para que la grilla lo muestre: fecha, tipo, concepto, monto, cuenta.
    """
    try:
        m = float(monto or 0)
    except Exception:
        m = 0.0

    # Normalizo tipo
    t = (tipo or "").strip().lower()
    if t not in ("ingreso", "egreso"):
        t = "ingreso" if m >= 0 else "egreso"
    tipo_norm = t.capitalize()  # "Ingreso"/"Egreso"

    # Defaults mÃ­nimos
    fecha = (fecha or "").strip() or today_str()
    concepto = (concepto or "").strip() or "Manual"
    cuenta = (cuenta or "").strip()

    conn = db.get_conn()
    cur = conn.cursor()

    # Columnas disponibles
    cols = [r[1] for r in cur.execute("PRAGMA table_info(movimientos_caja)")]

    data = {}
    if "fecha" in cols:         data["fecha"] = fecha
    if "tipo" in cols:          data["tipo"] = tipo_norm
    if "concepto" in cols:      data["concepto"] = concepto
    if "detalle" in cols:       data["detalle"] = detalle or ""
    if "monto" in cols:         data["monto"] = float(m)
    if "cuenta" in cols:        data["cuenta"] = cuenta
    if "medio" in cols:         data["medio"] = medio or None
    if "recibo" in cols and recibo is not None:
                                data["recibo"] = recibo
    if "origen_tipo" in cols:   data["origen_tipo"] = origen_tipo or "manual"
    if "origen_id" in cols and origen_id is not None:
                                data["origen_id"] = origen_id

    if not {"fecha","tipo","concepto","monto","cuenta"}.issubset(set(data.keys())):
        conn.close()
        raise RuntimeError("Faltan columnas clave en movimientos_caja")

    fields = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    # --- NormalizaciÃ³n y defaults seguros para que Caja lo liste ---
    from datetime import date
    hoy = date.today().strftime("%Y-%m-%d")
    
    # Clon defensivo (por si 'data' es un Mapping)
    data = dict(data)
    
    # fecha
    if not str(data.get("fecha") or "").strip():
        data["fecha"] = hoy
    
    # tipo (Caja suele mostrar sÃ³lo "Ingreso"/"Egreso" con capital inicial)
    t = str(data.get("tipo") or "").strip().lower()
    if t in ("ingreso", "i"):
        data["tipo"] = "Ingreso"
    elif t in ("egreso", "e"):
        data["tipo"] = "Egreso"
    else:
        # default razonable
        data["tipo"] = "Ingreso"
    
    # concepto
    if not str(data.get("concepto") or "").strip():
        data["concepto"] = "Manual"
    
    # monto
    try:
        data["monto"] = float(data.get("monto") or 0)
    except Exception:
        data["monto"] = 0.0
    
    # cuenta
    if not str(data.get("cuenta") or "").strip():
        # ponÃ© acÃ¡ tu cuenta por defecto si usÃ¡s otra
        data["cuenta"] = "Caja"
    
    # --- construir columnas y valores ALINEADOS ---
    fields = list(data.keys())
    placeholders = ",".join("?" for _ in fields)
    vals = tuple(data[k] for k in fields)
    
    cur.execute(
        f"INSERT INTO movimientos_caja ({', '.join(fields)}) VALUES ({placeholders})",
        vals
    )
    
    # ID insertado (para diagnÃ³stico)
    mid = None
    try:
        mid = cur.lastrowid
        if not mid:
            mid = cur.execute("SELECT MAX(id) FROM movimientos_caja").fetchone()[0]
    except Exception:
        pass
    print("DEBUG Caja: insert id=", mid, " data=", data)
    
    conn.commit()
    
    # Refrescar UI (best-effort)
    try:
        app_ = getattr(self, "app", None) or getattr(self.master, "app", None)
        if app_ and hasattr(app_, "tab_caj"):
            app_.tab_caj.reload()
    except Exception as _e:
        print("WARN reload Caja:", _e)
    
    try:
        from tkinter import messagebox
        messagebox.showinfo("Caja", "Movimiento guardado.")
    except Exception:
        pass


    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(new_id)


def _get_mov_caja(mov_id: int):
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        row = cur.execute(
            """SELECT id, fecha, tipo, medio, concepto, detalle, monto,
                      tercero_tipo, tercero_id, estado, origen_tipo, origen_id, cuenta
               FROM movimientos_caja WHERE id=?""",
            (mov_id,),
        ).fetchone()
        conn.close()
        return row
    except Exception:
        return None


def _update_mov_caja(
    mov_id: int,
    fecha,
    tipo,
    medio,
    concepto,
    detalle,
    monto,
    tercero_tipo,
    tercero_id,
    cuenta,
):
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE movimientos_caja
           SET fecha=?, tipo=?, medio=?, concepto=?, detalle=?, monto=?,
               tercero_tipo=?, tercero_id=?, cuenta=? WHERE id=?""",
        (
            fecha,
            tipo,
            medio,
            concepto,
            detalle,
            float(monto or 0),
            (tercero_tipo or ""),
            (int(tercero_id) if tercero_id else None),
            (1 if str(cuenta).endswith("1") else 2),
            mov_id,
        ),
    )
    conn.commit()
    conn.close()


def _update_cheque_basico(cheque_id: int, importe=None, fecha_cobro=None):
    sets, vals = [], []
    if importe is not None:
        sets.append("importe=?")
        vals.append(float(importe or 0))
    if fecha_cobro:
        sets.append("fecha_cobro=?")
        vals.append(str(fecha_cobro))
    if not sets:
        return
    vals.append(cheque_id)
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE cheques SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()

    def _get_cols(cur, table):
        try:
            return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        except Exception:
            return []

    conn = db.get_conn()
    cur = conn.cursor()

    # 1) Leer datos bÃ¡sicos del cheque
    ch = cur.execute("""
        SELECT id, numero, banco, fecha_cobro, importe, cliente_id, mov_caja_id, obs
        FROM cheques WHERE id=?""", (int(cheque_id),)).fetchone()
    if not ch:
        conn.close(); return

    _, ch_num, ch_bco, ch_fcob, ch_imp, ch_cid, ch_mid, ch_obs = ch

    # 2) Encontrar nÃºmero de REC (OBS o recibo_nro)
    rec_nro = ""
    try:
        rec_nro = _extraer_numero_recibo_de_obs(ch_obs or "")
    except Exception:
        rec_nro = ""
    if not rec_nro:
        try:
            row = cur.execute("SELECT recibo_nro FROM cheques WHERE id=?", (int(cheque_id),)).fetchone()
            rec_nro = (row[0] or "") if row else ""
        except Exception:
            pass
    if not rec_nro:
        # No hay REC asociado â†’ no hay nada que ajustar
        conn.close(); return

    # 3) Traer TODOS los cheques de ese REC y calcular total
    cheqs = []
    try:
        # Preferimos coincidencia por recibo_nro; si no existe, usamos OBS
        q = """SELECT id, numero, banco, fecha_cobro, importe, cliente_id, mov_caja_id
               FROM cheques
               WHERE recibo_nro=?"""
        cheqs = cur.execute(q, (rec_nro,)).fetchall()
    except Exception:
        pass
    if not cheqs:
        # Fallback: buscar por OBS "REC <n>"
        pat = f"%REC {rec_nro}%"
        cheqs = cur.execute("""SELECT id, numero, banco, fecha_cobro, importe, cliente_id, mov_caja_id
                               FROM cheques
                               WHERE obs LIKE ?""", (pat,)).fetchall()

    if not cheqs:
        conn.close(); return

    total = 0.0
    caja_id = None
    cliente_id = None
    items_pdf = []
    for (cid, numero, banco, fcob, importe, cli_id, mid) in cheqs:
        try:
            total += float(importe or 0.0)
        except Exception:
            pass
        if not caja_id and mid:
            caja_id = mid
        if not cliente_id and cli_id:
            cliente_id = cli_id
        items_pdf.append({
            "numero": numero or "",
            "banco": banco or "",
            "fecha": fcob or "",
            "importe": float(importe or 0.0),
        })

    # 4) Actualizar Caja (monto) si tengo movimiento
    if caja_id:
        try:
            cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(total), int(caja_id)))
        except Exception:
            pass

    # 5) Actualizar CC Clientes (haber del REC)
    # Buscamos tablas candidatas y columnas reales (nombres pueden variar)
    candidate_tables = [
        "cc_clientes_cuenta1","cc_clientes_cuenta2","cc_clientes",
        "cc_cli_cuenta1","cc_cli_cuenta2","cc_cli"
    ]
    updated_cc = 0
    for t in candidate_tables:
        try:
            cols = _get_cols(cur, t)
            if not cols: 
                continue
            # columnas mÃ­nimas requeridas
            if all(c in cols for c in ("numero","doc")) and ("haber" in cols or "monto_haber" in cols):
                haber_col = "haber" if "haber" in cols else "monto_haber"
                # setear haber=total y debe=0.0 para REC
                if "debe" in cols:
                    cur.execute(f"UPDATE {t} SET {haber_col}=?, debe=0 WHERE numero=? AND (doc='REC' OR lower(doc)='recibo')",
                                (float(total), str(rec_nro)))
                else:
                    cur.execute(f"UPDATE {t} SET {haber_col}=? WHERE numero=? AND (doc='REC' OR lower(doc)='recibo')",
                                (float(total), str(rec_nro)))
                updated_cc += cur.rowcount
        except Exception:
            continue

    conn.commit()
    conn.close()

    # 6) Re-emitir PDF del REC (best-effort)
    try:
        if not cliente_id:
            # Fallback: si no vino del cheque, uso el cliente del cheque editado
            cliente_id = ch_cid
        if not cliente_id:
            return
        cliente = _cliente_dict_from_id(int(cliente_id))
        from datetime import date
        hoy = date.today().strftime("%Y-%m-%d")
        # out = filedialog.asksaveasfilename(
        #    title="Guardar Recibo ACTUALIZADO",
        #    defaultextension=".pdf",
        #    initialfile=f"REC_{rec_nro}_ACT_{hoy}.pdf",
        #)
        #if out:
        #    _emitir_recibo_pdf(
        #        out,
        #        rec_nro,
        #        hoy,                 # mantenemos hoy como fecha de emisiÃ³n actualizada
        #        cliente,
        #        f"Ajuste por ediciÃ³n de cheques REC {rec_nro}",
        #        "cheque",
        #        float(total),
        #        items_pdf,
        #   )
            # No interrumpo si falla; ya se ajustÃ³ caja/cc
    except Exception:
        pass


# --- DIVISAS helpers y reciboâ†”cheque helpers ----------------------------


def _ensure_divisas_table():
    """
    Crea la tabla divisas si no existe y agrega columnas faltantes de forma segura.
    Soporta esquemas con 'tipo' o 'operacion', y 'ars' o 'total_ars'.
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # Crear si no existe (usando columnas mÃ¡s "completas")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS divisas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT,
                tipo TEXT,
                usd REAL,
                tc REAL,
                ars REAL,
                tercero_tipo TEXT,
                tercero_id INTEGER,
                obs TEXT,
                mov_caja_id INTEGER,
                cuenta TEXT
            )
        """)

        # Columnas existentes
        cols = [r[1] for r in cur.execute("PRAGMA table_info(divisas)")]

        def add_col_if_missing(colname: str, coltype: str):
            if colname not in cols:
                try:
                    cur.execute(f"ALTER TABLE divisas ADD COLUMN {colname} {coltype}")
                    cols.append(colname)
                except Exception:
                    pass

        # Asegurar columnas mÃ­nimas
        # (si no hay ni 'tipo' ni 'operacion', agrego 'tipo')
        if ("tipo" not in cols) and ("operacion" not in cols):
            add_col_if_missing("tipo", "TEXT")

        # (si no hay ni 'ars' ni 'total_ars', agrego 'ars')
        if ("ars" not in cols) and ("total_ars" not in cols):
            add_col_if_missing("ars", "REAL")

        # detalle/obs
        if ("obs" not in cols) and ("detalle" not in cols):
            add_col_if_missing("obs", "TEXT")

        # resto Ãºtiles
        add_col_if_missing("usd", "REAL")
        add_col_if_missing("tc", "REAL")
        add_col_if_missing("fecha", "TEXT")
        add_col_if_missing("tercero_tipo", "TEXT")
        add_col_if_missing("tercero_id", "INTEGER")
        add_col_if_missing("mov_caja_id", "INTEGER")
        add_col_if_missing("cuenta", "TEXT")

        conn.commit()
        conn.close()
    except Exception:
        # no detengas el flujo si algo falla aquÃ­
        pass


def _insert_divisas_mov(
    fecha: str,
    operacion: str,   # "compra" | "venta"
    usd: float,
    tc: float,
    ars: float,
    tercero_tipo: str | None,
    tercero_id: int | None,
    detalle: str,
    mov_caja_id: int | None,
) -> int | None:
    """
    Inserta/actualiza una fila en divisas adaptÃ¡ndose al esquema ('tipo' o 'operacion', 'ars' o 'total_ars').
    Si existe mov_caja_id, hace UPSERT (update si ya hay una fila con ese mov_caja_id).
    Devuelve el id de la fila en divisas (o None si no pudo).
    """
    try:
        _ensure_divisas_table()
        conn = db.get_conn()
        cur = conn.cursor()

        cols = [r[1] for r in cur.execute("PRAGMA table_info(divisas)")]

        # mapear nombres de columnas segÃºn existan
        col_tipo = "tipo" if "tipo" in cols else ("operacion" if "operacion" in cols else "tipo")
        col_ars  = "ars"  if "ars"  in cols else ("total_ars" if "total_ars" in cols else "ars")
        col_det  = "obs"  if "obs"  in cols else ("detalle" if "detalle" in cols else "obs")

        has_movid   = "mov_caja_id" in cols
        has_cuenta  = "cuenta" in cols

        # Â¿Ya hay registro para este mov_caja_id? â†’ entonces UPDATE
        if has_movid and mov_caja_id:
            row = cur.execute(
                "SELECT id FROM divisas WHERE mov_caja_id=? LIMIT 1",
                (int(mov_caja_id),)
            ).fetchone()
            if row:
                set_parts = [
                    "fecha=?",
                    f"{col_tipo}=?",
                    "usd=?",
                    "tc=?",
                    f"{col_ars}=?",
                    "tercero_tipo=?",
                    "tercero_id=?",
                    f"{col_det}=?",
                ]
                vals = [
                    str(fecha),
                    str(operacion),
                    float(usd or 0),
                    float(tc or 0),
                    float(ars or 0),
                    (tercero_tipo or None),
                    (int(tercero_id) if tercero_id else None),
                    detalle or "",
                ]
                if has_cuenta:
                    set_parts.append("cuenta=?")
                    vals.append((None if (not has_cuenta) else (detalle and None)) or None)  # placeholder, ajusto abajo

                # ajustar cuenta correctamente (si hay columna)
                if has_cuenta:
                    vals[-1] = None  # por defecto None; la cuenta real la asociamos desde Caja si es posible

                # HacÃ© el UPDATE
                vals.append(int(row[0]))
                cur.execute(f"UPDATE divisas SET {', '.join(set_parts)} WHERE id=?", vals)
                conn.commit()
                did = int(row[0])
                conn.close()
                return did

        # Si no hay fila previa o no hay mov_caja_id â†’ INSERT
        fields = ["fecha", col_tipo, "usd", "tc", col_ars, "tercero_tipo", "tercero_id", col_det]
        values = [str(fecha), str(operacion), float(usd or 0), float(tc or 0), float(ars or 0),
                  (tercero_tipo or None), (int(tercero_id) if tercero_id else None), detalle or ""]
        if has_movid:
            fields.append("mov_caja_id")
            values.append(int(mov_caja_id) if mov_caja_id else None)
        if has_cuenta:
            fields.append("cuenta")
            values.append(None)  # por ahora None; la cuenta se puede inferir luego desde movimientos_caja

        placeholders = ",".join("?" for _ in fields)
        cur.execute(
            f"INSERT INTO divisas ({', '.join(fields)}) VALUES ({placeholders})",
            tuple(values)
        )
        did = cur.lastrowid
        conn.commit()
        conn.close()
        return int(did)
    except Exception as ex:
        print("WARN _insert_divisas_mov:", ex)
        return None


def _try_set_recibo_nro(cheque_id: int, recibo_nro: str):
    """Guarda el nÂº de recibo en cheques (usa funciÃ³n de db si existe, si no SQL directo)."""
    try:
        if hasattr(db, "set_recibo_en_cheque"):
            db.set_recibo_en_cheque(int(cheque_id), str(recibo_nro))
            return
    except Exception:
        pass
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE cheques SET recibo_nro=? WHERE id=?",
            (str(recibo_nro), int(cheque_id)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def _cc_upsert_recibo_cliente(cliente_id: int, nro_rec: str, fecha_doc: str,
                              concepto: str, medio: str, total: float, mov_caja_id: int):
    """
    Reemplaza el movimiento de CC (cliente) del REC indicado por la suma actualizada de cheques.
    Busca tablas CC conocidas y usa la primera que exista.
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # Tablas candidatas (en orden de preferencia)
        candidatas = [
            "cc_clientes_cuenta1", "cc_cli_cuenta1",
            "cc_clientes", "cc_cli",
            "cc_clientes_cuenta2", "cc_cli_cuenta2",
        ]

        # helper table_exists
        def _texists(t):
            return bool(cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone())

        tabla = next((t for t in candidatas if _texists(t)), None)
        if not tabla:
            conn.close()
            return  # no hay CC local, salgo sin romper

        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({tabla})")]

        # detectar columnas
        col_cli = next((c for c in (
            "cliente_id","id_cliente","entidad_id","ent_id","cliente"
        ) if c in cols), None)
        col_monto = "monto" if "monto" in cols else ("importe" if "importe" in cols else None)

        if not col_cli or not col_monto or "fecha" not in cols:
            conn.close()
            return  # no puedo upsert si faltan columnas bÃ¡sicas

        # Borrar entrada anterior de este REC
        # segÃºn columnas disponibles: recibo/obs/origen_id+origen_tipo
        if "recibo" in cols:
            cur.execute(f"DELETE FROM {tabla} WHERE recibo=?", (str(nro_rec),))
        elif "obs" in cols:
            cur.execute(f"DELETE FROM {tabla} WHERE obs LIKE ?", (f"%REC {nro_rec}%",))
        elif "origen_id" in cols and "origen_tipo" in cols:
            cur.execute(f"DELETE FROM {tabla} WHERE origen_tipo='REC' AND origen_id=?", (int(mov_caja_id),))

        # Insertar nueva entrada
        campos = [col_cli, "fecha", "concepto", "medio", col_monto]
        vals   = [int(cliente_id), fecha_doc, concepto, f"{medio} (REC {nro_rec})", float(total)]

        if "recibo" in cols:
            campos += ["recibo"]; vals += [str(nro_rec)]
        if "obs" in cols:
            campos += ["obs"];    vals += [f"REC {nro_rec}"]
        if "origen_tipo" in cols and "origen_id" in cols:
            campos += ["origen_tipo","origen_id"]; vals += ["REC", int(mov_caja_id)]

        cur.execute(
            f"INSERT INTO {tabla} ({', '.join(campos)}) VALUES ({', '.join('?'*len(vals))})",
            vals
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("WARN _cc_upsert_recibo_cliente:", e)



def _cleanup_cheques_y_caja_por_recibo(recibo_nro: str):
    """Al borrar un Recibo en CC Clientes: borra cheques con ese recibo_nro y sus movimientos de caja vinculados."""
    if not recibo_nro:
        return
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, mov_caja_id FROM cheques WHERE recibo_nro=?", (str(recibo_nro),)
        ).fetchall()
        mov_ids = [r[1] for r in rows if r[1]]
        chq_ids = [r[0] for r in rows]
        if chq_ids:
            cur.execute("DELETE FROM cheques WHERE recibo_nro=?", (str(recibo_nro),))
        for mid in mov_ids:
            cur.execute("DELETE FROM movimientos_caja WHERE id=?", (mid,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("WARN cleanup cheques/caja:", e)


# ---------- DiÃ¡logos y helpers de selecciÃ³n de cheques -----------------


class ChequesSelectorDialog(tk.Toplevel):
    """
    Selecciona cheques EN CARTERA existentes para endoso en OP (proveedores).
    Devuelve en self.result:
      {
        "ids": [int...],
        "total": float,
        "items": [{"id":..., "numero":"", "banco":"", "fecha":"YYYY-MM-DD", "importe": float}, ...]
      }
    """

    def __init__(self, master, cuenta_flag=None):
        super().__init__(master)
        self.title("Seleccionar cheques en cartera")
        self.resizable(False, False)
        self.result = None

        # cuenta 1/2 si hay flag
        self._cuenta = None
        if cuenta_flag:
            self._cuenta = 1 if str(cuenta_flag).lower().endswith("1") else 2

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        cols = ("id", "numero", "banco", "fecha_cobro", "importe", "cliente")
        self.tree = ttk.Treeview(
            frm, show="headings", height=12, columns=cols, selectmode="extended"
        )
        for c in cols:
            self.tree.heading(c, text=c)
            if c == "cliente":
                self.tree.column(c, width=260, anchor="center")
            elif c == "importe":
                self.tree.column(c, width=100, anchor="center")
            else:
                self.tree.column(c, width=120, anchor="center")
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, pady=(0, 6))

        # cargar datos
        try:
            rows = []
            if hasattr(db, "listar_cheques_en_cartera"):
                try:
                    rows = db.listar_cheques_en_cartera()
                except Exception:
                    rows = []
            if not rows:
                # Fallback a listar_cheques + filtro robusto del estado
                try:
                    all_ch = db.listar_cheques()
                    rows = [
                        r for r in all_ch if _is_en_cartera(r[9] if len(r) > 9 else "")
                    ]
                except Exception:
                    rows = []
        except Exception as e:
            messagebox.showwarning(
                "Cheques", "No se pudo listar cheques en cartera:\n" + str(e)
            )
            rows = []

        # mapa de clientes id -> nombre
        cli_map = {}
        try:
            cli_map = {c[0]: c[2] for c in db.listar_clientes()}
        except Exception:
            pass

        def _safe_get(seq, idx, default=""):
            try:
                return seq[idx]
            except Exception:
                return default

        def _parse_int(v):
            try:
                return int(str(v).strip())
            except Exception:
                return None

        for ch in rows:
            # intentar detectar cuenta (si no se detecta, no filtramos por cuenta)
            cuenta = None
            for idx_try in (16, 15, 14, 13, 12, -1):
                if -len(ch) <= idx_try < len(ch):
                    cuenta = _parse_int(_safe_get(ch, idx_try, None))
                    if cuenta is not None:
                        break
            if self._cuenta and cuenta and cuenta != self._cuenta:
                continue

            cid = _safe_get(ch, 6, "")  # cliente_id
            cliente_txt = f"{cid} - {cli_map.get(cid, '')}".strip(" -")

            self.tree.insert(
                "",
                "end",
                values=(
                    _safe_get(ch, 0, ""),  # id
                    _safe_get(ch, 1, "") or "",  # numero
                    _safe_get(ch, 2, "") or "",  # banco
                    _safe_get(ch, 5, "") or "",  # fecha_cobro
                    f"{float(_safe_get(ch, 3, 0) or 0):.2f}",  # importe
                    cliente_txt,
                ),
            )

        # total seleccionado
        self.lbl_total = ttk.Label(frm, text="Total seleccionado: 0.00")
        self.lbl_total.pack(anchor="e")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._recalc_total())

        # botones
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Aceptar", command=self._ok).pack(side="right")

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _recalc_total(self):
        tot = 0.0
        for it in self.tree.selection():
            vals = self.tree.item(it, "values")
            try:
                tot += float(str(vals[4]).replace(",", "."))
            except Exception:
                pass
        self.lbl_total.config(text=f"Total seleccionado: {tot:.2f}")

    def _ok(self):
        ids = []
        items = []
        tot = 0.0
        for it in self.tree.selection():
            (cid, numero, banco, fecha, importe, _cli) = self.tree.item(it, "values")
            try:
                imp = float(str(importe).replace(",", "."))
            except Exception:
                imp = 0.0
            ids.append(int(cid))
            items.append(
                {
                    "id": int(cid),
                    "numero": numero or "",
                    "banco": banco or "",
                    "fecha": fecha or "",
                    "importe": imp,
                }
            )
            tot += imp
        if not ids:
            messagebox.showinfo("Cheques", "No seleccionaste cheques.")
            return
        self.result = {"ids": ids, "total": tot, "items": items}
        self.destroy()


# ------------------------------ diÃ¡logos -------------------------------
def _tipos_proveedor_desde_bd():
    """
    Junta tipos desde columnas posibles en proveedores: tipo_proveedor, tipo,
    tipoproveedor, tipoprov, categoria, rubroâ€¦ (las que existan).
    """
    tipos = set()
    try:
        conn = db.get_conn(); cur = conn.cursor()

        def cols(tab):
            try:
                return [r[1] for r in cur.execute(f"PRAGMA table_info({tab})")]
            except Exception:
                return []

        if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proveedores'").fetchone():
            C = cols("proveedores")
            candidatos = [c for c in ("tipo_proveedor","tipo","tipoproveedor","tipoprov","categoria","rubro") if c in C]
            for c in candidatos:
                try:
                    for (t,) in cur.execute(f"SELECT DISTINCT {c} FROM proveedores WHERE {c} IS NOT NULL AND TRIM({c})<>''"):
                        tipos.add(str(t).strip())
                except Exception:
                    pass
        conn.close()
    except Exception:
        pass

    if not tipos:
        tipos = {"TejedurÃ­a", "Terminado", "TejedurÃ­a/Terminado", "Otro"}
    return sorted(tipos, key=str.lower)



class ClienteDialog(tk.Toplevel):
    """Alta/EdiciÃ³n. En 'Nuevo' muestra ID sugerido. Campo 'tipo' configurable editable/readonly."""

    FIELDS = [
        ("tipo", "Tipo"),
        ("razon_social", "RazÃ³n social *"),
        ("condicion_iva", "Cond. IVA"),
        ("cuit_dni", "CUIT/DNI"),
        ("tel1", "Tel 1"),
        ("cont1", "Contacto 1"),
        ("tel2", "Tel 2"),
        ("cont2", "Contacto 2"),
        ("email", "Email"),
        ("calle", "Calle"),
        ("nro", "Nro"),
        ("entre", "Entre"),
        ("localidad", "Localidad"),
        ("cp", "CP"),
        ("provincia", "Provincia"),
        ("estado", "Estado"),
    ]
    TIPOS = ["TejedurÃ­a", "Terminado", "TejedurÃ­a/Terminado", "Otro"]
    ESTADOS = ["activo", "inactivo"]

    def __init__(
        self,
        master,
        title,
        initial_tuple=None,
        allow_id_hint=False,
        tipo_editable=False,
    ):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result = None
    
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.vars = {}
    
        # ---------------- Tipos dinÃ¡micos (si hay helpers en DB) ----------------
        # Usamos los valores de self.TIPOS si existen; si hay funciones en db para
        # listar tipos de clientes/proveedores, las preferimos.
        tipo_values = list(getattr(self, "TIPOS", [])) or []
        try:
            if "Proveedor" in title:
                if hasattr(db, "listar_tipos_proveedores"):
                    ts = db.listar_tipos_proveedores() or []
                    tipo_values = sorted({t for t in ts if t})
            elif "Cliente" in title:
                if hasattr(db, "listar_tipos_clientes"):
                    ts = db.listar_tipos_clientes() or []
                    tipo_values = sorted({t for t in ts if t})
        except Exception:
            # Si falla, nos quedamos con self.TIPOS si estaba definido
            pass
        self.TIPO_VALUES = tipo_values
        # -----------------------------------------------------------------------
    
        # ID sugerido (sÃ³lo en "Nuevo")
        row = 0
        if allow_id_hint:
            sug = 1
            try:
                all_rows = (
                    db.listar_clientes()
                    if "Cliente" in title
                    else db.listar_proveedores()
                )
                if all_rows:
                    # suponiendo que la col 0 es el ID
                    sug = (max(r[0] for r in all_rows if r and r[0]) or 0) + 1
            except Exception:
                pass
            ttk.Label(frm, text="ID sugerido:").grid(
                row=row, column=0, sticky="e", padx=5, pady=3
            )
            e = ttk.Entry(frm, width=12)
            e.insert(0, str(sug))
            e.configure(state="disabled")
            e.grid(row=row, column=1, sticky="w", padx=5, pady=3)
            row += 1
    
        # Map inicial (para ediciÃ³n)
        init_map = {}
        if isinstance(initial_tuple, (list, tuple)) and len(initial_tuple) >= 17:
            keys = [k for k, _ in self.FIELDS]
            # en muchos modelos, initial_tuple[0] es el ID, por eso start=1
            for i, k in enumerate(keys, start=1):
                init_map[k] = initial_tuple[i]
    
        # Campos
        for k, label in self.FIELDS:
            ttk.Label(frm, text=label + ":").grid(
                row=row, column=0, sticky="e", padx=5, pady=3
            )
    
            if k == "tipo":
                # Combobox de tipo (editable si querÃ©s agregar nuevos)
                editable = bool(tipo_editable) or bool(getattr(self, "TIPO_EDITABLE", False))
                default_tipo = init_map.get(k)
                if not default_tipo:
                    default_tipo = (self.TIPO_VALUES[0] if self.TIPO_VALUES else "")
    
                v = tk.StringVar(value=default_tipo)
                self.vars[k] = v
                cb = ttk.Combobox(
                    frm,
                    textvariable=v,
                    values=self.TIPO_VALUES,
                    state=("normal" if editable else "readonly"),
                    width=40,
                )
                cb.grid(row=row, column=1, sticky="w", padx=5, pady=3)
                row += 1
                continue
    
            if k == "estado":
                v = tk.StringVar(value=(init_map.get(k) or "activo"))
                if hasattr(self, "ESTADOS") and v.get() not in self.ESTADOS:
                    v.set("activo")
                self.vars[k] = v
                cb = ttk.Combobox(
                    frm,
                    textvariable=v,
                    values=(self.ESTADOS if hasattr(self, "ESTADOS") else ["activo", "inactivo"]),
                    state="readonly",
                    width=40,
                )
                cb.grid(row=row, column=1, sticky="w", padx=5, pady=3)
            else:
                v = tk.StringVar(value=str(init_map.get(k, "") or ""))
                self.vars[k] = v
                ttk.Entry(frm, textvariable=v, width=42).grid(
                    row=row, column=1, sticky="w", padx=5, pady=3
                )
            row += 1
    
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")
    
        self.grab_set()
        self.wait_visibility()
        self.focus()
    

    def _ok(self):
        rs = (self.vars["razon_social"].get() or "").strip()
        if not rs:
            messagebox.showwarning("ValidaciÃ³n", "La RazÃ³n social es obligatoria.")
            return
        if not (self.vars.get("estado") and self.vars["estado"].get()):
            self.vars["estado"].set("activo")
        data = tuple(self.vars[k].get().strip() for k, _ in self.FIELDS)
        self.result = data
        self.destroy()


class ProveedorDialog(ClienteDialog):
    def __init__(self, master, title, initial_tuple=None):
        self.TIPOS = _tipos_proveedor_desde_bd()
        self.TIPO_EDITABLE = True  # <â€” permite escribir nuevos tipos
        super().__init__(master, title, initial_tuple)


class ChequeItemDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Agregar cheque")
        self.resizable(False, False)
        self.result = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.vars = {
            "numero": tk.StringVar(value=""),
            "banco": tk.StringVar(value=""),
            "importe": tk.StringVar(value="0"),
            "fecha_cobro": tk.StringVar(value=today_str()),
            "firmante_nombre": tk.StringVar(value=""),
            "firmante_cuit": tk.StringVar(value=""),
        }
        rows = [
            ("numero", "NÂº cheque"),
            ("banco", "Banco"),
            ("importe", "Importe"),
            ("fecha_cobro", "Fecha de pago"),
            ("firmante_nombre", "Firmante"),
            ("firmante_cuit", "CUIT firmante"),
        ]
        for i, (k, lab) in enumerate(rows):
            ttk.Label(frm, text=lab + ":").grid(
                row=i, column=0, sticky="e", padx=5, pady=3
            )
            ttk.Entry(frm, textvariable=self.vars[k], width=34).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )

        btns = ttk.Frame(frm)
        btns.grid(row=len(rows), column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Agregar", command=self._ok).pack(side="right")

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        try:
            imp = float((self.vars["importe"].get() or "0").replace(",", "."))
        except Exception:
            messagebox.showwarning("Cheque", "Importe invÃ¡lido.")
            return
        out = {k: v.get().strip() for k, v in self.vars.items()}
        out["importe"] = imp
        self.result = out
        self.destroy()


class ChequesLoteDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Cargar cheques en lote")
        self.resizable(False, False)
        self.items = []
        self.total = 0.0
        self.result = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # tabla
        cols = ("numero", "banco", "fecha_cobro", "importe", "firmante", "cuit")
        self.tree = ttk.Treeview(frm, show="headings", height=8, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            if c in ("importe", "numero"):
                self.tree.column(c, width=100, anchor="center")
            else:
                self.tree.column(c, width=130, anchor="center")
        self.tree.column("numero", width=120)
        self.tree.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        # botones
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(0, 6))
        ttk.Button(btns, text="Agregar chequeâ€¦", command=self._add_item).pack(
            side="left"
        )
        ttk.Button(btns, text="Quitar seleccionado", command=self._del_item).pack(
            side="left", padx=6
        )
        self.lbl_total = ttk.Label(btns, text="Total: 0.00")
        self.lbl_total.pack(side="right")

        # aceptar/cancelar
        act = ttk.Frame(frm)
        act.pack(fill="x")
        ttk.Button(act, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(act, text="Aceptar", command=self._ok).pack(side="right")

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _add_item(self):
        d = ChequeItemDialog(self)
        self.wait_window(d)
        if not d.result:
            return
        it = d.result
        self.items.append(it)
        self.tree.insert(
            "",
            "end",
            values=(
                it["numero"],
                it["banco"],
                it["fecha_cobro"],
                f"{it['importe']:.2f}",
                it.get("firmante_nombre", ""),
                it.get("firmante_cuit", ""),
            ),
        )
        self.total = sum(x["importe"] for x in self.items)
        self.lbl_total.config(text=f"Total: {self.total:.2f}")

    def _del_item(self):
        it = self.tree.focus()
        if not it:
            return
        vals = self.tree.item(it, "values")
        num = vals[0]
        for i, x in enumerate(self.items):
            if x["numero"] == num:
                del self.items[i]
                break
        self.tree.delete(it)
        self.total = sum(x["importe"] for x in self.items)
        self.lbl_total.config(text=f"Total: {self.total:.2f}")

    def _ok(self):
        self.result = {"items": self.items, "total": self.total}
        self.destroy()


class CCDialog(tk.Toplevel):
    """
    Alta de movimiento de CC con campo Ãºnico 'Monto'.
    - CLIENTES: si Documento=Recibo y Medio=Cheque, habilita 'Cargar chequesâ€¦' (nuevos).
    - PROVEEDORES: si Documento=Orden de Pago y Medio=Cheque, habilita 'Seleccionar chequesâ€¦' (en cartera).
    Devuelve:
      result = {
        "fecha","doc","numero","concepto","medio","monto","obs",
        "cheques_nuevos": {"items":[...], "total": float}   # opcional (CLIENTES)
        "cheques_sel": {"ids":[...], "total": float, "items":[...]}  # opcional (PROVEEDORES)
      }
    """

    DOCS_CLIENTES = [
        "recibo",
        "factura",
        "remito",
        "nota de crÃ©dito",
        "nota de dÃ©bito",
        "ajuste (-)",
        "ajuste (+)",
        "mov",
    ]
    DOCS_PROVEEDORES = [
        "orden de pago",
        "factura",
        "remito",
        "nota de crÃ©dito",
        "nota de dÃ©bito",
        "ajuste (-)",
        "ajuste (+)",
        "mov",
    ]
    MEDIO_OPCIONES = ["efectivo", "cheque", "banco", "otro"]

    def __init__(self, master, tipo: str, cuenta_flag: str, entidad_nombre: str):
        super().__init__(master)
        self.title(f"Nuevo mov CC {tipo} â€” {cuenta_flag} â€” {entidad_nombre}")
        self.resizable(False, False)
        self.result = None
        self.tipo = (tipo or "").strip().lower()  # 'clientes' | 'proveedores'
        self.cuenta_flag = (cuenta_flag or "").strip().lower()

        # buffers para cheques
        self._cheques_nuevos = None  # {"items":[...], "total": float}
        self._cheques_sel = None  # {"ids":[...], "total": float, "items":[...]}

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.vars = {
            "fecha": tk.StringVar(value=today_str()),
            "doc": tk.StringVar(
                value="recibo" if self.tipo == "clientes" else "orden de pago"
            ),
            "numero": tk.StringVar(value=""),
            "concepto": tk.StringVar(value=""),
            "medio": tk.StringVar(value="efectivo"),
            "monto": tk.StringVar(value="0"),
            "obs": tk.StringVar(value=""),
        }

        row = 0

        def lbl(text):
            ttk.Label(frm, text=text + ":").grid(
                row=row, column=0, sticky="e", padx=5, pady=3
            )

        # Fecha
        lbl("Fecha")
        ttk.Entry(frm, textvariable=self.vars["fecha"], width=36).grid(
            row=row, column=1, sticky="w", padx=5, pady=3
        )
        row += 1

        # Documento
        lbl("Documento")
        self.cbo_doc = ttk.Combobox(
            frm,
            state="readonly",
            width=33,
            values=(
                self.DOCS_CLIENTES if self.tipo == "clientes" else self.DOCS_PROVEEDORES
            ),
            textvariable=self.vars["doc"],
        )
        self.cbo_doc.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        row += 1

        # NÃºmero
        lbl("NÃºmero")
        ttk.Entry(frm, textvariable=self.vars["numero"], width=36).grid(
            row=row, column=1, sticky="w", padx=5, pady=3
        )
        row += 1

        # Concepto
        lbl("Concepto")
        ttk.Entry(frm, textvariable=self.vars["concepto"], width=36).grid(
            row=row, column=1, sticky="w", padx=5, pady=3
        )
        row += 1

        # Medio
        lbl("Medio")
        self.cbo_medio = ttk.Combobox(
            frm,
            state="readonly",
            width=33,
            values=self.MEDIO_OPCIONES,
            textvariable=self.vars["medio"],
        )
        self.cbo_medio.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        row += 1

        # Monto (Ãºnico)
        lbl("Monto")
        ttk.Entry(frm, textvariable=self.vars["monto"], width=36).grid(
            row=row, column=1, sticky="w", padx=5, pady=3
        )
        row += 1

        # Obs
        lbl("Obs")
        ttk.Entry(frm, textvariable=self.vars["obs"], width=36).grid(
            row=row, column=1, sticky="w", padx=5, pady=3
        )
        row += 1

        # Bloque botones de cheques
        blk = ttk.Frame(frm)
        blk.grid(row=row, column=0, columnspan=2, sticky="we", pady=(6, 0))
        # CLIENTES -> Recibo/Cheque
        self.btn_chq_nuevos = ttk.Button(
            blk, text="Cargar chequesâ€¦", command=self._cargar_cheques_nuevos
        )
        self.btn_chq_nuevos.pack(side="left")
        self.lbl_chq_nuevos = ttk.Label(blk, text="Cheques: â€”")
        self.lbl_chq_nuevos.pack(side="left", padx=8)

        # PROVEEDORES -> OP/Cheque
        self.btn_chq_sel = ttk.Button(
            blk,
            text="Seleccionar chequesâ€¦",
            command=self._seleccionar_cheques_existentes,
        )
        self.btn_chq_sel.pack(side="left", padx=16)
        self.lbl_chq_sel = ttk.Label(blk, text="Seleccionados: â€”")
        self.lbl_chq_sel.pack(side="left", padx=8)

        row += 1

        # Botones Aceptar / Cancelar
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(10, 0), sticky="e")
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")

        # actualizar estados de botones segÃºn selecciÃ³n
        self.cbo_doc.bind(
            "<<ComboboxSelected>>", lambda e: self._update_cheque_controls()
        )
        self.cbo_medio.bind(
            "<<ComboboxSelected>>", lambda e: self._update_cheque_controls()
        )
        self._update_cheque_controls()

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _update_cheque_controls(self):
        doc = (self.vars["doc"].get() or "").strip().lower()
        medio = (self.vars["medio"].get() or "").strip().lower()

        # clientes â†’ recibo + cheque
        enable_nuevos = (
            self.tipo == "clientes" and doc == "recibo" and medio == "cheque"
        )
        # proveedores â†’ orden de pago + cheque
        enable_sel = (
            self.tipo == "proveedores" and doc == "orden de pago" and medio == "cheque"
        )

        self.btn_chq_nuevos["state"] = "normal" if enable_nuevos else "disabled"
        self.btn_chq_sel["state"] = "normal" if enable_sel else "disabled"

        if not enable_nuevos:
            self._cheques_nuevos = None
            self.lbl_chq_nuevos.config(text="Cheques: â€”")
        if not enable_sel:
            self._cheques_sel = None
            self.lbl_chq_sel.config(text="Seleccionados: â€”")

    def _cargar_cheques_nuevos(self):
        try:
            d = ChequesLoteDialog(self)
            self.wait_window(d)
            if not d.result:
                return
            items = d.result.get("items") or []
            total = float(d.result.get("total") or 0.0)
            if not items or total <= 0:
                messagebox.showwarning("Cheques", "No hay cheques cargados.")
                return
            self._cheques_nuevos = {"items": items, "total": total}
            self.lbl_chq_nuevos.config(
                text=f"Cheques: {len(items)} â€” Total: {total:.2f}"
            )
            self.vars["monto"].set(f"{total:.2f}")
        except Exception as e:
            messagebox.showwarning(
                "Cheques", "No se pudo abrir el cargador de cheques:\n" + str(e)
            )

    def _seleccionar_cheques_existentes(self):
        try:
            d = ChequesSelectorDialog(self, cuenta_flag=self.cuenta_flag)
            self.wait_window(d)
            if not d.result:
                return
            ids = d.result.get("ids") or []
            total = float(d.result.get("total") or 0.0)
            if not ids or total <= 0:
                messagebox.showwarning("Cheques", "No seleccionaste cheques.")
                return
            self._cheques_sel = {
                "ids": ids,
                "total": total,
                "items": d.result.get("items") or [],
            }
            self.lbl_chq_sel.config(
                text=f"Seleccionados: {len(ids)} â€” Total: {total:.2f}"
            )
            self.vars["monto"].set(f"{total:.2f}")
        except Exception as e:
            messagebox.showwarning(
                "Cheques", "No se pudo abrir el selector de cheques:\n" + str(e)
            )

    def _ok(self):
        # parseo de monto
        try:
            monto = float(str(self.vars["monto"].get()).replace(",", "."))
        except Exception:
            messagebox.showwarning("ValidaciÃ³n", "Monto invÃ¡lido.")
            return

        out = {
            k: (v.get().strip() if isinstance(v.get(), str) else v.get())
            for k, v in self.vars.items()
        }
        out["monto"] = monto
        if self._cheques_nuevos:
            out["cheques_nuevos"] = self._cheques_nuevos
        if self._cheques_sel:
            out["cheques_sel"] = self._cheques_sel

        self.result = out
        self.destroy()


class CajaDialog(tk.Toplevel):
    def __init__(self, master, initial=None):
        super().__init__(master)
        self.title("Movimiento de Caja (manual)")
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.vars = {
            "fecha": tk.StringVar(value=(initial["fecha"] if initial else today_str())),
            "tipo": tk.StringVar(value=(initial["tipo"] if initial else "ingreso")),
            "medio": tk.StringVar(value=(initial["medio"] if initial else "efectivo")),
            "doc": tk.StringVar(
                value="movimiento"
            ),  # movimiento | compra de divisas | venta de divisas
            "concepto": tk.StringVar(value=(initial["concepto"] if initial else "")),
            "detalle": tk.StringVar(value=(initial["detalle"] if initial else "")),
            "monto": tk.StringVar(value=str(initial["monto"]) if initial else "0"),
            "cuenta": tk.StringVar(value=str(initial["cuenta"]) if initial else "1"),
            "tercero_tipo": tk.StringVar(
                value=(initial.get("tercero_tipo") if initial else "")
            ),
            "tercero_id": tk.StringVar(
                value=(
                    str(initial.get("tercero_id"))
                    if (initial and initial.get("tercero_id"))
                    else ""
                )
            ),
            # divisas
            "usd_monto": tk.StringVar(value="0"),
            "tc": tk.StringVar(value="0"),
        }

        r = 0
        ttk.Label(frm, text="Fecha:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        ttk.Entry(frm, textvariable=self.vars["fecha"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Tipo:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        self.cbo_tipo = ttk.Combobox(
            frm,
            textvariable=self.vars["tipo"],
            state="readonly",
            values=["ingreso", "egreso"],
            width=16,
        )
        self.cbo_tipo.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Documento:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.cbo_doc = ttk.Combobox(
            frm,
            textvariable=self.vars["doc"],
            state="readonly",
            values=["movimiento", "compra de divisas", "venta de divisas"],
            width=24,
        )
        self.cbo_doc.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Medio:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        ttk.Combobox(
            frm,
            textvariable=self.vars["medio"],
            state="readonly",
            values=["efectivo", "cheque", "banco", "otro"],
            width=16,
        ).grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Concepto:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["concepto"], width=36).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Detalle:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["detalle"], width=36).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        # Bloque DIVISAS (se muestra sÃ³lo si doc = compra/venta divisas)
        self._row_div = r
        ttk.Label(frm, text="USD (monto):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.ent_usd = ttk.Entry(frm, textvariable=self.vars["usd_monto"], width=18)
        self.ent_usd.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Tipo de cambio:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.ent_tc = ttk.Entry(frm, textvariable=self.vars["tc"], width=18)
        self.ent_tc.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Monto (ARS):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.ent_monto = ttk.Entry(frm, textvariable=self.vars["monto"], width=18)
        self.ent_monto.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Cuenta (1/2):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Combobox(
            frm,
            textvariable=self.vars["cuenta"],
            state="readonly",
            values=["1", "2"],
            width=16,
        ).grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        # Selector de Tercero
        ttk.Label(frm, text="Cliente/Proveedor:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.cbo_ter = ttk.Combobox(frm, width=34, state="readonly")
        self.cbo_ter.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        self._ter_list = []  # [(display, tipo, id)]
        self._load_terceros(initial)
        r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")

        # Eventos: mostrar/ocultar campos de divisas y autocalcular ARS
        self.cbo_doc.bind(
            "<<ComboboxSelected>>", lambda e: self._update_divisas_fields()
        )
        self.vars["usd_monto"].trace_add("write", lambda *_: self._recalc_ars())
        self.vars["tc"].trace_add("write", lambda *_: self._recalc_ars())
        self._update_divisas_fields()

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _load_terceros(self, initial):
        cl = _map_clientes_activos()
        pr = _map_proveedores_activos()
        items = []
        for k in sorted(cl.keys()):
            items.append((cl[k], "cliente", k))
        for k in sorted(pr.keys()):
            items.append((f"{pr[k]} (Proveedor)", "proveedor", k))
        self._ter_list = items
        self.cbo_ter["values"] = [x[0] for x in items]
        if initial and initial.get("tercero_id") and initial.get("tercero_tipo"):
            want = (initial["tercero_tipo"], int(initial["tercero_id"]))
            for idx, (_, t, i) in enumerate(items):
                if (t, i) == want:
                    self.cbo_ter.current(idx)
                    break

    def _update_divisas_fields(self):
        doc = (self.vars["doc"].get() or "").strip().lower()
        es_div = doc in ("compra de divisas", "venta de divisas")
        # activar/desactivar campos y fijar tipo
        for w in (self.ent_usd, self.ent_tc):
            w.configure(state=("normal" if es_div else "disabled"))
        if es_div:
            # Compra â†’ egreso ARS; Venta â†’ ingreso ARS
            self.cbo_tipo.set("egreso" if doc == "compra de divisas" else "ingreso")
            self.cbo_tipo.configure(state="disabled")
            self._recalc_ars()
        else:
            self.cbo_tipo.configure(state="readonly")

    def _recalc_ars(self):
        doc = (self.vars["doc"].get() or "").strip().lower()
        if doc not in ("compra de divisas", "venta de divisas"):
            return
        try:
            usd = float(str(self.vars["usd_monto"].get()).replace(",", ".") or 0)
            tc = float(str(self.vars["tc"].get()).replace(",", ".") or 0)
            ars = usd * tc
        except Exception:
            ars = 0.0
        self.vars["monto"].set(f"{ars:.2f}")

    def _ok(self):
        # Anti doble-click / doble-Enter
        if getattr(self, "_saving", False):
            return
        self._saving = True

        # 1) Validar monto (ARS)
        try:
            monto = float(str(self.vars["monto"].get()).replace(",", "."))
        except Exception:
            from tkinter import messagebox
            messagebox.showwarning("ValidaciÃ³n", "Monto invÃ¡lido.")
            self._saving = False
            return
    
        # 2) Tercero (cliente/proveedor) desde el combo
        sel = self.cbo_ter.current()
        tercero_tipo = ""
        tercero_id = None
        if sel >= 0:
            tercero_tipo = self._ter_list[sel][1]
            tercero_id = self._ter_list[sel][2]
    
        # 3) Armar salida base desde vars
        out = {
            k: (v.get().strip() if isinstance(v, tk.StringVar) else v.get())
            for k, v in self.vars.items()
        }
        out["monto"] = monto
        out["tercero_tipo"], out["tercero_id"] = tercero_tipo, tercero_id
    
        # 4) ValidaciÃ³n extra si es compra/venta de divisas
        doc = (out.get("doc") or "").lower()
        if doc in ("compra de divisas", "venta de divisas"):
            try:
                usd = float(str(out.get("usd_monto") or "0").replace(",", "."))
                tc  = float(str(out.get("tc") or "0").replace(",", "."))
            except Exception:
                usd = tc = 0.0
            if usd <= 0 or tc <= 0:
                from tkinter import messagebox
                messagebox.showwarning("Divisas", "CompletÃ¡ USD y Tipo de cambio (> 0).")
                self._saving = False
                return
    
        # 5) Insertar en movimientos_caja leyendo columnas reales
        conn = db.get_conn()
        cur = conn.cursor()
    
        cols = [r[1] for r in cur.execute("PRAGMA table_info(movimientos_caja)")]
        required = {"fecha", "tipo", "concepto", "monto", "cuenta"}
        faltan = [c for c in required if c not in cols]
        if faltan:
            from tkinter import messagebox
            messagebox.showwarning(
                "Caja",
                "Faltan columnas clave en movimientos_caja: " + ", ".join(faltan),
            )
            conn.close()
            self._saving = False
            return
    
        base_map = {
            "fecha": out.get("fecha") or today_str(),
            "tipo":  out.get("tipo") or "Ingreso",
            "concepto": out.get("doc") or out.get("concepto") or "Manual",
            "detalle":  out.get("detalle") or "",
            "monto": float(out.get("monto") or 0),
            "cuenta": out.get("cuenta") or "",
            "tercero_tipo": out.get("tercero_tipo"),
            "tercero_id":   out.get("tercero_id"),
        }
        t = (base_map["tipo"] or "").strip().lower()
        if t.startswith("egr"):
            base_map["tipo"] = "Egreso"
        elif t.startswith("ing"):
            base_map["tipo"] = "Ingreso"
    
        data = {k: v for k, v in base_map.items() if k in cols}
        for k in required:
            if k in data and (data[k] is None or str(data[k]).strip() == ""):
                from tkinter import messagebox
                messagebox.showwarning("Caja", f"Campo requerido vacÃ­o: {k}")
                conn.close()
                self._saving = False
                return
    
        fields = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        cur.execute(
            f"INSERT INTO movimientos_caja ({fields}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
    
        # 6) Refrescar UI y avisar
        try:
            app = getattr(self, "app", None) or getattr(self.master, "app", None) or self.master
            if hasattr(app, "tab_caj"):
                app.tab_caj.reload()
        except Exception as _e:
            print("WARN reload Caja:", _e)
    
        try:
            from tkinter import messagebox
            messagebox.showinfo("Caja", f"Movimiento guardado (ID {int(new_id)}).")
        except Exception:
            pass
    
        self.result = {"saved": True, "mov_id": int(new_id), **out}
        self.destroy()
    



# ------------------------------ PestaÃ±as base ---------------------------


class BaseTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

    def _safe(self, fn, ok_msg=None, err_ctx=""):
        try:
            fn()
            if ok_msg:
                self.app.status.set(ok_msg)
        except Exception as e:
            messagebox.showwarning(
                "PestaÃ±a",
                "Hubo un problema en "
                + (err_ctx or self.__class__.__name__)
                + ":\n"
                + str(e)
                + "\nLa app sigue funcionando.",
            )


# ------------------------------ Caja -----------------------------------


class CajaTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Nuevo (manual)", command=self.new).pack(side="left")
        ttk.Button(top, text="Borrar seleccionado", command=self.delete_selected).pack(
            side="left", padx=6
        )
        self.lbl_total = ttk.Label(top, text="Saldo total: 0.00")
        self.lbl_total.pack(side="right")

        # NUEVO ORDEN: Id, Fecha, Tipo, Concepto, Tercero, Detalle, Monto, Medio, Estado, Origen, Cuenta
        cols = (
            "id",
            "fecha",
            "tipo",
            "concepto",
            "tercero",
            "detalle",
            "monto",
            "medio",
            "estado",
            "origen",
            "cuenta",
        )
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            if c in ("concepto", "detalle", "tercero"):
                self.tree.column(c, width=240, anchor="center")
            else:
                self.tree.column(c, width=120, anchor="center")
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", self._edit_if_manual)

        self._safe(self.reload, err_ctx="CajaTab.reload()")

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        cli_map = {c[0]: c[2] for c in db.listar_clientes()}
        prv_map = {p[0]: p[2] for p in db.listar_proveedores()}

        rows = db.listar_movimientos()
        # ORDENAR por fecha DESC (mÃ¡s cercana arriba) y luego id DESC
        rows = sorted(rows, key=lambda m: ((m[1] or ""), (m[0] or 0)), reverse=True)

        total = 0.0
        for m in rows:
            tercero = ""
            if (m[7] or "") == "cliente" and m[8]:
                tercero = f"{m[8]} - {cli_map.get(m[8],'')}".strip(" -")
            elif (m[7] or "") == "proveedor" and m[8]:
                tercero = f"{m[8]} - {prv_map.get(m[8],'')}".strip(" -")
            origen = f"{m[10] or ''} {m[11] or ''}".strip()
            sign = 1 if (m[2] or "") == "ingreso" else -1
            total += sign * float(m[6] or 0)
            self.tree.insert(
                "",
                "end",
                values=(
                    m[0],
                    m[1] or "",
                    m[2] or "",
                    m[4] or "",
                    tercero,
                    m[5] or "",
                    _money(m[6]),
                    m[3] or "",
                    m[9] or "",
                    origen,
                    m[12] or "",
                ),
            )
        self.lbl_total.config(text=f"Saldo total: {_money(total)}")

    def new(self):
        dlg = CajaDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result

        # Divisas (compra/venta) â€” evitar duplicados en Caja
        doc_lower = (r.get("doc") or "").strip().lower()
        if doc_lower in ("compra de divisas", "venta de divisas"):
            try:
                usd = float(str(r.get("usd_monto") or "0").replace(",", "."))
                tc = float(str(r.get("tc") or "0").replace(",", "."))
            except Exception:
                usd = tc = 0.0
            ars = usd * tc
            tipo = "egreso" if doc_lower == "compra de divisas" else "ingreso"
        
            # 1) Â¿Ya existe un movimiento igual en Caja? (mismo dÃ­a, tipo, concepto, monto, cuenta y detalle)
            mov_id = None
            try:
                conn = db.get_conn()
                cur = conn.cursor()
                row = cur.execute(
                    """
                    SELECT id FROM movimientos_caja
                    WHERE fecha=? AND lower(tipo)=lower(?)
                      AND lower(concepto)=lower(?)
                      AND ABS(monto - ?) < 0.0001
                      AND IFNULL(cuenta,'') = ?
                      AND IFNULL(detalle,'') = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        r["fecha"],
                        tipo,
                        r["doc"],
                        float(ars or 0),
                        r.get("cuenta") or "",
                        r.get("detalle") or "",
                    ),
                ).fetchone()
                if row:
                    mov_id = int(row[0])
            except Exception:
                mov_id = None
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        
            # 2) Si no existe, lo creo una sola vez
            if not mov_id:
                mov_id = db.caja_agregar(
                    fecha=r["fecha"],
                    tipo=tipo,
                    medio=r["medio"],
                    concepto=r["doc"],
                    detalle=r.get("detalle") or "",
                    monto=ars,
                    tercero_tipo=(r.get("tercero_tipo") or None),
                    tercero_id=(int(r["tercero_id"]) if r.get("tercero_id") else None),
                    cuenta=r["cuenta"],
                )
        
            # 3) Registrar la operaciÃ³n de divisas (sÃ³lo una vez; si ya estaba, _insert_divisas_mov puede ignorar/mergear)
            _insert_divisas_mov(
                r["fecha"],
                ("compra" if doc_lower == "compra de divisas" else "venta"),
                usd,
                tc,
                ars,
                r.get("tercero_tipo"),
                r.get("tercero_id"),
                r.get("detalle") or "",
                mov_id,
            )
            try:
                # refresca la grilla de Divisas, si existe ese tab en tu app
                self.app.tab_div.reload()
            except Exception:
                pass

            self._safe(self.reload, ok_msg="OperaciÃ³n de divisas registrada.")
            return


    def _edit_if_manual(self, _evt):
        it = self.tree.focus()
        if not it:
            return
        mov_id = int(self.tree.item(it, "values")[0])
        row = _get_mov_caja(mov_id)
        if not row:
            return
        # row: 0 id,1 fecha,2 tipo,3 medio,4 concepto,5 detalle,6 monto,7 tercerotipo,8 terceroid,9 estado,10 origentipo,11 origenid,12 cuenta
        if row[10] or row[11]:
            messagebox.showinfo(
                "Caja", "SÃ³lo se pueden editar movimientos MANUALES (sin origen)."
            )
            return
        init = {
            "fecha": row[1],
            "tipo": row[2],
            "medio": row[3],
            "concepto": row[4],
            "detalle": row[5],
            "monto": row[6],
            "tercero_tipo": row[7],
            "tercero_id": row[8],
            "cuenta": row[12],
        }
        dlg = CajaDialog(self, initial=init)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result
        _update_mov_caja(
            mov_id,
            r["fecha"],
            r["tipo"],
            r["medio"],
            r["concepto"],
            r["detalle"],
            r["monto"],
            r["tercero_tipo"],
            r["tercero_id"],
            r["cuenta"],
        )
        self._safe(self.reload, ok_msg=f"Movimiento {mov_id} actualizado.")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        vals = self.tree.item(it, "values")
        mov_id = int(vals[0])
        origen_txt = vals[9]
        if origen_txt:
            messagebox.showinfo(
                "Caja", "SÃ³lo se pueden borrar movimientos MANUALES (sin origen)."
            )
            return
        if messagebox.askyesno(
            "Confirmar",
            f"Â¿Eliminar movimiento {mov_id}? Esto desvincularÃ¡ CC y Cheques si los hubiera.",
        ):
            db.borrar_mov_caja(mov_id)
            self._safe(self.reload, ok_msg="Movimiento borrado.")


# ------------------------------ Cheques --------------------------------
class ChequeDialog(tk.Toplevel):
    """
    Alta manual de cheque EN CARTERA.
    Devuelve en self.result un dict con las claves esperadas por ChequesTab.new():
      numero, banco, importe, fecha_recibido, fecha_cobro, cliente_id,
      firmante_nombre, firmante_cuit, estado, fecha_estado, obs,
      proveedor_id, cuenta_banco, gastos_bancarios, cuenta
    """

    ESTADOS = ["en_cartera", "depositado", "endosado", "rechazado"]

    def __init__(self, master):
        super().__init__(master)
        self.title("Nuevo cheque (manual)")
        self.resizable(False, False)
        self.result = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # Combos de clientes/proveedores activos â€” ORDENADOS POR ID
        cli_map = _map_clientes_activos()  # {id: "id - nombre"}
        prv_map = _map_proveedores_activos()

        self._cli_items = [(k, cli_map[k]) for k in sorted(cli_map.keys())]
        self._prv_items = [(k, prv_map[k]) for k in sorted(prv_map.keys())]

        # Vars
        self.vars = {
            "numero": tk.StringVar(value=""),
            "banco": tk.StringVar(value=""),
            "importe": tk.StringVar(value="0"),
            "fecha_recibido": tk.StringVar(value=today_str()),
            "fecha_cobro": tk.StringVar(value=today_str()),
            "cliente_idx": tk.IntVar(value=-1),
            "proveedor_idx": tk.IntVar(value=-1),
            "firmante_nombre": tk.StringVar(value=""),
            "firmante_cuit": tk.StringVar(value=""),
            "estado": tk.StringVar(value="en_cartera"),
            "fecha_estado": tk.StringVar(value=today_str()),
            "obs": tk.StringVar(value=""),
            "cuenta_banco": tk.StringVar(value=""),
            "gastos_bancarios": tk.StringVar(value="0"),
            "cuenta": tk.StringVar(value="1"),  # 1 o 2
        }

        r = 0
        ttk.Label(frm, text="NÂº cheque:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["numero"], width=28).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Banco:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        ttk.Entry(frm, textvariable=self.vars["banco"], width=28).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Importe:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["importe"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Fecha recibido:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["fecha_recibido"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Fecha de pago:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["fecha_cobro"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        # Cuenta 1 / 2
        ttk.Label(frm, text="Cuenta (1/2):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Combobox(
            frm,
            textvariable=self.vars["cuenta"],
            state="readonly",
            values=["1", "2"],
            width=16,
        ).grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        # Cliente opcional
        ttk.Label(frm, text="Cliente (opcional):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.cbo_cli = ttk.Combobox(
            frm,
            state="readonly",
            width=40,
            values=[txt for (_id, txt) in self._cli_items],
        )
        self.cbo_cli.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        self.cbo_cli.bind(
            "<<ComboboxSelected>>",
            lambda e: self.vars["cliente_idx"].set(self.cbo_cli.current()),
        )
        r += 1

        # Proveedor opcional
        ttk.Label(frm, text="Proveedor (opcional):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        self.cbo_prv = ttk.Combobox(
            frm,
            state="readonly",
            width=40,
            values=[txt for (_id, txt) in self._prv_items],
        )
        self.cbo_prv.grid(row=r, column=1, sticky="w", padx=5, pady=3)
        self.cbo_prv.bind(
            "<<ComboboxSelected>>",
            lambda e: self.vars["proveedor_idx"].set(self.cbo_prv.current()),
        )
        r += 1

        ttk.Label(frm, text="Firmante:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["firmante_nombre"], width=28).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="CUIT firmante:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["firmante_cuit"], width=28).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Estado:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        ttk.Combobox(
            frm,
            textvariable=self.vars["estado"],
            state="readonly",
            values=self.ESTADOS,
            width=18,
        ).grid(row=r, column=1, sticky="w", padx=5, pady=3)
        r += 1

        ttk.Label(frm, text="Fecha estado:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["fecha_estado"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Cuenta bancaria (depÃ³sito):").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["cuenta_banco"], width=28).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Gastos bancarios:").grid(
            row=r, column=0, sticky="e", padx=5, pady=3
        )
        ttk.Entry(frm, textvariable=self.vars["gastos_bancarios"], width=18).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        ttk.Label(frm, text="Obs:").grid(row=r, column=0, sticky="e", padx=5, pady=3)
        ttk.Entry(frm, textvariable=self.vars["obs"], width=42).grid(
            row=r, column=1, sticky="w", padx=5, pady=3
        )
        r += 1

        # Botones
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        # importe
        try:
            imp = float(str(self.vars["importe"].get()).replace(",", "."))
        except Exception:
            messagebox.showwarning("Cheque", "Importe invÃ¡lido.")
            return
        if imp <= 0:
            messagebox.showwarning("Cheque", "El importe debe ser mayor a cero.")
            return

        # cliente/proveedor opcionales (no forzamos exclusividad; podÃ©s usar uno u otro)
        cid = None
        pid = None
        if self.vars["cliente_idx"].get() >= 0:
            cid = int(self._cli_items[self.vars["cliente_idx"].get()][0])
        if self.vars["proveedor_idx"].get() >= 0:
            pid = int(self._prv_items[self.vars["proveedor_idx"].get()][0])

        # gastos bancarios
        try:
            gastos = (
                float(str(self.vars["gastos_bancarios"].get()).replace(",", "."))
                if self.vars["gastos_bancarios"].get()
                else 0.0
            )
        except Exception:
            gastos = 0.0

        out = {
            "numero": self.vars["numero"].get().strip(),
            "banco": self.vars["banco"].get().strip(),
            "importe": imp,
            "fecha_recibido": self.vars["fecha_recibido"].get().strip(),
            "fecha_cobro": self.vars["fecha_cobro"].get().strip(),
            "cliente_id": cid,
            "firmante_nombre": self.vars["firmante_nombre"].get().strip(),
            "firmante_cuit": self.vars["firmante_cuit"].get().strip(),
            "estado": self.vars["estado"].get().strip(),
            "fecha_estado": self.vars["fecha_estado"].get().strip(),
            "obs": self.vars["obs"].get().strip(),
            "proveedor_id": pid,
            "cuenta_banco": self.vars["cuenta_banco"].get().strip(),
            "gastos_bancarios": gastos,
            "cuenta": int(self.vars["cuenta"].get() or "1"),
        }

        # ValidaciÃ³n mÃ­nima adicional
        if not out["numero"]:
            messagebox.showwarning("Cheque", "CompletÃ¡ el NÂº de cheque.")
            return
        if not out["banco"]:
            messagebox.showwarning("Cheque", "CompletÃ¡ el banco.")
            return

        self.result = out
        self.destroy()


class ChequeEditDialog(tk.Toplevel):
    def __init__(self, master, initial):
        super().__init__(master)
        self.title("Editar cheque (en cartera)")
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.vars = {
            "numero": tk.StringVar(value=initial.get("numero", "")),
            "banco": tk.StringVar(value=initial.get("banco", "")),
            "importe": tk.StringVar(value=f"{float(initial.get('importe') or 0):.2f}"),
            "fecha_cobro": tk.StringVar(
                value=initial.get("fecha_cobro", "") or today_str()
            ),
            "firmante_nombre": tk.StringVar(value=initial.get("firmante_nombre", "")),
            "firmante_cuit": tk.StringVar(value=initial.get("firmante_cuit", "")),
        }
        rows = [
            ("numero", "NÂº cheque"),
            ("banco", "Banco"),
            ("importe", "Importe"),
            ("fecha_cobro", "Fecha de pago"),
            ("firmante_nombre", "Firmante"),
            ("firmante_cuit", "CUIT firmante"),
        ]
        r = 0
        for k, lab in rows:
            ttk.Label(frm, text=lab + ":").grid(
                row=r, column=0, sticky="e", padx=5, pady=3
            )
            ttk.Entry(frm, textvariable=self.vars[k], width=34).grid(
                row=r, column=1, sticky="w", padx=5, pady=3
            )
            r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        try:
            imp = float(str(self.vars["importe"].get()).replace(",", "."))
        except Exception:
            messagebox.showwarning("Cheque", "Importe invÃ¡lido.")
            return
        out = {k: v.get().strip() for k, v in self.vars.items()}
        out["importe"] = imp
        self.result = out
        self.destroy()


class ChequesTab(BaseTab):
    def __getattr__(self, name):
        # Compatibilidad con nombres viejos de botÃ³n: baj / baja / bajar
        if name in ("baj", "baja", "bajar", "btn_baja", "btn_bajar"):
            # Intento mapear a los nombres actuales mÃ¡s probables
            for cand in ("btn_bajar", "btn_baja", "bajar", "baja", "baj"):
                if cand == name:
                    continue
                # Si ya existe como atributo, lo devuelvo
                if cand in self.__dict__:
                    return self.__dict__[cand]
                try:
                    return object.__getattribute__(self, cand)
                except Exception:
                    pass

            # Si no existe ninguno, devuelvo un stub que no rompe nada
            class _NullWidget:
                def config(self, *a, **k): pass
                def configure(self, *a, **k): pass
                def state(self, *a, **k): return None
                def __setitem__(self, *a, **k): pass
                def __getitem__(self, *a, **k): return None
                def __call__(self, *a, **k): pass
            return _NullWidget()

        # Para otros atributos inexistentes, lanzar el error normal
        raise AttributeError(name)

    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Nuevo (manual)", command=self.new).pack(side="left")
        ttk.Button(top, text="Actualizar estado / Baja", command=self.baja).pack(
            side="left", padx=6
        )
        self.var_solo = tk.IntVar(value=0)
        ttk.Checkbutton(
            top, text="SÃ³lo en cartera", variable=self.var_solo, command=self.reload
        ).pack(side="left", padx=8)

        cols = (
            "id",
            "numero",
            "banco",
            "importe",
            "recibido",
            "cobro",
            "cliente",
            "recibo",
            "estado",
            "proveedor_id",
            "cuenta",
        )
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            if c in ("numero", "cliente"):
                self.tree.column(c, width=180, anchor="center")
            elif c in ("recibo",):
                self.tree.column(c, width=120, anchor="center")
            else:
                self.tree.column(c, width=120, anchor="center")
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Doble click para editar (sÃ³lo en cartera)
        self.tree.bind("<Double-1>", self._edit)
                # --- Alias de compatibilidad por cÃ³digo viejo que usa self.baj ---
        if not hasattr(self, "baj"):
            if hasattr(self, "btn_bajar"):
                self.baj = self.btn_bajar
            elif hasattr(self, "btn_baja"):
                self.baj = self.btn_baja
            else:
                # Si no existe ningÃºn botÃ³n con ese nombre, al menos evitamos el crash
                self.baj = None

        self._safe(self.reload, err_ctx="ChequesTab.reload()")

    def _leer_cheque_row(self, cid: int):
        try:
            conn = db.get_conn()
            cur = conn.cursor()
            r = cur.execute(
                """
                SELECT id, numero, banco, importe, fecha_recibido, fecha_cobro,
                       cliente_id, firmante_nombre, firmante_cuit, estado, fecha_estado,
                       obs, mov_caja_id, proveedor_id, cuenta_banco, gastos_bancarios, cuenta
                FROM cheques WHERE id=?
            """,
                (int(cid),),
            ).fetchone()
            conn.close()
            return r
        except Exception:
            return None

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_cheques()
        cli_map = {c[0]: c[2] for c in db.listar_clientes()}

        # Filtrado â€œsÃ³lo en carteraâ€
        if self.var_solo.get():
            rows = [r for r in rows if _is_en_cartera(r[9] if len(r) > 9 else "")]

        # En cartera primero (fecha de cobro asc), luego el resto por id
        en_cart, otros = [], []
        for ch in rows:
            (en_cart if _is_en_cartera(ch[9] if len(ch) > 9 else "") else otros).append(
                ch
            )

        def _fch(x):
            f = (x[5] or "") if len(x) > 5 else ""
            return f or "9999-99-99"

        en_cart = sorted(en_cart, key=lambda r: (_fch(r), r[0] or 0))
        otros = sorted(otros, key=lambda r: (r[0] or 0))
        ordered = en_cart + otros

        for ch in ordered:
            cli_txt = ""
            if ch[6]:
                cli_txt = f"{ch[6]} - {cli_map.get(ch[6], '')}".strip(" -")
            obs_txt = _leer_cheque_obs(ch[0])
            nro_rec = _extraer_numero_recibo_de_obs(obs_txt)
            self.tree.insert(
                "",
                "end",
                values=(
                    ch[0],
                    ch[1] or "",
                    ch[2] or "",
                    _money(ch[3]),
                    ch[4] or "",
                    ch[5] or "",
                    cli_txt,
                    nro_rec,
                    ch[9] or "",
                    ch[13] or "",
                    ch[16] or "",
                ),
            )

    def _edit(self, _evt):
        it = self.tree.focus()
        if not it:
            return
        vals = self.tree.item(it, "values")
        try:
            cid = int(vals[0])
        except Exception:
            return
    
        # SÃ³lo EN CARTERA
        row = self._leer_cheque_row(cid)
        if not row:
            return
        estado = row.get("estado") if isinstance(row, dict) else (row[9] if len(row) > 9 else "")
        estado = estado or ""
        if not _is_en_cartera(estado):
            from tkinter import messagebox
            messagebox.showinfo("Editar cheque", "SÃ³lo podÃ©s editar cheques EN CARTERA.")
            return
    
        # Inicial para el diÃ¡logo
        init = {
            "numero": row.get("numero") if isinstance(row, dict) else row[1],
            "banco": row.get("banco") if isinstance(row, dict) else row[2],
            "importe": row.get("importe") if isinstance(row, dict) else row[3],
            "fecha_cobro": row.get("fecha_cobro") if isinstance(row, dict) else row[5],
            "firmante_nombre": (row.get("firmante_nombre") if isinstance(row, dict) else (row[7] if len(row) > 7 else "")),
            "firmante_cuit": (row.get("firmante_cuit") if isinstance(row, dict) else (row[8] if len(row) > 8 else "")),
        }
    
        # Dialogo de ediciÃ³n
        dlg = ChequeEditDialog(self, init)
        self.wait_window(dlg)
        if not getattr(dlg, "result", None):
            return
        r = dlg.result
    
        # 1) Actualizar cheque
        _update_cheque_detalle(
            cid,
            numero=r.get("numero"),
            banco=r.get("banco"),
            importe=r.get("importe"),
            fecha_cobro=r.get("fecha_cobro"),
            firmante_nombre=(r.get("firmante_nombre") or None),
            firmante_cuit=(r.get("firmante_cuit") or None),
        )
    
        # 2) REC â€œhintâ€ tomado de la propia grilla (columna 'recibo' si existe)
        # pista desde la grilla (columna 'recibo' si existe)
        rec_hint = None
        cols = self.tree["columns"]
        try:
            idx = list(cols).index("recibo")
            rec_hint = str(vals[idx]).strip()
        except Exception:
            rec_hint = None
        
        # LLAMADA CORRECTA
        try:
            ok, info = ajustar_recibo_por_cheque_editado(cid, rec_hint)
        except Exception as ex:
            print("WARN post-edición de cheque:", ex)
            ok, info = False, {"msg": str(ex)}
        
        msg_txt = info.get("msg") if isinstance(info, dict) else str(info)
        if ok:
            mbox.showinfo("Cheque", "Se actualizó el cheque.\n" + msg_txt)
        else:
            mbox.showwarning("Cheque", "Se actualizó el cheque, pero no se pudo ajustar REC/Caja/CC:\n" + msg_txt)
        
        # Auto-refresh de pestañas (evita que tengas que refrescar a mano)
        try:
            self._safe(self.reload)
            app = getattr(self, "app", None) or getattr(self.master, "app", None)
            if getattr(app, "tab_caj", None): app.tab_caj.reload()
            if getattr(app, "tab_ccc", None): app.tab_ccc.reload()
        except Exception:
            pass

        # 3) Recalcular Caja + CC + (opcional) PDF
        try:
            ok, info = ajustar_recibo_por_cheque_editado(cid, rec_hint)
            if not ok:
                from tkinter import messagebox
                messagebox.showwarning("REC", f"Se actualizÃ³ el cheque, pero no se pudo ajustar REC/Caja/CC:\n{info.get('msg','')}")
        except Exception as _e:
            print("WARN post-ediciÃ³n de cheque:", _e)
    
        # Refrescos
        self._safe(self.reload, ok_msg="Cheque actualizado.")
        try:
            self.app.tab_caj.reload()
            self.app.tab_ccc.reload()
            self.app.tab_scc.reload()
        except Exception:
            pass
                
    def new(self):
        try:
            dlg = ChequeItemDialog(self)
            self.wait_window(dlg)
            if not dlg.result:
                return
            it = dlg.result
            data = {
                "numero": it.get("numero", ""),
                "banco": it.get("banco", ""),
                "importe": float(it.get("importe") or 0),
                "fecha_recibido": today_str(),
                "fecha_cobro": it.get("fecha_cobro") or today_str(),
                "cliente_id": None,
                "firmante_nombre": it.get("firmante_nombre", ""),
                "firmante_cuit": it.get("firmante_cuit", ""),
                "estado": "en_cartera",
                "fecha_estado": today_str(),
                "obs": "",
                "mov_caja_id": None,
                "proveedor_id": None,
                "cuenta_banco": "",
                "gastos_bancarios": 0.0,
                "cuenta": 1,
            }
            db.agregar_cheque(data)
            self._safe(self.reload, ok_msg="Cheque agregado.")
        except Exception as e:
            messagebox.showwarning("Cheques", "No se pudo cargar el cheque:\n" + str(e))

    def baja(self):
        it = self.tree.focus()
        if not it:
            return
        vals = self.tree.item(it, "values")
        cid = int(vals[0])
        estado = simpledialog.askstring(
            "Estado",
            "Nuevo estado (depositado/endosado/rechazado):",
            initialvalue="depositado",
        )
        if not estado:
            return
        proveedor_id = None
        cuenta_banco = None
        gastos = None
        if estado == "endosado":
            pid = simpledialog.askstring("Endoso", "ID proveedor receptor del cheque:")
            if pid and pid.isdigit():
                proveedor_id = int(pid)
        elif estado == "depositado":
            cuenta_banco = simpledialog.askstring(
                "DepÃ³sito", "Cuenta bancaria (alias/nro):"
            )
            try:
                gastos = float(
                    simpledialog.askstring("DepÃ³sito", "Gastos bancarios (opcional):")
                    or 0
                )
            except Exception:
                gastos = None
        if messagebox.askyesno("Confirmar", f"Actualizar cheque {cid} â†’ {estado}?"):
            db.actualizar_estado_cheque(
                cid, estado, today_str(), proveedor_id, cuenta_banco, gastos
            )
            self._safe(self.reload, ok_msg="Cheque actualizado.")


# ------------------------------ CC (Clientes / Proveedores) ------------


class CCTab(BaseTab):
    """
    - Agregar mov: genera CC + Caja. Si es Recibo/OP â†’ emite PDF (si elegÃ­s ruta).
      * Clientes/Recibo/Cheque: crea cheques (en cartera) vinculados a Caja y anota obs="REC {N}"
      * Proveedores/OP/Cheque: selecciona cheques en cartera, los marca endosados y obs="OP {N}"
    - Enviar CC: genera PDFs por cuenta sÃ³lo si saldo â‰  0 (estÃ¡ en otra parte del archivo).
    """

    def __init__(self, master, app, tipo: str):
        super().__init__(master, app)
        self.tipo = tipo  # 'clientes' | 'proveedores'
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Label(top, text=f"{tipo.capitalize()}:").pack(side="left")
        self.cbo = ttk.Combobox(top, width=44, state="readonly")
        self.cbo.pack(side="left", padx=(6, 12))
        self.cbo.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Button(top, text="Agregar mov.", command=self.add_mov).pack(side="left")
        ttk.Button(top, text="Borrar mov.", command=self.del_mov).pack(
            side="left", padx=6
        )
        ttk.Button(top, text="Enviar CC", command=self.enviar_cc).pack(
            side="left", padx=6
        )

        # label saldos en negrita
        self.app.style_bold = getattr(self.app, "style_bold", None)
        if not self.app.style_bold:
            s = ttk.Style()
            s.configure("Bold.TLabel", font=("Segoe UI Semibold", 10))
            self.app.style_bold = True
        self.lbl = ttk.Label(
            top, text="Saldos C1=0.00  C2=0.00  Total=0.00", style="Bold.TLabel"
        )
        self.lbl.pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.grid1 = self._mk_grid(self.nb, "Cuenta 1")
        self.grid2 = self._mk_grid(self.nb, "Cuenta 2")
        self._safe(self._load_entidades, err_ctx="CCTab._load_entidades()")
        self._safe(self.reload, err_ctx="CCTab.reload()")

    def _mk_grid(self, nb, title):
        frame = ttk.Frame(nb)
        nb.add(frame, text=title)
        cols = ("id", "fecha", "doc", "numero", "concepto", "medio", "debe", "haber")
        tv = ttk.Treeview(frame, show="headings", height=14, columns=cols)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=120 if c not in ("concepto",) else 240, anchor="center")
        tv.column("id", width=60)
        tv.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        return tv

    def _load_entidades(self):
        if self.tipo == "clientes":
            rows = db.listar_clientes_id_nombre()
            full = db.listar_clientes()
        else:
            rows = db.listar_proveedores_id_nombre()
            full = db.listar_proveedores()
        activos = {r[0] for r in full if _es_activo(r[16])}
        rows = [(i, n) for (i, n) in rows if i in activos]
        rows = sorted(rows, key=lambda x: x[0])  # orden por nÃºmero
        self.ents = [(r[0], r[1]) for r in rows]
        self.cbo["values"] = [f"{i} â€” {n}" for i, n in self.ents]
        if self.ents:
            self.cbo.current(0)

    def _current_ent(self):
        if not getattr(self, "ents", None) or not self.cbo.get():
            return None
        return self.ents[self.cbo.current()]

    def reload(self):
        for tv in (self.grid1, self.grid2):
            for i in tv.get_children():
                tv.delete(i)
        ent = self._current_ent()
        if not ent:
            self.lbl.config(text="Saldos C1=0.00  C2=0.00  Total=0.00")
            return
        ent_id, _ = ent
        if self.tipo == "clientes":
            s1 = db.cc_cli_listar(ent_id, "cuenta1")
            s2 = db.cc_cli_listar(ent_id, "cuenta2")
            sal1 = db.cc_cli_saldo(ent_id, "cuenta1")
            sal2 = db.cc_cli_saldo(ent_id, "cuenta2")
        else:
            s1 = db.cc_prov_listar(ent_id, "cuenta1")
            s2 = db.cc_prov_listar(ent_id, "cuenta2")
            sal1 = db.cc_prov_saldo(ent_id, "cuenta1")
            sal2 = db.cc_prov_saldo(ent_id, "cuenta2")

        # ORDEN: fecha DESC, luego id DESC
        s1 = sorted(s1, key=lambda r: ((r[2] or ""), (r[1] or 0)), reverse=True)
        s2 = sorted(s2, key=lambda r: ((r[2] or ""), (r[1] or 0)), reverse=True)

        for r in s1:
            self.grid1.insert(
                "",
                "end",
                values=(
                    r[1],
                    r[2] or "",
                    r[4] or "",
                    r[5] or "",
                    r[6] or "",
                    r[7] or "",
                    _money(r[8]),
                    _money(r[9]),
                ),
            )
        for r in s2:
            self.grid2.insert(
                "",
                "end",
                values=(
                    r[1],
                    r[2] or "",
                    r[4] or "",
                    r[5] or "",
                    r[6] or "",
                    r[7] or "",
                    _money(r[8]),
                    _money(r[9]),
                ),
            )
        self.lbl.config(
            text=f"Saldos C1={_money(sal1)}  C2={_money(sal2)}  Total={_money((sal1 or 0)+(sal2 or 0))}"
        )

    # ---------- Alta de movimiento (incluye emisiÃ³n de PDF) --------------

    def add_mov(self):
        ent = self._current_ent()
        if not ent:
            messagebox.showinfo("CC", "ElegÃ­ una entidad primero.")
            return
        ent_id, ent_name = ent
        cuenta_flag = "cuenta1" if self.nb.select() == self.nb.tabs()[0] else "cuenta2"
        cuenta_n = 1 if cuenta_flag.endswith("1") else 2

        # Abrir diÃ¡logo
        dlg = CCDialog(self, self.tipo, cuenta_flag, ent_name)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result

        def norm(s):
            return (s or "").strip().lower()

        doc = norm(r.get("doc"))
        medio = norm(r.get("medio"))
        fecha = r.get("fecha") or today_str()
        numero_in = (r.get("numero") or "").strip()
        concepto = r.get("concepto") or ""
        obs = r.get("obs") or ""
        monto = float(r.get("monto") or 0.0)

        # ---------- CLIENTES ----------
        if self.tipo == "clientes":
            # datos cliente para PDF
            cli = db.obtener_cliente(ent_id)
            cli_dict = {
                "rs": cli[2] or "",
                "cuit": cli[4] or "",
                "dir": f"{(cli[10] or '')} {(cli[11] or '')}, {(cli[13] or '')}".strip().strip(
                    ","
                ),
            }

            es_recibo = doc == "recibo"

            if es_recibo and medio == "cheque":
                chq = r.get("cheques_nuevos")
                if not chq:
                    messagebox.showwarning(
                        "CC Clientes",
                        "Elegiste Recibo/Cheque, pero no cargaste cheques.",
                    )
                    return
                total = float(chq.get("total") or 0.0)
                items = chq.get("items") or []
                if total <= 0 or not items:
                    messagebox.showwarning("CC Clientes", "No hay cheques cargados.")
                    return

                numero_cc = numero_in or db.next_num("recibo")
                # CC + Caja (haber = total)
                cc_id, caja_id = db.cc_cli_agregar_con_caja(
                    cuenta_flag,
                    fecha,
                    ent_id,
                    "REC",
                    numero_cc,
                    concepto,
                    "cheque",
                    0.0,
                    total,
                    cuenta_caja=cuenta_n,
                    cheque_id=None,
                    obs=obs,
                )
                # Guardar cheques y vincular a caja, con obs="REC {N}"
                for it in items:
                    ch_data = {
                        "numero": it.get("numero", ""),
                        "banco": it.get("banco", ""),
                        "importe": float(it.get("importe") or 0),
                        "fecha_recibido": fecha,
                        "fecha_cobro": it.get("fecha")
                        or it.get("fecha_cobro")
                        or fecha,
                        "cliente_id": ent_id,
                        "firmante_nombre": it.get("firmante_nombre", ""),
                        "firmante_cuit": it.get("firmante_cuit", ""),
                        "estado": "en_cartera",
                        "fecha_estado": fecha,
                        "obs": f"REC {numero_cc}",
                        "mov_caja_id": caja_id,
                        "proveedor_id": None,
                        "cuenta_banco": "",
                        "gastos_bancarios": 0.0,
                        "cuenta": cuenta_n,
                    }
                    cid = db.agregar_cheque(ch_data)
                    try:
                        db.set_mov_caja_en_cheque(cid, caja_id)
                    except Exception:
                        pass

                # PDF Recibo
                try:
                        out = filedialog.asksaveasfilename(
                            title="Guardar Recibo",
                            defaultextension=".pdf",
                            initialfile=f"Recibo_{numero_cc}.pdf",
                        )
                    #   if out:
                    #       _emitir_recibo_pdf(
                    #           out,
                    #           numero_cc,
                    #           fecha,
                    #           cli_dict,
                    #           concepto,
                    #           "cheque",
                    #           total,
                    #           cheques=items,
                    #       )
                    #       messagebox.showinfo("Recibo", f"Recibo generado:\n{out}")
                except Exception as ex:
                    messagebox.showwarning(
                        "Recibo", "No se pudo generar el PDF del Recibo:\n" + str(ex)
                    )

                self._safe(self.reload, ok_msg="Recibo con cheques registrado.")
                try:
                    self.app.tab_chq.reload()
                    self.app.tab_caj.reload()
                    self.app.tab_scc.reload()
                except Exception:
                    pass
                return

            # Recibo con efectivo/banco/otro â†’ CC + Caja + PDF
            if es_recibo:
                numero_cc = numero_in or db.next_num("recibo")
                db.cc_cli_agregar_con_caja(
                    cuenta_flag,
                    fecha,
                    ent_id,
                    "REC",
                    numero_cc,
                    concepto,
                    medio,
                    0.0,
                    float(monto),
                    cuenta_caja=cuenta_n,
                    cheque_id=None,
                    obs=obs,
                )
                # PDF Recibo (sin cheques)
                try:
                     out = filedialog.asksaveasfilename(
                         title="Guardar Recibo",
                         defaultextension=".pdf",
                         initialfile=f"Recibo_{numero_cc}.pdf",
                     )
                 #   if out:
                 #       _emitir_recibo_pdf(
                 #           out,
                 #           numero_cc,
                 #           fecha,
                 #           cli_dict,
                 #           concepto,
                 #           medio,
                 #           float(monto),
                 #           cheques=None,
                 #       )
                 #       messagebox.showinfo("Recibo", f"Recibo generado:\n{out}")
                except Exception as ex:
                    messagebox.showwarning(
                        "Recibo", "No se pudo generar el PDF del Recibo:\n" + str(ex)
                    )

                self._safe(self.reload, ok_msg="Recibo registrado.")
                try:
                    self.app.tab_caj.reload()
                    self.app.tab_scc.reload()
                except Exception:
                    pass
                return

            # Resto de documentos â†’ alta simple en CC
            dest = _decide_destino_monto("clientes", doc)
            debe = monto if dest == "debe" else 0.0
            haber = monto if dest == "haber" else 0.0
            db.cc_cli_agregar_mov(
                cuenta_flag,
                fecha,
                ent_id,
                r.get("doc"),
                numero_in,
                concepto,
                medio,
                debe,
                haber,
                None,
                None,
                obs,
            )
            self._safe(self.reload, ok_msg="Movimiento agregado.")
            return

        # ---------- PROVEEDORES ----------
        prv = db.obtener_proveedor(ent_id)
        prv_dict = {
            "rs": prv[2] or "",
            "cuit": prv[4] or "",
            "dir": f"{(prv[10] or '')} {(prv[11] or '')}, {(prv[13] or '')}".strip().strip(
                ","
            ),
        }
        es_op = doc == "orden de pago"

        if es_op and medio == "cheque":
            sel = r.get("cheques_sel")
            if not sel:
                messagebox.showwarning(
                    "CC Proveedores",
                    "Elegiste Orden de Pago/Cheque, pero no seleccionaste cheques.",
                )
                return
            ids = sel.get("ids") or []
            items = sel.get("items") or []
            total = float(sel.get("total") or 0.0)
            if not ids or total <= 0:
                messagebox.showwarning(
                    "CC Proveedores", "No hay cheques seleccionados."
                )
                return

            numero_op = numero_in or db.next_num("op")
            # CC + Caja (haber = total, egreso)
            cc_id, caja_id = db.cc_prov_agregar_con_caja(
                cuenta_flag,
                fecha,
                ent_id,
                "OP",
                numero_op,
                concepto,
                "cheque",
                0.0,
                total,
                cuenta_caja=cuenta_n,
                cheque_id=None,
                obs=obs,
            )
            # Cheques â†’ endosado + obs="OP {N}" + vinculaciÃ³n con caja
            for cid in ids:
                try:
                    db.actualizar_estado_cheque(
                        int(cid), "endosado", fecha, proveedor_id=ent_id
                    )
                    db.set_mov_caja_en_cheque(int(cid), caja_id)
                    # anotar obs
                    try:
                        db.actualizar_obs_cheque(int(cid), f"OP {numero_op}")
                    except Exception:
                        pass
                except Exception:
                    pass

            # PDF OP
            try:
                out = filedialog.asksaveasfilename(
                    title="Guardar Orden de Pago",
                    defaultextension=".pdf",
                    initialfile=f"OP_{numero_op}.pdf",
                )
                if out:
                    _emitir_op_pdf(
                        out,
                        numero_op,
                        fecha,
                        prv_dict,
                        concepto,
                        "cheque",
                        total,
                        cheques=items,
                    )
                    messagebox.showinfo(
                        "Orden de Pago", f"Orden de Pago generada:\n{out}"
                    )
            except Exception as ex:
                messagebox.showwarning(
                    "OP", "No se pudo generar el PDF de la Orden de Pago:\n" + str(ex)
                )

            self._safe(self.reload, ok_msg="Orden de Pago con cheques registrada.")
            try:
                self.app.tab_chq.reload()
                self.app.tab_caj.reload()
                self.app.tab_scp.reload()
            except Exception:
                pass
            return

        # OP con efectivo/banco/otro â†’ CC + Caja + PDF
        if es_op:
            numero_op = numero_in or db.next_num("op")
            db.cc_prov_agregar_con_caja(
                cuenta_flag,
                fecha,
                ent_id,
                "OP",
                numero_op,
                concepto,
                medio,
                0.0,
                float(monto),
                cuenta_caja=cuenta_n,
                cheque_id=None,
                obs=obs,
            )
            try:
                out = filedialog.asksaveasfilename(
                    title="Guardar Orden de Pago",
                    defaultextension=".pdf",
                    initialfile=f"OP_{numero_op}.pdf",
                )
                if out:
                    _emitir_op_pdf(
                        out,
                        numero_op,
                        fecha,
                        prv_dict,
                        concepto,
                        medio,
                        float(monto),
                        cheques=None,
                    )
                    messagebox.showinfo(
                        "Orden de Pago", f"Orden de Pago generada:\n{out}"
                    )
            except Exception as ex:
                messagebox.showwarning(
                    "OP", "No se pudo generar el PDF de la Orden de Pago:\n" + str(ex)
                )

            self._safe(self.reload, ok_msg="Orden de Pago registrada.")
            try:
                self.app.tab_caj.reload()
                self.app.tab_scp.reload()
            except Exception:
                pass
            return

        # Resto de documentos en Proveedores â†’ alta simple en CC
        dest = _decide_destino_monto("proveedores", doc)
        debe = monto if dest == "debe" else 0.0
        haber = monto if dest == "haber" else 0.0
        db.cc_prov_agregar_mov(
            cuenta_flag,
            fecha,
            ent_id,
            r.get("doc"),
            numero_in,
            concepto,
            medio,
            debe,
            haber,
            None,
            None,
            obs,
        )
        self._safe(self.reload, ok_msg="Movimiento agregado.")

    # ---------- Borrado de movimiento con fallback en cascada ------------

    def del_mov(self):
        ent = self._current_ent()
        if not ent:
            return

        cuenta_flag = "cuenta1" if self.nb.select() == self.nb.tabs()[0] else "cuenta2"
        tv = self.grid1 if cuenta_flag == "cuenta1" else self.grid2
        it = tv.focus()
        if not it:
            return

        vals = tv.item(it, "values")
        mov_id = int(vals[0])
        doc_lbl = (vals[2] or "").strip().upper()
        numero = (vals[3] or "").strip()

        if not messagebox.askyesno(
            "Confirmar",
            "Â¿Eliminar movimiento? Si estÃ¡ vinculado puede borrar tambiÃ©n en Caja/Cheques.",
        ):
            return

        # 1) Borrado en CC (helpers si existen; si no, fallback)
        if self.tipo == "clientes":
            try:
                db.cc_cli_borrar_cascada(cuenta_flag, mov_id)
            except Exception:
                try:
                    db.cc_cli_borrar_mov(cuenta_flag, mov_id)
                except Exception:
                    pass
        else:
            try:
                db.cc_prov_borrar_cascada(cuenta_flag, mov_id)
            except Exception:
                try:
                    db.cc_prov_borrar_mov(cuenta_flag, mov_id)
                except Exception:
                    pass

        # 2) Borrar Caja originada por este CC (bestâ€“effort)
        try:
            conn = db.get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "DELETE FROM movimientos_caja "
                    "WHERE origen_id=? AND (origen_tipo LIKE 'cc_cli%' OR origen_tipo LIKE 'cc_prov%')",
                    (mov_id,),
                )
            except Exception:
                pass
            conn.commit()
            conn.close()
        except Exception:
            pass

        # 3) CLIENTES + REC: borrar cheques EN CARTERA asociados al recibo (por OBS "REC <n>")
        if self.tipo == "clientes" and doc_lbl == "REC" and numero:
            try:
                conn = db.get_conn()
                cur = conn.cursor()
                tag = f"%REC {numero}%"
                chqs = cur.execute(
                    "SELECT id, estado FROM cheques WHERE obs IS NOT NULL AND lower(obs) LIKE lower(?)",
                    (tag,),
                ).fetchall()
                a_borrar, no_borrados = [], []
                for cid, est in chqs:
                    if _is_en_cartera(est):
                        a_borrar.append(cid)
                    else:
                        no_borrados.append((cid, est))
                for cid in a_borrar:
                    try:
                        cur.execute("DELETE FROM cheques WHERE id=?", (cid,))
                    except Exception:
                        pass
                conn.commit()
                conn.close()
                if no_borrados:
                    detalle = "\n".join(
                        [f"Cheque {cid} (estado: {est})" for cid, est in no_borrados]
                    )
                    messagebox.showinfo(
                        "Cheques no borrados",
                        "Algunos cheques no estaban EN CARTERA y no se borraron:\n\n"
                        + detalle,
                    )
            except Exception:
                pass

        self._safe(self.reload, ok_msg="Movimiento eliminado.")
        try:
            if hasattr(self.app, "tab_caj"):
                self.app.tab_caj.reload()
            if hasattr(self.app, "tab_chq"):
                self.app.tab_chq.reload()
            if hasattr(self.app, "tab_scc"):
                self.app.tab_scc.reload()
            if hasattr(self.app, "tab_scp"):
                self.app.tab_scp.reload()
        except Exception:
            pass


# -------------------- CCTab.enviar_cc (monkey patch) --------------------


def _cctab_enviar_cc(self: "CCTab"):
    """
    PDFs por cuenta sÃ³lo si saldo â‰  0.

    CLIENTES:
      - Saldo DEUDOR (>0): ir desde los movimientos mÃ¡s antiguos sumando SOLO los DEBE
        hasta cubrir el saldo. Se imprime ese tramo en orden cronolÃ³gico.
      - Saldo ACREEDOR (<0): mostrar desde el ÃšLTIMO RECIBO (doc='REC') hasta hoy
        en orden cronolÃ³gico.

    PROVEEDORES:
      - Mantengo la selecciÃ³n anterior (mÃ­nimo de renglones que cubran |saldo| desde los mÃ¡s recientes).
    """
    ent = self._current_ent()
    if not ent:
        messagebox.showinfo("Enviar CC", "ElegÃ­ una entidad primero.")
        return

    ent_id, ent_name = ent
    from datetime import date

    hoy = date.today().strftime("%Y-%m-%d")

    folder = filedialog.askdirectory(title="ElegÃ­ carpeta destino para los PDFs")
    if not folder:
        return

    # Obtener movimientos y saldos
    if self.tipo == "clientes":
        ent_row = db.obtener_cliente(ent_id)
        cuit = ent_row[4] or ""
        dir_ = f"{(ent_row[10] or '')} {(ent_row[11] or '')}, {(ent_row[13] or '')}".strip().strip(
            ","
        )
        movs_c1 = db.cc_cli_listar(ent_id, "cuenta1")
        movs_c2 = db.cc_cli_listar(ent_id, "cuenta2")
        saldo_c1 = float(db.cc_cli_saldo(ent_id, "cuenta1") or 0.0)
        saldo_c2 = float(db.cc_cli_saldo(ent_id, "cuenta2") or 0.0)
    else:
        ent_row = db.obtener_proveedor(ent_id)
        cuit = ent_row[4] or ""
        dir_ = f"{(ent_row[10] or '')} {(ent_row[11] or '')}, {(ent_row[13] or '')}".strip().strip(
            ","
        )
        movs_c1 = db.cc_prov_listar(ent_id, "cuenta1")
        movs_c2 = db.cc_prov_listar(ent_id, "cuenta2")
        saldo_c1 = float(db.cc_prov_saldo(ent_id, "cuenta1") or 0.0)
        saldo_c2 = float(db.cc_prov_saldo(ent_id, "cuenta2") or 0.0)

    hechos = []

    def _filas_pdf_generico(movs, saldo):
        # Como antes: cubrir |saldo| con los Ãºltimos (mÃ¡s recientes hacia atrÃ¡s)
        target = abs(float(saldo or 0))
        if target == 0:
            sel = []
        else:
            ordered = sorted(
                movs, key=lambda r: ((r[2] or ""), (r[1] or 0)), reverse=True
            )
            acc = 0.0
            sel = []
            for r in ordered:
                debe = float(r[8] or 0.0)
                haber = float(r[9] or 0.0)
                amt = max(debe, haber) or abs(haber - debe)
                sel.append(r)
                acc += amt
                if acc >= target:
                    break
            sel = list(reversed(sel)) if sel else list(reversed(ordered))
        filas = []
        for r in sel:
            fecha = r[2] or ""
            docnum = ((r[4] or "") + (" " + (r[5] or "") if r[5] else "")).strip()
            filas.append(
                (fecha, docnum, r[6] or "", float(r[8] or 0.0), float(r[9] or 0.0))
            )
        return filas

    def _filas_pdf_clientes(movs, saldo):
        if abs(saldo) < 0.0001:
            return []
        # Orden cronolÃ³gico ascendente por fecha, luego id
        movs_ord = sorted(movs, key=lambda r: ((r[2] or ""), (r[1] or 0)))
        if saldo > 0:
            # Deudor â†’ sumar sÃ³lo DEBE desde los mÃ¡s viejos hasta cubrir saldo
            acc = 0.0
            sel = []
            for r in movs_ord:
                debe = float(r[8] or 0.0)
                haber = float(r[9] or 0.0)
                # sumo solamente el DEBE
                if debe > 0:
                    sel.append(r)
                    acc += debe
                    if acc >= saldo:
                        break
            if not sel:
                sel = movs_ord
        else:
            # Acreedor â†’ desde el Ãºltimo REC hasta el final
            idx = None
            for i in range(len(movs_ord) - 1, -1, -1):
                if (movs_ord[i][4] or "").strip().upper() == "REC":
                    idx = i
                    break
            sel = movs_ord[idx:] if idx is not None else movs_ord

        filas = []
        for r in sel:
            fecha = r[2] or ""
            docnum = ((r[4] or "") + (" " + (r[5] or "") if r[5] else "")).strip()
            filas.append(
                (fecha, docnum, r[6] or "", float(r[8] or 0.0), float(r[9] or 0.0))
            )
        return filas

    def _emit(folder, cuenta_label, filas, saldo):
        titulo = f"Resumen de cuenta {ent_id} - {ent_name}"
        encabezado = (
            {"Entidad": ent_name, "CUIT/DNI": cuit, "DirecciÃ³n": dir_}
            if cuenta_label == "Cuenta 1"
            else {"Entidad": ent_name}
        )
        path = os.path.join(
            folder,
            f"resumen cc {'c1' if cuenta_label=='Cuenta 1' else 'c2'} {ent_id} {hoy}.pdf",
        )
        _emitir_resumen_cc_pdf(path, titulo, encabezado, cuenta_label, filas, saldo)
        return path

    # Cuenta 1
    if abs(saldo_c1) > 0.0001:
        filas = (
            _filas_pdf_clientes(movs_c1, saldo_c1)
            if self.tipo == "clientes"
            else _filas_pdf_generico(movs_c1, saldo_c1)
        )
        try:
            hechos.append(_emit(folder, "Cuenta 1", filas, saldo_c1))
        except Exception as e:
            messagebox.showwarning("Enviar CC", f"No se pudo generar C1:\n{e}")

    # Cuenta 2
    if abs(saldo_c2) > 0.0001:
        filas = (
            _filas_pdf_clientes(movs_c2, saldo_c2)
            if self.tipo == "clientes"
            else _filas_pdf_generico(movs_c2, saldo_c2)
        )
        try:
            hechos.append(_emit(folder, "Cuenta 2", filas, saldo_c2))
        except Exception as e:
            messagebox.showwarning("Enviar CC", f"No se pudo generar C2:\n{e}")

    if not hechos:
        messagebox.showinfo(
            "Enviar CC", "Ambas cuentas tienen saldo 0. No se generaron PDFs."
        )
    else:
        messagebox.showinfo("Enviar CC", "Se generaron:\n" + "\n".join(hechos))


# aplica el monkey patch
CCTab.enviar_cc = _cctab_enviar_cc


class DivisasTab(BaseTab):
    """
    PestaÃ±a Divisas con fallback y JOIN a Caja (para 'cuenta').
    Orden: fecha DESC.
    """

    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Refrescar", command=self.reload).pack(side="left")
        self.tree = ttk.Treeview(
            self, show="headings", height=18,
            columns=("id","fecha","operacion","usd","tc","total_ars","cuenta","detalle")
        )
        widths = {"id":70,"fecha":120,"operacion":140,"usd":120,"tc":100,"total_ars":140,"cuenta":90,"detalle":320}
        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c,120), anchor="center")

        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c, 120), anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._safe(self.reload, err_ctx="DivisasTab.reload()")

    def _fetch_divisas_rows(self):
        """
        Devuelve lista de dicts normalizados:
        {"id","fecha","operacion","usd","tc","total_ars","cuenta","detalle","tercero_tipo","tercero_id"}
        """
        rows = []
        try:
            conn = db.get_conn()
            cur = conn.cursor()

            def _table_exists(name):
                return bool(cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone())

            def _cols(name):
                try:
                    return [r[1] for r in cur.execute(f"PRAGMA table_info({name})")]
                except Exception:
                    return []

            if _table_exists("divisas"):
                C = _cols("divisas")
                col_tipo = "tipo" if "tipo" in C else ("operacion" if "operacion" in C else None)
                col_ars  = "ars" if "ars" in C else ("total_ars" if "total_ars" in C else None)
                has_movid = "mov_caja_id" in C

                sel = ["id","fecha"]
                if col_tipo: sel.append(col_tipo)
                if "usd" in C: sel.append("usd")
                if "tc" in C: sel.append("tc")
                if col_ars: sel.append(col_ars)
                if "cuenta" in C: sel.append("cuenta")
                if "obs" in C: sel.append("obs")  # <- usamos obs como 'detalle'
                if has_movid: sel.append("mov_caja_id")

                sql = f"SELECT {', '.join(sel)} FROM divisas ORDER BY id DESC"
                for r in cur.execute(sql):
                    rec = dict(zip(sel, r))
                    cuenta = rec.get("cuenta")
                    if (not cuenta) and has_movid and rec.get("mov_caja_id"):
                        try:
                            c_row = cur.execute("SELECT cuenta FROM movimientos_caja WHERE id=?", (int(rec["mov_caja_id"]),)).fetchone()
                            cuenta = c_row[0] if c_row else None
                        except Exception:
                            cuenta = None
                    rows.append({
                        "id": rec.get("id"),
                        "fecha": rec.get("fecha"),
                        "operacion": rec.get(col_tipo) if col_tipo else "",
                        "usd": rec.get("usd"),
                        "tc": rec.get("tc"),
                        "total_ars": rec.get(col_ars),
                        "cuenta": cuenta,
                        "detalle": rec.get("obs") or "",   # <â€” mostrar a quiÃ©n le compraste
                        "tercero_tipo": None,
                        "tercero_id": None,
                    })

            elif _table_exists("movimientos_divisas"):
                # Si tuvieras esta tabla alternativa
                for r in cur.execute("""SELECT id, fecha, operacion, usd, tc, total_ars, cuenta
                                         FROM movimientos_divisas ORDER BY id DESC"""):
                    rows.append({
                        "id": r[0], "fecha": r[1], "operacion": r[2], "usd": r[3], "tc": r[4],
                        "total_ars": r[5], "cuenta": r[6], "detalle":"", "tercero_tipo":None, "tercero_id":None
                    })
            else:
                # Fallback desde Caja (tomamos DETALLE)
                for r in cur.execute("""SELECT id, fecha, tipo, concepto, detalle, monto, cuenta
                                        FROM movimientos_caja
                                        WHERE
                                            lower(concepto) LIKE '%divisa%' OR lower(detalle) LIKE '%divisa%' OR
                                            lower(concepto) LIKE '%usd%'    OR lower(detalle) LIKE '%usd%'    OR
                                            lower(concepto) LIKE '%dÃ³lar%'  OR lower(detalle) LIKE '%dÃ³lar%'  OR
                                            lower(concepto) LIKE '%dolar%'  OR lower(detalle) LIKE '%dolar%'  OR
                                            lower(concepto) LIKE '%u$s%'    OR lower(detalle) LIKE '%u$s%'
                                        ORDER BY id DESC"""):
                    oper = "compra" if (r[2] or "").lower() == "egreso" else "venta"
                    rows.append({
                        "id": r[0], "fecha": r[1], "operacion": oper,
                        "usd": None, "tc": None, "total_ars": r[5],
                        "cuenta": r[6], "detalle": r[4] or "", "tercero_tipo": None, "tercero_id": None
                    })
            conn.close()
        except Exception:
            pass
        return rows


    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        filas = self._fetch_divisas_rows()
        cli_map, prv_map = {}, {}
        try:
            cli_map = {c[0]: c[2] for c in db.listar_clientes()}
        except Exception:
            pass
        try:
            prv_map = {p[0]: p[2] for p in db.listar_proveedores()}
        except Exception:
            pass
        for r in filas:
            total_ars = r.get("total_ars")
            try:
                total_ars_txt = f"{float(total_ars or 0):.2f}"
            except Exception:
                total_ars_txt = str(total_ars or "")

            self.tree.insert("", "end", values=(
                r.get("id",""),
                r.get("fecha",""),
                r.get("operacion",""),
                ("" if r.get("usd") is None else f"{float(r.get('usd') or 0):.2f}"),
                ("" if r.get("tc") is None else f"{float(r.get('tc') or 0):.4f}"),
                total_ars_txt,
                r.get("cuenta",""),
                r.get("detalle","")
            ))


class SaldosTab(BaseTab):
    def __init__(self, master, app, tipo: str):
        super().__init__(master, app)
        self.tipo = tipo
        cols = ("id", "nombre", "saldo_c1", "saldo_c2", "total")
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140 if c != "nombre" else 240, anchor="center")
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.lbl = ttk.Label(self, text="Totales â€” C1=0.00  C2=0.00")
        self.lbl.pack(anchor="e", padx=12, pady=(0, 8))
        self._safe(self.reload, err_ctx="SaldosTab.reload()")

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        if self.tipo == "clientes":
            ents = db.listar_clientes_id_nombre()
            get1 = lambda i: db.cc_cli_saldo(i, "cuenta1")
            get2 = lambda i: db.cc_cli_saldo(i, "cuenta2")
        else:
            ents = db.listar_proveedores_id_nombre()
            get1 = lambda i: db.cc_prov_saldo(i, "cuenta1")
            get2 = lambda i: db.cc_prov_saldo(i, "cuenta2")
        ents = sorted(ents, key=lambda x: x[0])  # orden por id
        t1 = t2 = 0.0
        for i, n in ents:
            s1 = float(get1(i) or 0.0)
            s2 = float(get2(i) or 0.0)
            if s1 == 0 and s2 == 0:
                continue
            self.tree.insert(
                "",
                "end",
                values=(i, n or f"ID {i}", _money(s1), _money(s2), _money(s1 + s2)),
            )
            t1 += s1
            t2 += s2
        self.lbl.config(text=f"Totales â€” C1={_money(t1)}  C2={_money(t2)}")


# -------------------- Helpers para vincular cheques â†” recibo -------------------


def _set_cheque_recibo_numero(cheque_id: int, numero_recibo: str):
    """
    Graba el nÃºmero de recibo dentro del campo OBS del cheque (no requiere cambiar esquema).
    Formato:  "... | REC <numero>"
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        row = cur.execute(
            "SELECT obs FROM cheques WHERE id=?", (int(cheque_id),)
        ).fetchone()
        obs = (row[0] or "") if row else ""
        # Evitar duplicado si ya estÃ¡
        tag = f"REC {numero_recibo}".strip()
        if tag not in obs:
            obs = (obs + (" | " if obs else "") + tag).strip()
            cur.execute("UPDATE cheques SET obs=? WHERE id=?", (obs, int(cheque_id)))
            conn.commit()
        conn.close()
    except Exception:
        # best-effort: no interrumpir el flujo si falla
        pass


def _leer_cheque_obs(cheque_id: int) -> str:
    """
    Devuelve el texto â€œOBS/Detalleâ€ del cheque, probando varias columnas posibles.
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cols = [ (r[1] or "").lower() for r in cur.execute("PRAGMA table_info(cheques)") ]
        txtcol = None
        for c in ("obs", "observaciones", "detalle", "nota", "comentario"):
            if c in cols:
                txtcol = c
                break
        if not txtcol:
            conn.close()
            return ""
        row = cur.execute(f"SELECT {txtcol} FROM cheques WHERE id=?", (int(cheque_id),)).fetchone()
        conn.close()
        return (row[0] or "") if row else ""
    except Exception:
        return ""


def _extraer_numero_recibo_de_obs(obs: str, prefer_last: bool = True) -> str | None:
    """
    Extrae el NÚMERO de recibo desde textos como:
      - 'REC 0001-00000049'  -> devuelve '49'
      - 'REC 00000049'       -> devuelve '49'
    """
    s = str(obs or "")
    m = re.search(r"(?i)\bREC(?:[\s:#\-]*)0*(\d+)(?:[^0-9]+0*(\d+))?", s)
    if not m:
        return None
    if m.group(2):
        # formato con punto de venta y número (tomamos la 2da parte)
        return str(int(m.group(2)))
    # formato simple: 'REC 00000049'
    return str(int(m.group(1)))

def _cheques_de_recibo(recibo_nro: str):
    """
    Devuelve lista de cheques del REC (por obs) y metadatos:
    [{"id","importe","fecha","numero","banco","cliente_id","cuenta","mov_caja_id"}]
    """
    out = []
    if not recibo_nro:
        return out
    pat = f"%REC {str(recibo_nro).strip()}%"
    try:
        conn = db.get_conn(); cur = conn.cursor()
        # Intentamos traer columnas que existan realmente
        cols = [r[1] for r in cur.execute("PRAGMA table_info(cheques)")]
        sel_cols = []
        for c in ("id","importe","fecha_cobro","numero","banco","cliente_id","cuenta","mov_caja_id"):
            if c in cols:
                sel_cols.append(c)
        if not sel_cols:
            conn.close(); return out
        sql = f"SELECT {', '.join(sel_cols)} FROM cheques WHERE UPPER(COALESCE(obs,'')) LIKE UPPER(?)"
        for r in cur.execute(sql, (pat,)):
            row = dict(zip(sel_cols, r))
            row.setdefault("fecha_cobro", row.get("fecha") or row.get("fecha_cobro") or "")
            out.append({
                "id": row.get("id"),
                "importe": float(row.get("importe") or 0.0),
                "fecha": row.get("fecha_cobro") or "",
                "numero": row.get("numero") or "",
                "banco": row.get("banco") or "",
                "cliente_id": row.get("cliente_id"),
                "cuenta": row.get("cuenta"),
                "mov_caja_id": row.get("mov_caja_id"),
            })
        conn.close()
    except Exception:
        pass
    return out

def _actualizar_caja_por_recibo(recibo_nro: str, nuevo_total: float):
    """Ajusta Monto en movimientos_caja del recibo (si estÃ¡ vinculado). Best-effort."""
    if not recibo_nro:
        return
    pat = f"%REC {str(recibo_nro).strip()}%"
    try:
        conn = db.get_conn(); cur = conn.cursor()
        # tomar mov_caja_id desde cheques del recibo
        mids = [x[0] for x in cur.execute(
            "SELECT DISTINCT mov_caja_id FROM cheques WHERE UPPER(COALESCE(obs,'')) LIKE UPPER(?) AND mov_caja_id IS NOT NULL",
            (pat,)
        ).fetchall()]
        for mid in mids:
            try:
                cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(nuevo_total), int(mid)))
            except Exception:
                pass
        conn.commit(); conn.close()
    except Exception:
        pass

def _update_cc_recibo_monto(ent_id: int, cuenta_n: int, recibo_nro: str, nuevo_haber: float) -> bool:
    """
    Ajusta el HABER del movimiento 'REC' con 'numero=recibo_nro' para el cliente ent_id
    en la CC de la cuenta indicada. Tolera tablas/columnas variadas y ceros a la izquierda en 'numero'.
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # candidatos de tablas CC
        if str(cuenta_n) == "2":
            cand = ["cc_clientes_cuenta2", "cc_cli_cuenta2", "cc_clientes", "cc_cli"]
        else:
            cand = ["cc_clientes_cuenta1", "cc_cli_cuenta1", "cc_clientes", "cc_cli"]

        def exists(t: str) -> bool:
            return bool(cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())

        def cols(t: str) -> list[str]:
            try:
                return [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
            except Exception:
                return []

        ok_any = False
        for t in cand:
            if not exists(t):
                continue
            C = cols(t)

            # adivinar columnas
            idcol = None
            for c in ("cliente_id", "entidad_id", "ent_id", "id_cliente"):
                if c in C:
                    idcol = c; break
            if not idcol: 
                continue

            numcol = "numero" if "numero" in C else None
            if not numcol:
                continue

            doccol = None
            for c in ("doc", "documento"):
                if c in C:
                    doccol = c; break

            habercol = "haber" if "haber" in C else None
            if not habercol:
                continue

            set_part = f"{habercol}=?"
            if "debe" in C:
                set_part += ", debe=0"

            # --- intento 1: igualdad directa por texto (por si 'numero' ya estÃ¡ sin ceros) ---
            where1 = f"{idcol}=? AND {numcol}=?"
            params1 = [float(nuevo_haber), int(ent_id), str(recibo_nro)]
            if doccol:
                where1 += f" AND UPPER({doccol}) IN ('REC','RECIBO')"

            cur.execute(f"UPDATE {t} SET {set_part} WHERE {where1}", params1)
            if cur.rowcount and cur.rowcount > 0:
                ok_any = True
                continue  # probÃ© en esta tabla: listo

            # --- intento 2: comparar por valor numÃ©rico (tolerando ceros a la izquierda en numero) ---
            try:
                where2 = f"{idcol}=? AND CAST({numcol} AS INTEGER)=?"
                params2 = [float(nuevo_haber), int(ent_id), int(recibo_nro)]
                if doccol:
                    where2 += f" AND UPPER({doccol}) IN ('REC','RECIBO')"
                cur.execute(f"UPDATE {t} SET {set_part} WHERE {where2}", params2)
                if cur.rowcount and cur.rowcount > 0:
                    ok_any = True
                    continue
            except Exception:
                pass

        conn.commit()
        conn.close()
        return ok_any
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return False

def _cc_recibo_campos(ent_id: int, cuenta_n: int, recibo_nro: str):
    """
    Descubre la tabla y columnas correctas para actualizar el movimiento 'REC' de la CC.
    Devuelve: (tabla, col_ent, col_doc_opc, col_numero_opc, col_haber, col_debe_opc)
      - col_doc_opc puede ser None si no existe
      - col_numero_opc puede ser None si usan 'recibo' en vez de 'numero'
      - col_debe_opc puede ser None
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # candidatos de tablas (cuenta 1/2 + legacy)
        tabs = []
        if cuenta_n in (1, 2):
            tabs.append(f"cc_clientes_cuenta{cuenta_n}")
            tabs.append(f"cc_cli_cuenta{cuenta_n}")
        tabs += ["cc_clientes", "cc_cli"]

        def _exists(t):
            return bool(cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (t,)
            ).fetchone())

        def _cols(t):
            try:
                return [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
            except Exception:
                return []

        # candidatos de nombres por columna
        ent_cols    = ["cliente_id", "entidad_id", "ent_id", "id_cliente", "id_entidad", "id"]
        doc_cols    = ["doc", "documento", "tipo_doc"]
        nro_cols    = ["numero", "nro", "num", "recibo"]  # 'recibo' a veces se guarda como campo propio
        haber_cols  = ["haber", "monto", "importe", "credit", "credito"]
        debe_cols   = ["debe", "debito", "debit"]

        for t in tabs:
            if not _exists(t):
                continue
            C = _cols(t)
            if not C:
                continue

            def pick(cands):
                for c in cands:
                    if c in C:
                        return c
                return None

            col_ent   = pick(ent_cols)
            col_doc   = pick(doc_cols)
            col_nro   = pick(nro_cols)
            col_haber = pick(haber_cols)
            col_debe  = pick(debe_cols)

            # Necesitamos al menos: entidad y 'haber' (o equivalente) y algo para identificar el recibo (numero/recibo)
            if col_ent and col_haber and col_nro:
                conn.close()
                return t, col_ent, col_doc, col_nro, col_haber, col_debe

        conn.close()
        return None, None, None, None, None, None
    except Exception:
        return None, None, None, None, None, None

def _update_cc_recibo_brutal(cur, rec_simple: str | None, rec_full: str | None, cliente_id: int | None, total: float) -> bool:
    """
    Intenta actualizar CC del cliente para un REC dado, probando muchas variantes:
    - Tablas: cc_clientes_cuenta1, cc_cli_cuenta1, cc_clientes_cuenta2, cc_cli_cuenta2, cc_clientes, cc_cli
    - Cols numero/nro/num/recibo (texto y CAST a INT)
    - doc/documento/tipo_doc = 'REC'/'recibo' si existe
    - haber/monto/importe/credit/credito
    - filtro por cliente si existe cliente_id/entidad_id/ent_id/id_cliente/cliente
    Devuelve True si tocó 1+ filas en alguna tabla.
    """
    if not rec_simple and not rec_full:
        return False

    # Listado amplio de tablas candidatas
    cand_tabs = [
        "cc_clientes_cuenta1", "cc_cli_cuenta1",
        "cc_clientes_cuenta2", "cc_cli_cuenta2",
        "cc_clientes", "cc_cli",
    ]

    # Chequea qué tablas existen realmente
    tabs = []
    for t in cand_tabs:
        ex = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        if ex:
            tabs.append(t)

    if not tabs:
        print("DBG CC: no hay tablas cc_clientes* en esta BD")
        return False

    updated = False
    for t in tabs:
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]

        # Candidatas por tipo
        num_cols   = [c for c in ("numero","nro","num","recibo") if c in cols]
        doc_col    = next((c for c in ("doc","documento","tipo_doc") if c in cols), None)
        haber_col  = next((c for c in ("haber","monto","importe","credit","credito") if c in cols), None)
        debe_col   = next((c for c in ("debe","debito","debit") if c in cols), None)
        cli_col    = next((c for c in ("cliente_id","entidad_id","ent_id","id_cliente","cliente") if c in cols), None)

        if not num_cols or not haber_col:
            print(f"DBG CC: {t} sin columnas clave (num_cols={num_cols}, haber={haber_col})")
            continue

        # Armamos candidatos de número
        num_candidates = []
        if rec_full:   num_candidates.append(("txt", rec_full))
        if rec_simple: num_candidates.append(("txt", str(rec_simple)))
        if rec_simple and str(rec_simple).isdigit():
            num_candidates.append(("int", int(rec_simple)))  # para CAST

        touched = 0

        for nc in num_cols:
            # Probá match por texto exacto
            for kind, val in num_candidates:
                where = []
                params = []

                # por número
                if kind == "int":
                    where.append(f"CAST({nc} AS INTEGER)=?")
                    params.append(int(val))
                else:
                    where.append(f"{nc}=?")
                    params.append(str(val))

                # por doc si hay
                if doc_col:
                    where.append(f"UPPER({doc_col}) IN ('REC','RECIBO')")

                # por cliente si hay
                if cli_col and cliente_id:
                    where.append(f"{cli_col}=?")
                    params.append(int(cliente_id))

                set_sql = f"{haber_col}=?"
                set_vals = [float(total)]

                # si existe DEBE, lo ponemos a 0 para REC
                if debe_col:
                    set_sql += ", " + f"{debe_col}=0"

                sql = f"UPDATE {t} SET {set_sql} WHERE {' AND '.join(where)}"
                cur.execute(sql, (*set_vals, *params))
                rc = cur.rowcount or 0
                touched += rc
                if rc:
                    print(f"DBG CC: {t} set {haber_col}={total} donde {nc}={'CAST(INT)' if kind=='int' else 'TXT'}={val} (doc/cli aplicados) -> {rc} fila(s)")
                    break  # no sigas probando más variantes en esta columna

            if touched:
                break  # ya tocamos en esta tabla con este número

        if touched:
            updated = True
        else:
            print(f"DBG CC: {t} sin coincidencias para REC simple={rec_simple} full={rec_full}")

    return updated


def ajustar_recibo_por_cheque_editado(cheque_id: int, rec_hint: str | None = None) -> tuple[bool, dict]:
    """
    Ajusta monto en Caja y CC del cliente para el REC vinculado al cheque editado.
    También ofrece reimprimir el PDF actualizado.
    Retorna (ok, info_dict).
    """
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # --- columnas de cheques disponibles
        ch_cols = [r[1] for r in cur.execute("PRAGMA table_info(cheques)")]
        pick = [c for c in ("id","obs","detalle","comentario","recibo_nro","mov_caja_id","cliente_id",
                            "importe","fecha_cobro","numero","banco") if c in ch_cols]
        row = cur.execute(f"SELECT {', '.join(pick)} FROM cheques WHERE id=?", (int(cheque_id),)).fetchone()
        if not row:
            return False, {"msg": f"Cheque {cheque_id} no existe", "recibo": None}
        ch = dict(zip(pick, row))
        print("DBG REC: cheque base =>", ch)

        # --- detectar recibo (número simple y versión con pto. de venta)
        txt_obs = str(ch.get("obs") or ch.get("detalle") or ch.get("comentario") or "")
        rec_simple = None   # '49'
        rec_full   = None   # '0001-00000049' si existe
        # 1) pista del grid
        if rec_hint:
            m = re.search(r"(?i)0*(\d+)(?:\D+0*(\d+))?", str(rec_hint))
            if m:
                if m.group(2):
                    rec_simple = str(int(m.group(2)))
                    rec_full   = f"{int(m.group(1)):04d}-{int(m.group(2)):08d}"
                else:
                    rec_simple = str(int(m.group(1)))
        # 2) columna recibo_nro
        if not rec_simple and "recibo_nro" in ch_cols and ch.get("recibo_nro"):
            rec_simple = str(int(ch.get("recibo_nro")))
        # 3) texto OBS/DETALLE/COMENTARIO
        if not rec_simple:
            m = re.search(r"(?i)\bREC(?:[\s:#\-]*)0*(\d+)(?:[^0-9]+0*(\d+))?", txt_obs)
            if m:
                if m.group(2):
                    rec_simple = str(int(m.group(2)))
                    rec_full   = f"{int(m.group(1)):04d}-{int(m.group(2)):08d}"
                else:
                    rec_simple = str(int(m.group(1)))

        mov_id     = int(ch.get("mov_caja_id") or 0) if "mov_caja_id" in ch_cols else 0
        cliente_id = int(ch.get("cliente_id") or 0)  if "cliente_id"  in ch_cols else 0
        print("DBG REC: rec_nro detectado =>", rec_simple, " mov_id=", mov_id, " cliente_id=", cliente_id)

        if not rec_simple and not mov_id:
            return False, {"msg": f"No puedo determinar el REC del cheque {cheque_id}", "recibo": None}

        # --- reunir cheques del REC y totalizarlos
        chs = []
        if rec_simple:
            if "recibo_nro" in ch_cols:
                chs = cur.execute("SELECT id, importe FROM cheques WHERE CAST(recibo_nro AS INTEGER)=?", (int(rec_simple),)).fetchall()
            if not chs and any(c in ch_cols for c in ("obs","detalle","comentario")) and rec_full:
                txtcol = "obs" if "obs" in ch_cols else ("detalle" if "detalle" in ch_cols else "comentario")
                chs = cur.execute(f"SELECT id, importe FROM cheques WHERE {txtcol} LIKE ?", (f"%REC {rec_full}%",)).fetchall()
            if not chs and any(c in ch_cols for c in ("obs","detalle","comentario")):
                txtcol = "obs" if "obs" in ch_cols else ("detalle" if "detalle" in ch_cols else "comentario")
                chs = cur.execute(f"SELECT id, importe FROM cheques WHERE {txtcol} LIKE ?", (f"%REC {rec_simple}%",)).fetchall()
        if not chs and mov_id:
            chs = cur.execute("SELECT id, importe FROM cheques WHERE mov_caja_id=?", (mov_id,)).fetchall()

        total = sum(float(imp or 0) for (_cid, imp) in (chs or []))
        print(f"DBG REC: cheques vinculados={len(chs)}, total={total}")

        if total <= 0:
            return False, {"msg": f"REC {rec_simple or '?'}: total = 0", "recibo": rec_simple}

        # --- actualizar CAJA ---
        updated_caja = False
        if mov_id:
            cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(total), int(mov_id)))
            conn.commit()
            updated_caja = True
        else:
            mc_cols = [r[1] for r in cur.execute("PRAGMA table_info(movimientos_caja)")]
            for col in ("detalle","concepto"):
                if col in mc_cols:
                    for needle in (rec_full, rec_simple):
                        if not needle:
                            continue
                        row = cur.execute(f"SELECT id FROM movimientos_caja WHERE {col} LIKE ? LIMIT 1", (f"%REC {needle}%",)).fetchone()
                        if row:
                            mov_id = int(row[0])
                            cur.execute("UPDATE movimientos_caja SET monto=? WHERE id=?", (float(total), mov_id))
                            conn.commit()
                            updated_caja = True
                            break
                if updated_caja:
                    break

        # --- actualizar CC CLIENTE ---
        # --- actualizar CC CLIENTE ---
        updated_cc = False
        if cliente_id and rec_simple:
            try:
                ok1 = _update_cc_recibo_monto(int(cliente_id), 1, str(rec_simple), float(total))
                ok2 = _update_cc_recibo_monto(int(cliente_id), 2, str(rec_simple), float(total))
                updated_cc = bool(ok1 or ok2)
            except Exception:
                updated_cc = False

        # Fallback BRUTAL: mira todas las tablas/columnas posibles
        if not updated_cc:
            try:
                updated_cc = _update_cc_recibo_brutal(cur, rec_simple, (rec_full if 'rec_full' in locals() else None), cliente_id, float(total))
                conn.commit()
            except Exception as _ex:
                print("DBG CC: fallback brutal lanzó:", _ex)
                updated_cc = False


        # fallback directo por tablas y varias formas de 'numero'
        if not updated_cc and rec_simple:
            for t in ("cc_clientes_cuenta1","cc_cli_cuenta1","cc_clientes_cuenta2","cc_cli_cuenta2","cc_clientes","cc_cli"):
                ex = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
                if not ex:
                    continue
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
                numcol = "numero" if "numero" in cols else None
                doccol = "doc" if "doc" in cols else ("documento" if "documento" in cols else None)
                habcol = "haber" if "haber" in cols else ("monto" if "monto" in cols else ("importe" if "importe" in cols else None))
                if not (numcol and habcol):
                    continue

                candidates = []
                if rec_full: candidates.append(rec_full)      # '0001-00000049'
                candidates.append(str(rec_simple))            # '49'
                try:
                    candidates.append(int(rec_simple))        # 49 (para CAST)
                except Exception:
                    pass

                touched = False
                for needle in candidates:
                    if isinstance(needle, int):
                        where = [f"CAST({numcol} AS INTEGER)=?"]
                        p = [int(needle)]
                    else:
                        where = [f"{numcol}=?"]
                        p = [str(needle)]
                    if doccol:
                        where.append(f"UPPER({doccol}) IN ('REC','RECIBO')")
                    sql = f"UPDATE {t} SET {habcol}=?, debe=0 WHERE {' AND '.join(where)}"
                    cur.execute(sql, (float(total), *p))
                    if cur.rowcount:
                        touched = True
                        break
                if touched:
                    updated_cc = True
                    break
            conn.commit()

        # --- reimprimir PDF actualizado (opcional, si el usuario acepta path)
        try:
            items = []
            if chs:
                cols_ch = [r[1] for r in cur.execute("PRAGMA table_info(cheques)")]
                pick_pdf = [c for c in ("numero","banco","fecha","fecha_cobro","importe") if c in cols_ch]
                for cid, _imp in chs:
                    r = cur.execute(f"SELECT {', '.join(pick_pdf)} FROM cheques WHERE id=?", (int(cid),)).fetchone()
                    if r:
                        d = dict(zip(pick_pdf, r))
                        items.append({
                            "numero": d.get("numero",""),
                            "banco":  d.get("banco",""),
                            "fecha":  d.get("fecha") or d.get("fecha_cobro") or today_str(),
                            "importe": float(d.get("importe") or 0.0),
                        })
            cli = _cliente_dict_from_id(int(cliente_id)) if cliente_id else {"id": None, "nombre": ""}
            fecha_pdf = today_str()
            # nombre lindo: usa full si hay, sino zfill(5) del simple
            pretty = (rec_full if rec_full else str(int(rec_simple)).zfill(5))
            out = filedialog.asksaveasfilename(
                title="Guardar Recibo ACTUALIZADO",
                defaultextension=".pdf",
                initialfile=f"REC_{pretty}_ACT_{fecha_pdf}.pdf",
            )
            if out:
                _emitir_recibo_pdf(out, (rec_full or rec_simple), fecha_pdf, cli, "Recibo", "cheque", float(total), items or None)
        except Exception:
            pass

        msg = f"REC {(rec_full or rec_simple)}: caja={'OK' if updated_caja else 'no'}; cc={'OK' if updated_cc else 'no'}; total={total:.2f}"
        print("DBG REC:", msg)
        return (updated_caja or updated_cc), {"msg": msg, "recibo": (rec_full or rec_simple)}

    except Exception as e:
        print("WARN ajustar_recibo_por_cheque_editado:", e)
        return False, {"msg": f"Error: {e}", "recibo": None}


def _cliente_dict_from_id(cid: int) -> dict:
    cli = db.obtener_cliente(cid)
    return {
        "rs": cli[2] or "",
        "cuit": cli[4] or "",
        "dir": f"{(cli[10] or '')} {(cli[11] or '')}, {(cli[13] or '')}".strip().strip(
            ","
        ),
    }


def _proveedor_dict_from_id(pid: int) -> dict:
    prv = db.obtener_proveedor(pid)
    return {
        "rs": prv[2] or "",
        "cuit": prv[4] or "",
        "dir": f"{(prv[10] or '')} {(prv[11] or '')}, {(prv[13] or '')}".strip().strip(
            ","
        ),
    }


# -------------------- Patch: CCTab.add_mov (PDF + recibo en cheques) -----------


def _cctab_add_mov_patched(self: "CCTab"):
    ent = self._current_ent()
    if not ent:
        messagebox.showinfo("CC", "ElegÃ­ una entidad primero.")
        return
    ent_id, ent_name = ent
    cuenta_flag = "cuenta1" if self.nb.select() == self.nb.tabs()[0] else "cuenta2"
    cuenta_n = 1 if cuenta_flag.endswith("1") else 2

    # Abrir diÃ¡logo
    dlg = CCDialog(self, self.tipo, cuenta_flag, ent_name)
    self.wait_window(dlg)
    if not dlg.result:
        return
    r = dlg.result

    def norm(s):
        return (s or "").strip().lower()

    doc = norm(r.get("doc"))
    medio = norm(r.get("medio"))
    fecha = r.get("fecha") or today_str()
    numero_in = (r.get("numero") or "").strip()
    concepto = r.get("concepto") or ""
    obs = r.get("obs") or ""
    monto = float(r.get("monto") or 0.0)

    # ---------- CLIENTES ----------
    if self.tipo == "clientes":
        es_recibo = doc == "recibo"

        # Recibo con cheques
        if es_recibo and medio == "cheque":
            chq = r.get("cheques_nuevos")
            if not chq:
                messagebox.showwarning(
                    "CC Clientes", "Elegiste Recibo/Cheque, pero no cargaste cheques."
                )
                return
            total = float(chq.get("total") or 0.0)
            items = chq.get("items") or []
            if total <= 0 or not items:
                messagebox.showwarning("CC Clientes", "No hay cheques cargados.")
                return

            numero_cc = numero_in or db.next_num("recibo")
            # CC + Caja (haber = total)
            cc_id, caja_id = db.cc_cli_agregar_con_caja(
                cuenta_flag,
                fecha,
                ent_id,
                "REC",
                numero_cc,
                concepto,
                "cheque",
                0.0,
                total,  # debe, haber
                cuenta_caja=cuenta_n,
                cheque_id=None,
                obs=obs,
            )
            # Guardar cheques y vincular a caja + marcar REC nro en OBS
            cheque_ids = []
            for it in items:
                ch_data = {
                    "numero": it.get("numero", ""),
                    "banco": it.get("banco", ""),
                    "importe": float(it.get("importe") or 0),
                    "fecha_recibido": fecha,
                    "fecha_cobro": it.get("fecha") or it.get("fecha_cobro") or fecha,
                    "cliente_id": ent_id,
                    "firmante_nombre": it.get("firmante_nombre", ""),
                    "firmante_cuit": it.get("firmante_cuit", ""),
                    "estado": "en_cartera",
                    "fecha_estado": fecha,
                    "obs": "",
                    "mov_caja_id": caja_id,
                    "proveedor_id": None,
                    "cuenta_banco": "",
                    "gastos_bancarios": 0.0,
                    "cuenta": cuenta_n,
                }
                cid = db.agregar_cheque(ch_data)
                cheque_ids.append(cid)
                try:
                    db.set_mov_caja_en_cheque(cid, caja_id)
                except Exception:
                    pass
                # graba "REC <numero>" en OBS
                _set_cheque_recibo_numero(cid, numero_cc)

            # PDF Recibo
            try:
                out = filedialog.asksaveasfilename(
                    title="Guardar Recibo",
                    defaultextension=".pdf",
                    initialfile=f"REC_{numero_cc}.pdf",
                )
                if out:
                     cliente_dict = _cliente_dict_from_id(ent_id)
                #    # armamos lista simple de cheques para el PDF
                #    pdf_cheques = [
                #        {
                #            "numero": it.get("numero", ""),
                #            "banco": it.get("banco", ""),
                #            "fecha": it.get("fecha") or it.get("fecha_cobro") or fecha,
                #            "importe": float(it.get("importe") or 0.0),
                #        }
                #        for it in items
                #    ]
                #    _emitir_recibo_pdf(
                #        out,
                #        numero_cc,
                #        fecha,
                #        cliente_dict,
                #        concepto,
                #        "cheque",
                #        total,
                #        pdf_cheques,
                #    )
            except Exception as ex:
                messagebox.showwarning(
                    "Recibo", f"No se pudo generar el PDF del recibo.\n{ex}"
                )

            self._safe(self.reload, ok_msg="Recibo con cheques registrado.")
            try:
                self.app.tab_chq.reload()
                self.app.tab_caj.reload()
                self.app.tab_scc.reload()
            except Exception:
                pass
            return

        # Recibo con efectivo/banco/otro â†’ CC + Caja + PDF
        if es_recibo:
            numero_cc = numero_in or db.next_num("recibo")
            db.cc_cli_agregar_con_caja(
                cuenta_flag,
                fecha,
                ent_id,
                "REC",
                numero_cc,
                concepto,
                medio,
                0.0,
                float(monto),  # haber
                cuenta_caja=cuenta_n,
                cheque_id=None,
                obs=obs,
            )
            # PDF Recibo simple (sin cheques)
            try:
                out = filedialog.asksaveasfilename(
                    title="Guardar Recibo",
                    defaultextension=".pdf",
                    initialfile=f"REC_{numero_cc}.pdf",
                )
                if out:
                     cliente_dict = _cliente_dict_from_id(ent_id)
                #    _emitir_recibo_pdf(
                #        out,
                #        numero_cc,
                #        fecha,
                #        cliente_dict,
                #        concepto,
                #        medio,
                #        float(monto),
                #        cheques=None,
                #    )
            except Exception as ex:
                messagebox.showwarning(
                    "Recibo", f"No se pudo generar el PDF del recibo.\n{ex}"
                )

            self._safe(self.reload, ok_msg="Recibo registrado.")
            try:
                self.app.tab_caj.reload()
                self.app.tab_scc.reload()
            except Exception:
                pass
            return

        # Resto de documentos (clientes) â†’ alta simple en CC
        dest = _decide_destino_monto("clientes", doc)
        debe = monto if dest == "debe" else 0.0
        haber = monto if dest == "haber" else 0.0
        db.cc_cli_agregar_mov(
            cuenta_flag,
            fecha,
            ent_id,
            r.get("doc"),
            numero_in,
            concepto,
            medio,
            debe,
            haber,
            None,
            None,
            obs,
        )
        self._safe(self.reload, ok_msg="Movimiento agregado.")
        return

    # ---------- PROVEEDORES ----------
    es_op = doc == "orden de pago"

    # OP en cheques â†’ seleccionar cheques de cartera, CC + Caja + PDF
    if es_op and medio == "cheque":
        sel = r.get("cheques_sel")
        if not sel:
            messagebox.showwarning(
                "CC Proveedores",
                "Elegiste Orden de Pago/Cheque, pero no seleccionaste cheques.",
            )
            return
        ids = sel.get("ids") or []
        total = float(sel.get("total") or 0.0)
        if not ids or total <= 0:
            messagebox.showwarning("CC Proveedores", "No hay cheques seleccionados.")
            return

        numero_op = numero_in or db.next_num("op")
        # CC + Caja (haber = total, egreso)
        cc_id, caja_id = db.cc_prov_agregar_con_caja(
            cuenta_flag,
            fecha,
            ent_id,
            "OP",
            numero_op,
            concepto,
            "cheque",
            0.0,
            total,
            cuenta_caja=cuenta_n,
            cheque_id=None,
            obs=obs,
        )
        # Cambiar estado de cada cheque a endosado â†’ proveedor actual, y vincular a caja
        for cid in ids:
            try:
                db.actualizar_estado_cheque(
                    int(cid), "endosado", fecha, proveedor_id=ent_id
                )
                db.set_mov_caja_en_cheque(int(cid), caja_id)
            except Exception:
                pass

        # PDF OP
        try:
            out = filedialog.asksaveasfilename(
                title="Guardar Orden de Pago",
                defaultextension=".pdf",
                initialfile=f"OP_{numero_op}.pdf",
            )
            if out:
                proveedor_dict = _proveedor_dict_from_id(ent_id)
                pdf_cheques = sel.get("items") or []
                _emitir_op_pdf(
                    out,
                    numero_op,
                    fecha,
                    proveedor_dict,
                    concepto,
                    "cheque",
                    total,
                    pdf_cheques,
                )
        except Exception as ex:
            messagebox.showwarning(
                "Orden de Pago", f"No se pudo generar el PDF de la Orden de Pago.\n{ex}"
            )

        self._safe(self.reload, ok_msg="Orden de Pago con cheques registrada.")
        try:
            self.app.tab_chq.reload()
            self.app.tab_caj.reload()
            self.app.tab_scp.reload()
        except Exception:
            pass
        return

    # OP con efectivo/banco/otro â†’ CC + Caja + PDF
    if es_op:
        numero_op = numero_in or db.next_num("op")
        db.cc_prov_agregar_con_caja(
            cuenta_flag,
            fecha,
            ent_id,
            "OP",
            numero_op,
            concepto,
            medio,
            0.0,
            float(monto),
            cuenta_caja=cuenta_n,
            cheque_id=None,
            obs=obs,
        )
        try:
            out = filedialog.asksaveasfilename(
                title="Guardar Orden de Pago",
                defaultextension=".pdf",
                initialfile=f"OP_{numero_op}.pdf",
            )
            if out:
                proveedor_dict = _proveedor_dict_from_id(ent_id)
                _emitir_op_pdf(
                    out,
                    numero_op,
                    fecha,
                    proveedor_dict,
                    concepto,
                    medio,
                    float(monto),
                    cheques=None,
                )
        except Exception as ex:
            messagebox.showwarning(
                "Orden de Pago", f"No se pudo generar el PDF de la Orden de Pago.\n{ex}"
            )

        self._safe(self.reload, ok_msg="Orden de Pago registrada.")
        try:
            self.app.tab_caj.reload()
            self.app.tab_scp.reload()
        except Exception:
            pass
        return

    # Resto de documentos (proveedores) â†’ alta simple en CC
    dest = _decide_destino_monto("proveedores", doc)
    debe = monto if dest == "debe" else 0.0
    haber = monto if dest == "haber" else 0.0
    db.cc_prov_agregar_mov(
        cuenta_flag,
        fecha,
        ent_id,
        r.get("doc"),
        numero_in,
        concepto,
        medio,
        debe,
        haber,
        None,
        None,
        obs,
    )
    self._safe(self.reload, ok_msg="Movimiento agregado.")


# aplicar patch
CCTab.add_mov = _cctab_add_mov_patched


# --------------- Patch: ChequesTab.reload con columna "recibo" ---------------


def _cheques_tab_reload_with_recibo(self: "ChequesTab"):
    # Columnas (asegurar que tenga 'recibo')
    want_cols = (
        "id",
        "numero",
        "banco",
        "importe",
        "recibido",
        "cobro",
        "cliente",
        "recibo",
        "estado",
        "proveedor_id",
        "cuenta",
    )
    try:
        current_cols = tuple(self.tree["columns"])
    except Exception:
        current_cols = ()
    if current_cols != want_cols:
        try:
            self.tree.destroy()
        except Exception:
            pass
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=want_cols)
        for c in want_cols:
            self.tree.heading(c, text=c)
            if c in ("numero", "cliente"):
                self.tree.column(c, width=180, anchor="center")
            elif c in ("recibo",):
                self.tree.column(c, width=120, anchor="center")
            else:
                self.tree.column(c, width=120, anchor="center")
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # poblar
    for i in self.tree.get_children():
        self.tree.delete(i)

    rows = db.listar_cheques()
    # Map clientes
    cli_map = {c[0]: c[2] for c in db.listar_clientes()}

    # Separar en cartera vs resto
    en_cart, otros = [], []
    for ch in rows:
        estado = ch[9] if len(ch) > 9 else ""
        (en_cart if _is_en_cartera(estado) else otros).append(ch)

    # Ordenar "en cartera" por fecha_cobro ASC (mÃ¡s cercana arriba). Si falta fecha, va al final.
    def _fch(x):
        f = (x[5] or "") if len(x) > 5 else ""
        return f or "9999-99-99"

    en_cart = sorted(en_cart, key=lambda r: (_fch(r), r[0] or 0))

    # Los otros no requieren orden especial; mantengo por id asc
    otros = sorted(otros, key=lambda r: (r[0] or 0))

    ordered = en_cart + otros

    for ch in ordered:
        cli_txt = ""
        if ch[6]:
            cli_txt = f"{ch[6]} - {cli_map.get(ch[6], '')}".strip(" -")
        obs_txt = _leer_cheque_obs(ch[0])
        nro_rec = _extraer_numero_recibo_de_obs(obs_txt)
        self.tree.insert(
            "",
            "end",
            values=(
                ch[0],
                ch[1] or "",
                ch[2] or "",
                _money(ch[3]),
                ch[4] or "",
                ch[5] or "",
                cli_txt,
                nro_rec,
                ch[9] or "",
                ch[13] or "",
                ch[16] or "",
            ),
        )


# aplicar patch
ChequesTab.reload = _cheques_tab_reload_with_recibo


def _pdf_resumen_cc(path, titulo, meta_texto, movimientos, saldo):
    """
    Genera un PDF simple con columnas (Fecha, Documento, Concepto, Debe, Haber) + saldo.
    movimientos: lista de tuplas (fecha, docnum, concepto, debe, haber)
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )
    s = getSampleStyleSheet()
    el = []
    el.append(Paragraph(titulo, s["Title"]))
    if meta_texto:
        el.append(Paragraph(meta_texto, s["Normal"]))
    el.append(Spacer(1, 8))

    data = [["Fecha", "Documento", "Concepto", "Debe", "Haber"]]
    for fecha, doc_lbl, concepto, debe, haber in movimientos:
        data.append(
            [
                fecha or "",
                doc_lbl or "",
                concepto or "",
                f"{float(debe or 0):,.2f}",
                f"{float(haber or 0):,.2f}",
            ]
        )
    tbl = Table(data, repeatRows=1, colWidths=[80, 140, 180, 70, 70])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    el.append(tbl)
    el.append(Spacer(1, 10))
    el.append(Paragraph(f"<b>Saldo:</b> {float(saldo or 0):,.2f}", s["Heading3"]))
    doc.build(el)


class ClientesTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Nuevo", command=self.new).pack(side="left")
        ttk.Button(top, text="Borrar", command=self.delete_selected).pack(
            side="left", padx=6
        )
        self.var_activos = tk.IntVar(value=1)
        ttk.Checkbutton(
            top, text="SÃ³lo activos", variable=self.var_activos, command=self.reload
        ).pack(side="left", padx=8)

        self.tree = ttk.Treeview(
            self,
            show="headings",
            height=18,
            columns=(
                "id",
                "tipo",
                "razon_social",
                "cuit_dni",
                "tel1",
                "email",
                "localidad",
                "estado",
            ),
        )
        for c, w in (
            ("id", 60),
            ("tipo", 130),
            ("razon_social", 240),
            ("cuit_dni", 140),
            ("tel1", 120),
            ("email", 180),
            ("localidad", 140),
            ("estado", 100),
        ):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", self._edit)
        self._safe(self.reload, err_ctx="ClientesTab.reload()")

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_clientes()
        # Orden por ID ascendente (consistente)
        rows = sorted(rows, key=lambda r: (r[0] or 0))
        solo_act = bool(self.var_activos.get())
        for r in rows:
            estado = (r[16] or "").strip().lower()
            if solo_act and estado not in ("activo", "activos", "activa"):
                continue
            self.tree.insert(
                "",
                "end",
                values=(
                    r[0],
                    r[1] or "",
                    r[2] or "",
                    r[4] or "",
                    r[5] or "",
                    r[9] or "",
                    r[13] or "",
                    r[16] or "",
                ),
            )

    def _edit(self, _):
        it = self.tree.focus()
        if not it:
            return
        cid = int(self.tree.item(it, "values")[0])
        row = db.obtener_cliente(cid)
        dlg = ClienteDialog(self, "Editar Cliente", row, allow_id_hint=False)
        self.wait_window(dlg)
        if dlg.result:
            db.editar_cliente(cid, dlg.result)
            self._safe(self.reload, ok_msg="Cliente actualizado.")

    def new(self):
        dlg = ClienteDialog(self, "Nuevo Cliente", allow_id_hint=True)
        self.wait_window(dlg)
        if dlg.result:
            db.agregar_cliente(dlg.result)
            self._safe(self.reload, ok_msg="Cliente agregado.")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        cid, rs = self.tree.item(it, "values")[0], self.tree.item(it, "values")[2]
        if messagebox.askyesno("Confirmar", f"Â¿Eliminar cliente {rs}?"):
            try:
                db.borrar_cliente(int(cid))
            except Exception as e:
                messagebox.showwarning("Clientes", "No se pudo borrar:\n" + str(e))
                return
            self._safe(self.reload, ok_msg="Cliente eliminado.")


class ProveedoresTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Nuevo", command=self.new).pack(side="left")
        ttk.Button(top, text="Borrar", command=self.delete_selected).pack(
            side="left", padx=6
        )
        self.var_activos = tk.IntVar(value=1)
        ttk.Checkbutton(
            top, text="SÃ³lo activos", variable=self.var_activos, command=self.reload
        ).pack(side="left", padx=8)

        self.tree = ttk.Treeview(
            self,
            show="headings",
            height=18,
            columns=(
                "id",
                "tipo",
                "razon_social",
                "cuit_dni",
                "tel1",
                "email",
                "localidad",
                "estado",
            ),
        )
        for c, w in (
            ("id", 60),
            ("tipo", 130),
            ("razon_social", 240),
            ("cuit_dni", 140),
            ("tel1", 120),
            ("email", 180),
            ("localidad", 140),
            ("estado", 100),
        ):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", self._edit)
        self._safe(self.reload, err_ctx="ProveedoresTab.reload()")

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_proveedores()
        rows = sorted(rows, key=lambda r: (r[0] or 0))  # orden por ID
        solo_act = bool(self.var_activos.get())
        for r in rows:
            estado = (r[16] or "").strip().lower()
            if solo_act and estado not in ("activo", "activos", "activa"):
                continue
            self.tree.insert(
                "",
                "end",
                values=(
                    r[0],
                    r[1] or "",
                    r[2] or "",
                    r[4] or "",
                    r[5] or "",
                    r[9] or "",
                    r[13] or "",
                    r[16] or "",
                ),
            )

    def _edit(self, _):
        it = self.tree.focus()
        if not it:
            return
        pid = int(self.tree.item(it, "values")[0])
        row = db.obtener_proveedor(pid)
        dlg = ProveedorDialog(self, "Editar Proveedor", row)
        self.wait_window(dlg)
        if dlg.result:
            db.editar_proveedor(pid, dlg.result)
            self._safe(self.reload, ok_msg="Proveedor actualizado.")

    def new(self):
        dlg = ProveedorDialog(self, "Nuevo Proveedor")
        self.wait_window(dlg)
        if dlg.result:
            db.agregar_proveedor(dlg.result)
            self._safe(self.reload, ok_msg="Proveedor agregado.")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        pid, rs = self.tree.item(it, "values")[0], self.tree.item(it, "values")[2]
        if messagebox.askyesno("Confirmar", f"Â¿Eliminar proveedor {rs}?"):
            try:
                db.borrar_proveedor(int(pid))
            except Exception as e:
                messagebox.showwarning("Proveedores", "No se pudo borrar:\n" + str(e))
                return
            self._safe(self.reload, ok_msg="Proveedor eliminado.")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x840")
        self.minsize(1120, 720)

        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", font=("Segoe UI", 10))
        s.configure("Title.TLabel", font=("Segoe UI Semibold", 14))

        # MenÃº
        self._build_menu()

        # Toolbar
        tb = ttk.Frame(self, padding=(8, 6))
        tb.pack(fill="x")
        if os.path.exists(LOGO_FILE):
            try:
                self._logo = tk.PhotoImage(file=LOGO_FILE)
                ttk.Label(tb, image=self._logo).pack(side="left", padx=(0, 8))
            except Exception:
                pass
        ttk.Label(tb, text="GestiÃ³n Textil â€” Operativa", style="Title.TLabel").pack(
            side="left"
        )

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        # PestaÃ±as
        try:
            self.tab_cli = ClientesTab(self.nb, self)
            self.nb.add(self.tab_cli, text="Clientes")
        except Exception as e:
            messagebox.showwarning("Clientes", f"Error al cargar Clientes:\n{e}")

        try:
            self.tab_prv = ProveedoresTab(self.nb, self)
            self.nb.add(self.tab_prv, text="Proveedores")
        except Exception as e:
            messagebox.showwarning("Proveedores", f"Error al cargar Proveedores:\n{e}")

        try:
            self.tab_caj = CajaTab(self.nb, self)
            self.nb.add(self.tab_caj, text="Caja")
        except Exception as e:
            messagebox.showwarning("Caja", f"Error al cargar Caja:\n{e}")

        try:
            self.tab_chq = ChequesTab(self.nb, self)
            self.nb.add(self.tab_chq, text="Cheques")
        except Exception as e:
            messagebox.showwarning("Cheques", f"Error al cargar Cheques:\n{e}")

        try:
            self.tab_ccc = CCTab(self.nb, self, "clientes")
            self.nb.add(self.tab_ccc, text="CC Clientes")
        except Exception as e:
            messagebox.showwarning("CC Clientes", f"Error al cargar CC Clientes:\n{e}")

        try:
            self.tab_ccp = CCTab(self.nb, self, "proveedores")
            self.nb.add(self.tab_ccp, text="CC Proveedores")
        except Exception as e:
            messagebox.showwarning(
                "CC Proveedores", f"Error al cargar CC Proveedores:\n{e}"
            )

        try:
            self.tab_scc = SaldosTab(self.nb, self, "clientes")
            self.nb.add(self.tab_scc, text="Saldos CC")
        except Exception as e:
            messagebox.showwarning("Saldos CC", f"Error al cargar Saldos CC:\n{e}")

        try:
            self.tab_scp = SaldosTab(self.nb, self, "proveedores")
            self.nb.add(self.tab_scp, text="Saldos Proveedores")
        except Exception as e:
            messagebox.showwarning(
                "Saldos Proveedores", f"Error al cargar Saldos Proveedores:\n{e}"
            )

        # Divisas (si no existe tabla, la pestaÃ±a igual aparece con fallback)
        try:
            self.tab_div = DivisasTab(self.nb, self)
            self.nb.add(self.tab_div, text="Divisas")
        except Exception as e:
            messagebox.showwarning("Divisas", "Error al cargar Divisas:\n" + str(e))

        # Status
        self.status = tk.StringVar(value="Listo.")
        ttk.Label(self, textvariable=self.status).pack(fill="x", padx=8, pady=4)

    # ----------------------------- MenÃº -----------------------------
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)

        # Archivo
        m_arch = tk.Menu(m, tearoff=0)
        m_arch.add_command(label="Backup BDâ€¦", command=self._backup)
        m_arch.add_separator()
        m_arch.add_command(label="Salir", command=self.destroy)
        m.add_cascade(label="Archivo", menu=m_arch)

        # Importar (CSV: fecha,id,doc,numero,concepto,medio,debe,haber)
        m_imp = tk.Menu(m, tearoff=0)
        m_imp.add_command(
            label="Importar CC Clientes â€” Cuenta 1 (CSV)",
            command=lambda: self._import_cc("clientes", 1),
        )
        m_imp.add_command(
            label="Importar CC Clientes â€” Cuenta 2 (CSV)",
            command=lambda: self._import_cc("clientes", 2),
        )
        m_imp.add_separator()
        m_imp.add_command(
            label="Importar CC Proveedores â€” Cuenta 1 (CSV)",
            command=lambda: self._import_cc("proveedores", 1),
        )
        m_imp.add_command(
            label="Importar CC Proveedores â€” Cuenta 2 (CSV)",
            command=lambda: self._import_cc("proveedores", 2),
        )
        m.add_cascade(label="Importar", menu=m_imp)

        # Ver
        m_ver = tk.Menu(m, tearoff=0)
        m_ver.add_command(label="Refrescar pestaÃ±a", command=self._refresh_tab)
        m.add_cascade(label="Ver", menu=m_ver)

        # Herramientas (resets rÃ¡pidos con confirmaciÃ³n y fallback SQL)
        m_tools = tk.Menu(m, tearoff=0)

        m_tools.add_command(label="Reset Cajaâ€¦", command=self._tools_reset_caja)
        m_tools.add_command(label="Reset Chequesâ€¦", command=self._tools_reset_cheques)

        m_tools.add_separator()

        sub_cli = tk.Menu(m_tools, tearoff=0)
        sub_cli.add_command(
            label="CC Clientes â€” Reset TOTALâ€¦",
            command=lambda: self._tools_reset_cc("clientes", None),
        )
        sub_cli.add_command(
            label="CC Clientes â€” Reset por IDâ€¦",
            command=lambda: self._tools_reset_cc("clientes", "by_id"),
        )
        m_tools.add_cascade(label="CC Clientes", menu=sub_cli)

        sub_prv = tk.Menu(m_tools, tearoff=0)
        sub_prv.add_command(
            label="CC Proveedores â€” Reset TOTALâ€¦",
            command=lambda: self._tools_reset_cc("proveedores", None),
        )
        sub_prv.add_command(
            label="CC Proveedores â€” Reset por IDâ€¦",
            command=lambda: self._tools_reset_cc("proveedores", "by_id"),
        )
        m_tools.add_cascade(label="CC Proveedores", menu=sub_prv)

        m.add_cascade(label="Herramientas", menu=m_tools)

    # --------------------------- Acciones ---------------------------
    def _refresh_tab(self):
        cur = self.nb.select()
        if not cur:
            messagebox.showinfo("Ver", "No hay pestaÃ±as cargadas.")
            return
        page = self.nb.nametowidget(cur)
        if hasattr(page, "reload"):
            try:
                page.reload()
                self.status.set("PestaÃ±a recargada.")
            except Exception as e:
                messagebox.showwarning(
                    "Recargar", f"No se pudo recargar esta pestaÃ±a:\n{e}"
                )

    def _backup(self):
        folder = filedialog.askdirectory(title="ElegÃ­ una carpeta de destino")
        if not folder:
            return
        import shutil
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(folder, f"backup_{ts}_" + os.path.basename(str(DB_PATH)))
        try:
            shutil.copyfile(str(DB_PATH), dest)
            messagebox.showinfo("Backup", "Backup creado:\n" + dest)
        except Exception as e:
            messagebox.showwarning("Backup", "No se pudo crear el backup:\n" + str(e))

    def _import_cc(self, tipo: str, cuenta: int):
        """
        Importa CSV robusto con columnas (mÃ­nimas): fecha, id, doc, numero, concepto, medio, debe, haber
        - Detecta delimitador automÃ¡ticamente
        - Normaliza encabezados
        - Acepta coma decimal, sÃ­mbolos $ y miles
        - Medio por defecto 'otro' (antes 'otros' â†’ tiraba error)
        - Acepta fechas dd/mm/yyyy o yyyy-mm-dd
        """
        path = filedialog.askopenfilename(
            title="ElegÃ­ CSV", filetypes=[("CSV", "*.csv"), ("Todos", "*.*")]
        )
        if not path:
            return

        import io

        ok = 0
        err = 0
        sample_errors = []

        # 1) Detectar dialecto
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                head = f.read(4096)
            try:
                sniff = csv.Sniffer().sniff(head)
            except Exception:
                sniff = csv.excel
        except Exception:
            # fallback latin-1
            with open(path, "r", encoding="latin-1", newline="") as f:
                head = f.read(4096)
            try:
                sniff = csv.Sniffer().sniff(head)
            except Exception:
                sniff = csv.excel

        # 2) Leer con DictReader + normalizaciÃ³n de headers
        def _iter_rows():
            # intentamos UTF-8 con BOM
            try:
                fh = open(path, "r", encoding="utf-8-sig", newline="")
            except Exception:
                fh = open(path, "r", encoding="latin-1", newline="")
            with fh:
                rd = csv.DictReader(fh, dialect=sniff)
                # normalizamos los nombres de columnas
                norm_fields = [_norm_header_key(c) for c in (rd.fieldnames or [])]
                # Si vienen repetidas, DictReader se queda con la Ãºltima; no pasa nada
                for raw in rd:
                    row = {}
                    for k, v in raw.items():
                        nk = _norm_header_key(k)
                        row[nk] = v
                    yield row

        for r in _iter_rows():
            try:
                fecha = _parse_date_flexible(r.get("fecha") or today_str())
                doc = _strip_bom(r.get("doc") or "MOV")
                numero = _strip_bom(r.get("numero") or "")
                concepto = _strip_bom(r.get("concepto") or "")
                medio = _norm_medio(r.get("medio") or "otro")
                debe = _parse_float_flexible(r.get("debe"))
                haber = _parse_float_flexible(r.get("haber"))

                # ID de entidad
                ent_raw = r.get("id")
                if ent_raw is None or str(ent_raw).strip() == "":
                    raise ValueError("ID vacÃ­o")
                try:
                    ent = int(str(ent_raw).strip())
                except Exception:
                    raise ValueError(f"ID invÃ¡lido: {ent_raw!r}")

                # Inserciones
                if tipo == "clientes":
                    db.cc_cli_agregar_mov(
                        f"cuenta{cuenta}",
                        fecha,
                        ent,
                        doc,
                        numero,
                        concepto,
                        medio,
                        float(debe or 0),
                        float(haber or 0),
                        None,
                        None,
                        r.get("obs"),
                    )
                else:
                    db.cc_prov_agregar_mov(
                        f"cuenta{cuenta}",
                        fecha,
                        ent,
                        doc,
                        numero,
                        concepto,
                        medio,
                        float(debe or 0),
                        float(haber or 0),
                        None,
                        None,
                        r.get("obs"),
                    )
                ok += 1
            except Exception as ex:
                err += 1
                if len(sample_errors) < 5:
                    # guardo una muestra de errores para diagnosticar
                    sample_errors.append(str(ex))

        # refrescos best-effort
        try:
            self.tab_ccc.reload()
            self.tab_ccp.reload()
            self.tab_scc.reload()
            self.tab_scp.reload()
        except Exception:
            pass

        msg = f"Importados OK: {ok}\nErrores: {err}"
        if sample_errors:
            msg += "\n\nEjemplos de error:\n- " + "\n- ".join(sample_errors)
        messagebox.showinfo("Importar", msg)

    # --------------------------- Herramientas / Resets ---------------------------

    def _tools_reset_caja(self):
        if not messagebox.askyesno(
            "Reset Caja",
            "Â¿Borrar TODOS los movimientos de caja? Esta acciÃ³n no se puede deshacer.",
        ):
            return
        try:
            if hasattr(db, "reset_caja"):
                db.reset_caja()
            else:
                conn = db.get_conn()
                cur = conn.cursor()
                # Desvincular cheques de caja (si existe la columna)
                try:
                    cur.execute("UPDATE cheques SET mov_caja_id=NULL")
                except Exception:
                    pass
                cur.execute("DELETE FROM movimientos_caja")
                conn.commit()
                conn.close()
            try:
                self.tab_caj.reload()
            except Exception:
                pass
            self.status.set("Caja reseteada.")
            messagebox.showinfo("Reset Caja", "Listo. Caja vaciada.")
        except Exception as e:
            messagebox.showwarning("Reset Caja", "No se pudo resetear Caja:\n" + str(e))

    def _tools_reset_cheques(self):
        if not messagebox.askyesno(
            "Reset Cheques",
            "Â¿Borrar TODOS los cheques? Esta acciÃ³n no se puede deshacer.",
        ):
            return
        try:
            if hasattr(db, "reset_cheques"):
                db.reset_cheques()
            else:
                conn = db.get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM cheques")
                conn.commit()
                conn.close()
            try:
                self.tab_chq.reload()
            except Exception:
                pass
            self.status.set("Cheques reseteados.")
            messagebox.showinfo("Reset Cheques", "Listo. Cheques vaciados.")
        except Exception as e:
            messagebox.showwarning(
                "Reset Cheques", "No se pudo resetear Cheques:\n" + str(e)
            )

    def _tools_reset_cc(self, tipo: str, mode: str | None):
        """
        tipo: 'clientes' | 'proveedores'
        mode: None (TOTAL) | 'by_id'
        """
        if mode == "by_id":
            raw = simpledialog.askstring(
                "Reset por ID", "IngresÃ¡ ID(s) separados por coma (ej: 101, 205):"
            )
            if not raw:
                return
            ids = []
            for part in raw.split(","):
                part = (part or "").strip()
                if part.isdigit():
                    ids.append(int(part))
            if not ids:
                messagebox.showinfo("Reset CC", "No se ingresaron IDs vÃ¡lidos.")
                return
            if not messagebox.askyesno(
                "Confirmar",
                f"Â¿Resetear CC de {tipo} para IDs: {', '.join(map(str, ids))}?",
            ):
                return
            touched = _reset_cc_by_ids(tipo, ids)
            messagebox.showinfo("Reset CC", f"OK. Se afectaron {touched} tabla(s).")
        else:
            if not messagebox.askyesno(
                "Confirmar", f"Â¿Resetear TOTAL la CC de {tipo}?"
            ):
                return
            touched = self._reset_cc_total(tipo)
            messagebox.showinfo("Reset CC", f"OK. Se vaciaron {touched} tabla(s).")

        # refrescos (best-effort)
        try:
            if tipo == "clientes":
                self.tab_ccc.reload()
                self.tab_scc.reload()
            else:
                self.tab_ccp.reload()
                self.tab_scp.reload()
        except Exception:
            pass
        self.status.set("Reset CC completado.")

    def _reset_cc_total(self, tipo: str) -> int:
        """
        Reset total = junta todos los IDs y delega en _reset_cc_by_ids.
        """
        ids: list[int] = []
        try:
            if tipo == "clientes":
                # listar_clientes_id_nombre() -> [(id, nombre), ...]
                ids = [i for (i, _) in db.listar_clientes_id_nombre()]
            else:
                ids = [i for (i, _) in db.listar_proveedores_id_nombre()]
        except Exception as ex:
            print("ERR listando IDs para reset total:", ex)
            return 0
    
        return self._reset_cc_by_ids(tipo, ids)
    
    
def _reset_cc_by_ids(tipo: str, ids: list[int]) -> int:
    """
    Reset â€œduroâ€ por IDs: borra movimientos de CC en tablas locales.
    Descubre las tablas automÃ¡ticamente.
    Devuelve cuÃ¡ntas TABLAS fueron modificadas.
    """
    if not ids:
        return 0

    touched_tables = 0
    conn = None
    try:
        conn = db.get_conn()
        cur = conn.cursor()

        # --- descubrir tablas existentes ---
        alltabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        low = [t.lower() for t in alltabs]

        if tipo == "clientes":
            existing = [t for t in alltabs
                        if t.lower().startswith("cc_")
                        and ("cli" in t.lower() or "cliente" in t.lower())]
        else:  # proveedores
            existing = [t for t in alltabs
                        if t.lower().startswith("cc_")
                        and ("prov" in t.lower() or "prove" in t.lower())]

        if not existing:
            print("RESET CC: no hay tablas CC locales para", tipo, " â€” vistas en DB:", alltabs)
            return 0

        def table_cols(name: str) -> list[str]:
            try:
                return [r[1] for r in cur.execute(f"PRAGMA table_info({name})")]
            except Exception:
                return []

        def guess_id_col(name: str) -> str | None:
            cols = table_cols(name)
            prefer = [
                "cliente_id", "proveedor_id",
                "entidad_id", "ent_id", "id_entidad",
                "id_cliente", "id_proveedor",
                "cliente", "proveedor", "ent",
                "id_cli", "id_prov"
            ]
            for c in prefer:
                if c in cols:
                    return c
            # fallback por prefijo
            lower = [c.lower() for c in cols]
            for i, c in enumerate(lower):
                if tipo == "clientes" and (c.startswith("cliente") or c.startswith("cli") or c.startswith("ent")):
                    return cols[i]
                if tipo != "clientes" and (c.startswith("proveedor") or c.startswith("prov") or c.startswith("ent")):
                    return cols[i]
            return None

        placeholders = ",".join("?" * len(ids))

        # DEBUG
        print("DEBUG RESET tipo=", tipo, "ids=", ids)
        print("DEBUG Tablas CC detectadas:", existing)

        for t in existing:
            idcol = guess_id_col(t)
            print(f"DEBUG {t}: idcol={idcol}, cols={table_cols(t)}")
            if not idcol:
                continue
            try:
                cnt = cur.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE {idcol} IN ({placeholders})",
                    ids
                ).fetchone()[0]
                print(f"DEBUG rows matching in {t}: {cnt}")
                if cnt > 0:
                    cur.execute(
                        f"DELETE FROM {t} WHERE {idcol} IN ({placeholders})",
                        ids
                    )
                    touched_tables += 1
                    print(f"RESET CC: {t} borradas {cnt} filas")
            except Exception as ex:
                print(f"RESET CC: fallo al borrar en {t}: {ex}")

        conn.commit()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    return touched_tables
    
    
# ----- Guard ÃšNICO -----
if __name__ == "__main__":
    import traceback

    try:
        app = App()
        app.mainloop()
    except Exception as e:
        try:
            with open("app_error.log", "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:
            pass
        messagebox.showerror(
            "App",
            "Error fatal:\n" + str(e) + "\n\nSe guardÃ³ el detalle en app_error.log",
        )











