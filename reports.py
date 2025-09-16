# app.py — Gestión Textil (integrado v2 con tu backend)
# ---------------------------------------------------------------------------------
# Integra tu db_access.py y reports.py (tal como los pasaste)
# Cumple:
#  - Clientes/Proveedores: listar completo, alta, edición (doble click), borrado
#  - Caja: lista completa, alta MANUAL, borrado sólo si es manual (sin origen)
#  - Cheques: lista (en cartera primero + por fecha), alta manual, baja/actualizar estado
#  - CC Clientes/Proveedores: combobox de entidad, solapas Cuenta 1/2, saldo por cuenta y total,
#    alta de movimiento (diálogo), borrado con confirmación (usa funciones *cascada* del backend)
#  - Saldos CC: clientes / proveedores con no-cero + totales al pie
#  - Menú Importar: CSV para CC clientes/proveedores (Cta1 o Cta2)
#  - Comprobantes: Recibo / Orden de Pago con numeración correlativa (usa next_num) y PDF (reports)
#  - Backup: copia física del archivo gestion_textil.db (usa db_access.DB_PATH)
# ---------------------------------------------------------------------------------

import os
import csv
from datetime import date

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import db_access as db
import reports

APP_TITLE = "Gestión Textil — Operativa"

try:
    from db_access import DB_PATH
except Exception:
    from pathlib import Path

    DB_PATH = Path("gestion_textil.db")

LOGO_FILE = os.path.join(os.getcwd(), "logo.png")

# -------------------------------- utils --------------------------------


def today_str():
    return date.today().strftime("%Y-%m-%d")


def _money(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "0.00"


# ------------------------------ diálogos -------------------------------
class ClienteDialog(tk.Toplevel):
    """Alta/Edición de Cliente con el orden de columnas que espera tu backend."""

    FIELDS = [
        ("tipo", "Tipo"),
        ("razon_social", "Razón social *"),
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

    def __init__(self, master, title, initial_tuple=None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.vars = {}
        # Si viene tupla de obtener_cliente: id,tipo,razon_social,... (orden explícito)
        init_map = {}
        if isinstance(initial_tuple, (list, tuple)) and len(initial_tuple) >= 17:
            # indices 1..16 son los campos
            keys = [k for k, _ in self.FIELDS]
            for i, k in enumerate(keys, start=1):
                init_map[k] = initial_tuple[i]
        for i, (k, label) in enumerate(self.FIELDS):
            ttk.Label(frm, text=label + ":").grid(
                row=i, column=0, sticky="e", padx=5, pady=3
            )
            v = tk.StringVar(value=str(init_map.get(k, "") or ""))
            self.vars[k] = v
            ttk.Entry(frm, textvariable=v, width=42).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
        btns = ttk.Frame(frm)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        rs = (self.vars["razon_social"].get() or "").strip()
        if not rs:
            messagebox.showwarning("Validación", "La Razón social es obligatoria.")
            return
        data = tuple(self.vars[k].get().strip() for k, _ in self.FIELDS)
        self.result = data
        self.destroy()


class ProveedorDialog(ClienteDialog):
    pass


class CajaDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Nuevo movimiento de Caja (manual)")
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.vars = {
            "fecha": tk.StringVar(value=today_str()),
            "tipo": tk.StringVar(value="ingreso"),
            "medio": tk.StringVar(value="efectivo"),
            "concepto": tk.StringVar(value=""),
            "detalle": tk.StringVar(value=""),
            "monto": tk.StringVar(value="0"),
            "tercero_tipo": tk.StringVar(value=""),  # cliente | proveedor | (vacío)
            "tercero_id": tk.StringVar(value=""),
            "cuenta": tk.StringVar(value="1"),  # 1 | 2
        }
        rows = [
            ("fecha", "Fecha (YYYY-MM-DD)"),
            ("tipo", "Tipo (ingreso/egreso)"),
            ("medio", "Medio"),
            ("concepto", "Concepto"),
            ("detalle", "Detalle"),
            ("monto", "Monto"),
            ("tercero_tipo", "Tercero (cliente/proveedor)"),
            ("tercero_id", "ID tercero (opcional)"),
            ("cuenta", "Cuenta (1/2)"),
        ]
        for i, (k, lab) in enumerate(rows):
            ttk.Label(frm, text=lab + ":").grid(
                row=i, column=0, sticky="e", padx=5, pady=3
            )
            ttk.Entry(frm, textvariable=self.vars[k], width=30).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
        btns = ttk.Frame(frm)
        btns.grid(row=len(rows), column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        try:
            monto = float(self.vars["monto"].get().replace(",", "."))
        except Exception:
            messagebox.showwarning("Validación", "Monto inválido.")
            return
        out = {k: v.get().strip() for k, v in self.vars.items()}
        out["monto"] = monto
        self.result = out
        self.destroy()


class ChequeDialog(tk.Toplevel):
    def __init__(self, master, title="Nuevo Cheque (manual)"):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.vars = {
            "numero": tk.StringVar(value=""),
            "banco": tk.StringVar(value=""),
            "importe": tk.StringVar(value="0"),
            "fecha_recibido": tk.StringVar(value=today_str()),
            "fecha_cobro": tk.StringVar(value=today_str()),
            "cliente_id": tk.StringVar(value=""),
            "firmante_nombre": tk.StringVar(value=""),
            "firmante_cuit": tk.StringVar(value=""),
            "estado": tk.StringVar(value="en_cartera"),
            "fecha_estado": tk.StringVar(value=today_str()),
            "obs": tk.StringVar(value=""),
            "mov_caja_id": tk.StringVar(value=""),
            "proveedor_id": tk.StringVar(value=""),
            "cuenta_banco": tk.StringVar(value=""),
            "gastos_bancarios": tk.StringVar(value="0"),
            "cuenta": tk.StringVar(value="1"),
        }
        order = [
            ("numero", "Nº Cheque"),
            ("banco", "Banco"),
            ("importe", "Importe"),
            ("fecha_recibido", "Fecha Recibido"),
            ("fecha_cobro", "Fecha Pago"),
            ("cliente_id", "Cliente ID (opcional)"),
            ("firmante_nombre", "Firmante"),
            ("firmante_cuit", "CUIT Firmante"),
            ("estado", "Estado"),
            ("fecha_estado", "Fecha Estado"),
            ("obs", "Obs"),
            ("proveedor_id", "Proveedor ID (si endosa)"),
            ("cuenta_banco", "Cuenta Banco (si dep.)"),
            ("gastos_bancarios", "Gastos Bancarios"),
            ("cuenta", "Cuenta (1/2)"),
        ]
        for i, (k, lab) in enumerate(order):
            ttk.Label(frm, text=lab + ":").grid(
                row=i, column=0, sticky="e", padx=5, pady=3
            )
            ttk.Entry(frm, textvariable=self.vars[k], width=34).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
        btns = ttk.Frame(frm)
        btns.grid(row=len(order), column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        try:
            imp = float(self.vars["importe"].get().replace(",", "."))
            gb = float(self.vars["gastos_bancarios"].get().replace(",", "."))
        except Exception:
            messagebox.showwarning("Validación", "Importes inválidos.")
            return
        out = {k: v.get().strip() for k, v in self.vars.items()}
        out["importe"], out["gastos_bancarios"] = imp, gb
        self.result = out
        self.destroy()


class CCDialog(tk.Toplevel):
    def __init__(self, master, tipo: str, cuenta_flag: str, entidad_nombre: str):
        super().__init__(master)
        self.title(f"Nuevo mov CC {tipo} — {cuenta_flag} — {entidad_nombre}")
        self.resizable(False, False)
        self.result = None
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.vars = {
            "fecha": tk.StringVar(value=today_str()),
            "doc": tk.StringVar(value="MOV"),
            "numero": tk.StringVar(value=""),
            "concepto": tk.StringVar(value=""),
            "medio": tk.StringVar(value="otros"),
            "debe": tk.StringVar(value="0"),
            "haber": tk.StringVar(value="0"),
            "obs": tk.StringVar(value=""),
        }
        order = [
            ("fecha", "Fecha"),
            ("doc", "Doc (REC/OP/MOV)"),
            ("numero", "Número"),
            ("concepto", "Concepto"),
            ("medio", "Medio"),
            ("debe", "Debe"),
            ("haber", "Haber"),
            ("obs", "Obs"),
        ]
        for i, (k, lab) in enumerate(order):
            ttk.Label(frm, text=lab + ":").grid(
                row=i, column=0, sticky="e", padx=5, pady=3
            )
            ttk.Entry(frm, textvariable=self.vars[k], width=36).grid(
                row=i, column=1, sticky="w", padx=5, pady=3
            )
        btns = ttk.Frame(frm)
        btns.grid(row=len(order), column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(
            side="right", padx=6
        )
        ttk.Button(btns, text="Guardar", command=self._ok).pack(side="right")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _ok(self):
        try:
            debe = float(self.vars["debe"].get().replace(",", "."))
            haber = float(self.vars["haber"].get().replace(",", "."))
        except Exception:
            messagebox.showwarning("Validación", "Importes inválidos.")
            return
        out = {k: v.get().strip() for k, v in self.vars.items()}
        out["debe"], out["haber"] = debe, haber
        self.result = out
        self.destroy()


# ------------------------------ pestañas -------------------------------
class BaseTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app


class ClientesTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(top, text="Nuevo", command=self.new).pack(side="left")
        ttk.Button(top, text="Borrar", command=self.delete_selected).pack(
            side="left", padx=6
        )
        self.tree = ttk.Treeview(
            self,
            show="headings",
            height=18,
            columns=(
                "id",
                "razon_social",
                "cuit_dni",
                "tel1",
                "email",
                "localidad",
                "estado",
            ),
        )
        for c in (
            "id",
            "razon_social",
            "cuit_dni",
            "tel1",
            "email",
            "localidad",
            "estado",
        ):
            self.tree.heading(c, text=c)
            self.tree.column(
                c, width=140 if c != "razon_social" else 240, anchor="center"
            )
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", self._edit)
        self.reload()

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_clientes()
        for r in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    r[0],
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
        dlg = ClienteDialog(self, "Editar Cliente", row)
        self.wait_window(dlg)
        if dlg.result:
            db.editar_cliente(cid, dlg.result)
            self.reload()
            self.app.status.set("Cliente actualizado.")

    def new(self):
        dlg = ClienteDialog(self, "Nuevo Cliente")
        self.wait_window(dlg)
        if dlg.result:
            db.agregar_cliente(dlg.result)
            self.reload()
            self.app.status.set("Cliente agregado.")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        cid, rs = self.tree.item(it, "values")[0], self.tree.item(it, "values")[1]
        if messagebox.askyesno("Confirmar", f"¿Eliminar cliente {rs}?"):
            db.borrar_cliente(int(cid))
            self.reload()
            self.app.status.set("Cliente eliminado.")


class ProveedoresTab(ClientesTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.tree.heading("razon_social", text="Razón social")
        self.reload()

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_proveedores()
        for r in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    r[0],
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
            self.reload()
            self.app.status.set("Proveedor actualizado.")

    def new(self):
        dlg = ProveedorDialog(self, "Nuevo Proveedor")
        self.wait_window(dlg)
        if dlg.result:
            db.agregar_proveedor(dlg.result)
            self.reload()
            self.app.status.set("Proveedor agregado.")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        pid, rs = self.tree.item(it, "values")[0], self.tree.item(it, "values")[1]
        if messagebox.askyesno("Confirmar", f"¿Eliminar proveedor {rs}?"):
            db.borrar_proveedor(int(pid))
            self.reload()
            self.app.status.set("Proveedor eliminado.")


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
        cols = (
            "id",
            "fecha",
            "tipo",
            "medio",
            "concepto",
            "detalle",
            "monto",
            "tercero",
            "estado",
            "origen",
            "cuenta",
        )
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(
                c,
                width=120 if c not in ("detalle", "concepto") else 240,
                anchor="center",
            )
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.reload()

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_movimientos()
        total = 0.0
        for m in rows:
            tercero = ""
            if (m[7] or "") == "cliente" and m[8]:
                tercero = f"{m[8]} - "
            elif (m[7] or "") == "proveedor" and m[8]:
                tercero = f"{m[8]} - "
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
                    m[3] or "",
                    m[4] or "",
                    m[5] or "",
                    _money(m[6]),
                    tercero,
                    m[9] or "",
                    origen,
                    m[14] or "",
                ),
            )
        self.lbl_total.config(text=f"Saldo total: {_money(total)}")

    def new(self):
        dlg = CajaDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result
        mov_id = db.caja_agregar(
            fecha=r["fecha"],
            tipo=r["tipo"],
            medio=r["medio"],
            concepto=r["concepto"],
            detalle=r["detalle"],
            monto=r["monto"],
            tercero_tipo=(r["tercero_tipo"] or None),
            tercero_id=(int(r["tercero_id"]) if r["tercero_id"] else None),
            cuenta=r["cuenta"],
        )
        self.reload()
        self.app.status.set(f"Movimiento creado (ID {mov_id}).")

    def delete_selected(self):
        it = self.tree.focus()
        if not it:
            return
        vals = self.tree.item(it, "values")
        mov_id = int(vals[0])
        # Sólo permitir borrar si NO tiene origen (manual)
        # En listar_movimientos, origen_tipo está en index 10 y origen_id en 11
        # Para verificar, volvemos a leer una fila concreta desde db (opcional). Aquí usamos las columnas de la grilla: 'origen'
        origen_txt = vals[9]
        if origen_txt:
            messagebox.showinfo(
                "Caja", "Sólo se pueden borrar movimientos MANUALES (sin origen)."
            )
            return
        if messagebox.askyesno(
            "Confirmar",
            f"¿Eliminar movimiento {mov_id}? Esto desvinculará CC y Cheques si los hubiera.",
        ):
            db.borrar_mov_caja(mov_id)
            self.reload()
            self.app.status.set("Movimiento borrado.")


class ChequesTab(BaseTab):
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
            top, text="Sólo en cartera", variable=self.var_solo, command=self.reload
        ).pack(side="left", padx=8)
        cols = (
            "id",
            "numero",
            "banco",
            "importe",
            "recibido",
            "cobro",
            "cliente_id",
            "estado",
            "proveedor_id",
            "cuenta",
        )
        self.tree = ttk.Treeview(self, show="headings", height=18, columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(
                c, width=120 if c not in ("numero",) else 160, anchor="center"
            )
        self.tree.column("id", width=60)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.reload()

    def reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows = db.listar_cheques()
        if self.var_solo.get():
            rows = [r for r in rows if (r[9] or "").lower() == "en_cartera"]
        # Orden ya viene: en cartera primero y por fecha de cobro ascendente
        for ch in rows:
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
                    ch[6] or "",
                    ch[9] or "",
                    ch[13] or "",
                    ch[16] or "",
                ),
            )

    def new(self):
        dlg = ChequeDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result
        data = {
            "numero": r["numero"],
            "banco": r["banco"],
            "importe": r["importe"],
            "fecha_recibido": r["fecha_recibido"],
            "fecha_cobro": r["fecha_cobro"],
            "cliente_id": (int(r["cliente_id"]) if r["cliente_id"] else None),
            "firmante_nombre": r["firmante_nombre"],
            "firmante_cuit": r["firmante_cuit"],
            "estado": r["estado"],
            "fecha_estado": r["fecha_estado"],
            "obs": r["obs"],
            "mov_caja_id": None,
            "proveedor_id": (int(r["proveedor_id"]) if r["proveedor_id"] else None),
            "cuenta_banco": r["cuenta_banco"],
            "gastos_bancarios": r["gastos_bancarios"],
            "cuenta": r["cuenta"],
        }
        db.agregar_cheque(data)
        self.reload()
        self.app.status.set("Cheque agregado.")

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
            # pedir proveedor id
            pid = simpledialog.askstring("Endoso", "ID proveedor receptor del cheque:")
            if pid and pid.isdigit():
                proveedor_id = int(pid)
        elif estado == "depositado":
            cuenta_banco = simpledialog.askstring(
                "Depósito", "Cuenta bancaria (alias/nro):"
            )
            try:
                gastos = float(
                    simpledialog.askstring("Depósito", "Gastos bancarios (opcional):")
                    or 0
                )
            except Exception:
                gastos = None
        if messagebox.askyesno("Confirmar", f"Actualizar cheque {cid} → {estado}?"):
            db.actualizar_estado_cheque(
                cid, estado, today_str(), proveedor_id, cuenta_banco, gastos
            )
            self.reload()
            self.app.status.set("Cheque actualizado.")


class CCTab(BaseTab):
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
        self.lbl = ttk.Label(top, text="Saldos C1=0.00  C2=0.00  Total=0.00")
        self.lbl.pack(side="right")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.grid1 = self._mk_grid(self.nb, "Cuenta 1")
        self.grid2 = self._mk_grid(self.nb, "Cuenta 2")
        self._load_entidades()
        self.reload()

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
        else:
            rows = db.listar_proveedores_id_nombre()
        self.ents = [(r[0], r[1]) for r in rows]
        self.cbo["values"] = [f"{i} — {n}" for i, n in self.ents]
        if self.ents:
            self.cbo.current(0)

    def _current_ent(self):
        if not self.ents or not self.cbo.get():
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

    def add_mov(self):
        ent = self._current_ent()
        if not ent:
            return
        ent_id, ent_name = ent
        cuenta_flag = "cuenta1" if self.nb.select() == self.nb.tabs()[0] else "cuenta2"
        dlg = CCDialog(self, self.tipo, cuenta_flag, ent_name)
        self.wait_window(dlg)
        if not dlg.result:
            return
        r = dlg.result
        if self.tipo == "clientes":
            db.cc_cli_agregar_mov(
                cuenta_flag,
                r["fecha"],
                ent_id,
                r["doc"],
                r["numero"],
                r["concepto"],
                r["medio"],
                r["debe"],
                r["haber"],
                None,
                None,
                r["obs"],
            )
        else:
            db.cc_prov_agregar_mov(
                cuenta_flag,
                r["fecha"],
                ent_id,
                r["doc"],
                r["numero"],
                r["concepto"],
                r["medio"],
                r["debe"],
                r["haber"],
                None,
                None,
                r["obs"],
            )
        self.reload()
        self.app.status.set("Movimiento agregado.")

    def del_mov(self):
        ent = self._current_ent()
        if not ent:
            return
        cuenta_flag = "cuenta1" if self.nb.select() == self.nb.tabs()[0] else "cuenta2"
        tv = self.grid1 if cuenta_flag == "cuenta1" else self.grid2
        it = tv.focus()
        if not it:
            return
        mov_id = int(tv.item(it, "values")[0])
        if not messagebox.askyesno(
            "Confirmar",
            "¿Eliminar movimiento? Si está vinculado puede borrar también en Caja/Cheques.",
        ):
            return
        if self.tipo == "clientes":
            db.cc_cli_borrar_cascada(cuenta_flag, mov_id)
        else:
            db.cc_prov_borrar_cascada(cuenta_flag, mov_id)
        self.reload()
        self.app.status.set("Movimiento eliminado.")


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
        self.lbl = ttk.Label(self, text="Totales — C1=0.00  C2=0.00")
        self.lbl.pack(anchor="e", padx=12, pady=(0, 8))
        self.reload()

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
        self.lbl.config(text=f"Totales — C1={_money(t1)}  C2={_money(t2)}")


# ------------------------------ aplicación -----------------------------
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

        # Menú
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
        ttk.Label(tb, text="Gestión Textil — Operativa", style="Title.TLabel").pack(
            side="left"
        )

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.tab_cli = ClientesTab(self.nb, self)
        self.nb.add(self.tab_cli, text="Clientes")
        self.tab_prv = ProveedoresTab(self.nb, self)
        self.nb.add(self.tab_prv, text="Proveedores")
        self.tab_caj = CajaTab(self.nb, self)
        self.nb.add(self.tab_caj, text="Caja")
        self.tab_chq = ChequesTab(self.nb, self)
        self.nb.add(self.tab_chq, text="Cheques")
        self.tab_ccc = CCTab(self.nb, self, "clientes")
        self.nb.add(self.tab_ccc, text="CC Clientes")
        self.tab_ccp = CCTab(self.nb, self, "proveedores")
        self.nb.add(self.tab_ccp, text="CC Proveedores")
        self.tab_scc = SaldosTab(self.nb, self, "clientes")
        self.nb.add(self.tab_scc, text="Saldos CC")
        self.tab_scp = SaldosTab(self.nb, self, "proveedores")
        self.nb.add(self.tab_scp, text="Saldos Proveedores")

        # Status
        self.status = ttk.Label(self, text="Listo.")
        self.status.pack(fill="x", padx=8, pady=4)

    # ----------------------------- Menú -----------------------------
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        m_arch = tk.Menu(m, tearoff=0)
        m_arch.add_command(label="Backup BD…", command=self._backup)
        m_arch.add_separator()
        m_arch.add_command(label="Salir", command=self.destroy)
        m.add_cascade(label="Archivo", menu=m_arch)

        m_imp = tk.Menu(m, tearoff=0)
        m_imp.add_command(
            label="Importar CC Clientes — Cuenta 1 (CSV)",
            command=lambda: self._import_cc("clientes", 1),
        )
        m_imp.add_command(
            label="Importar CC Clientes — Cuenta 2 (CSV)",
            command=lambda: self._import_cc("clientes", 2),
        )
        m_imp.add_separator()
        m_imp.add_command(
            label="Importar CC Proveedores — Cuenta 1 (CSV)",
            command=lambda: self._import_cc("proveedores", 1),
        )
        m_imp.add_command(
            label="Importar CC Proveedores — Cuenta 2 (CSV)",
            command=lambda: self._import_cc("proveedores", 2),
        )
        m.add_cascade(label="Importar", menu=m_imp)

        m_comp = tk.Menu(m, tearoff=0)
        m_comp.add_command(
            label="Emitir Recibo (Clientes)", command=self._emitir_recibo
        )
        m_comp.add_command(
            label="Emitir Orden de Pago (Proveedores)", command=self._emitir_op
        )
        m.add_cascade(label="Comprobantes", menu=m_comp)

        m_ver = tk.Menu(m, tearoff=0)
        m_ver.add_command(label="Refrescar pestaña", command=self._refresh_tab)
        m.add_cascade(label="Ver", menu=m_ver)

    # --------------------------- Acciones ---------------------------
    def _refresh_tab(self):
        cur = self.nb.select()
        if not cur:
            return
        page = self.nb.nametowidget(cur)
        if hasattr(page, "reload"):
            page.reload()
        self.status.config(text="Pestaña recargada.")

    def _backup(self):
        folder = filedialog.askdirectory(title="Elegí una carpeta de destino")
        if not folder:
            return
        import shutil
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(folder, f"backup_{ts}_" + os.path.basename(str(DB_PATH)))
        shutil.copyfile(str(DB_PATH), dest)
        messagebox.showinfo("Backup", f"Backup creado:\n{dest}")

    def _import_cc(self, tipo: str, cuenta: int):
        path = filedialog.askopenfilename(
            title="Elegí CSV", filetypes=[("CSV", "*.csv"), ("Todos", "*.*")]
        )
        if not path:
            return
        default_ent = None
        # ¿tiene columna id? si no, pedimos elegir una entidad para imputar
        ask = messagebox.askyesno(
            "Importación",
            "¿El CSV tiene columna 'cliente_id'/'proveedor_id'? Si NO, te pediré elegir la entidad destino.",
        )
        if not ask:
            if tipo == "clientes":
                opts = db.listar_clientes_id_nombre()
            else:
                opts = db.listar_proveedores_id_nombre()
            if not opts:
                messagebox.showwarning("Importar", "No hay entidades cargadas.")
                return
            mapping = {f"{i} — {n}": i for i, n in opts}
            pick = simpledialog.askstring(
                "Entidad",
                "Escribí una opción exacta:\n" + "\n".join(list(mapping.keys())[:30]),
            )
            if not pick or pick not in mapping:
                return
            default_ent = mapping[pick]
        ok = err = 0
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    fecha = r.get("fecha") or today_str()
                    doc = r.get("doc") or "MOV"
                    numero = r.get("numero") or ""
                    concepto = r.get("concepto") or ""
                    medio = r.get("medio") or "otros"
                    debe = float(r.get("debe", 0) or 0)
                    haber = float(r.get("haber", 0) or 0)
                    if tipo == "clientes":
                        ent = int(r.get("cliente_id") or (default_ent or 0))
                        db.cc_cli_agregar_mov(
                            f"cuenta{cuenta}",
                            fecha,
                            ent,
                            doc,
                            numero,
                            concepto,
                            medio,
                            debe,
                            haber,
                            None,
                            None,
                            r.get("obs"),
                        )
                    else:
                        ent = int(r.get("proveedor_id") or (default_ent or 0))
                        db.cc_prov_agregar_mov(
                            f"cuenta{cuenta}",
                            fecha,
                            ent,
                            doc,
                            numero,
                            concepto,
                            medio,
                            debe,
                            haber,
                            None,
                            None,
                            r.get("obs"),
                        )
                    ok += 1
                except Exception:
                    err += 1
        self.tab_ccc.reload()
        self.tab_ccp.reload()
        self.tab_scc.reload()
        self.tab_scp.reload()
        messagebox.showinfo("Importar", f"Importados: {ok}\nErrores: {err}")

    def _emitir_recibo(self):
        # usa entidad seleccionada en CC Clientes
        ent = self.tab_ccc._current_ent()
        if not ent:
            messagebox.showinfo("Recibo", "Elegí un cliente en la pestaña CC Clientes.")
            return
        cid, name = ent
        numero = db.next_num("recibo")
        importe = simpledialog.askfloat("Recibo", f"Importe a imputar a {name}:")
        if not importe:
            return
        concepto = (
            simpledialog.askstring(
                "Recibo", "Concepto:", initialvalue="Pago cuenta corriente"
            )
            or "Pago cuenta corriente"
        )
        medio = (
            simpledialog.askstring("Recibo", "Medio de pago:", initialvalue="efectivo")
            or "efectivo"
        )
        # PDF
        cli = db.obtener_cliente(cid)
        cliente_dict = {
            "rs": cli[2] or "",
            "cuit": cli[4] or "",
            "dir": f"{(cli[10] or '')} {(cli[11] or '')}, {(cli[13] or '')}",
        }
        out = filedialog.asksaveasfilename(
            title="Guardar Recibo",
            defaultextension=".pdf",
            initialfile=f"REC_{numero}.pdf",
        )
        if not out:
            return
        reports.pdf_recibo(
            out,
            numero,
            today_str(),
            cliente_dict,
            concepto,
            medio,
            importe,
            cheques=None,
        )
        # CC + Caja (haber en clientes -> ingreso)
        db.cc_cli_agregar_con_caja(
            "cuenta1",
            today_str(),
            cid,
            "REC",
            numero,
            concepto,
            medio,
            0.0,
            float(importe),
            cuenta_caja=1,
            cheque_id=None,
            obs=None,
        )
        self.tab_ccc.reload()
        self.tab_scc.reload()
        self.tab_caj.reload()
        messagebox.showinfo(
            "Recibo", f"Recibo {numero} generado y movimientos registrados."
        )

    def _emitir_op(self):
        ent = self.tab_ccp._current_ent()
        if not ent:
            messagebox.showinfo(
                "OP", "Elegí un proveedor en la pestaña CC Proveedores."
            )
            return
        pid, name = ent
        numero = db.next_num("op")
        importe = simpledialog.askfloat("Orden de Pago", f"Importe a pagar a {name}:")
        if not importe:
            return
        concepto = (
            simpledialog.askstring(
                "Orden de Pago", "Concepto:", initialvalue="Pago proveedores"
            )
            or "Pago proveedores"
        )
        medio = (
            simpledialog.askstring(
                "Orden de Pago", "Medio de pago:", initialvalue="efectivo"
            )
            or "efectivo"
        )
        prv = db.obtener_proveedor(pid)
        prov_dict = {
            "rs": prv[2] or "",
            "cuit": prv[4] or "",
            "dir": f"{(prv[10] or '')} {(prv[11] or '')}, {(prv[13] or '')}",
        }
        out = filedialog.asksaveasfilename(
            title="Guardar Orden de Pago",
            defaultextension=".pdf",
            initialfile=f"OP_{numero}.pdf",
        )
        if not out:
            return
        reports.pdf_orden_pago(
            out, numero, today_str(), prov_dict, concepto, medio, importe, cheques=None
        )
        # CC + Caja (haber en proveedores -> egreso)
        db.cc_prov_agregar_con_caja(
            "cuenta1",
            today_str(),
            pid,
            "OP",
            numero,
            concepto,
            medio,
            0.0,
            float(importe),
            cuenta_caja=1,
            cheque_id=None,
            obs=None,
        )
        self.tab_ccp.reload()
        self.tab_scp.reload()
        self.tab_caj.reload()
        messagebox.showinfo(
            "Orden de Pago", f"OP {numero} generada y movimientos registrados."
        )


if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except Exception as e:
        messagebox.showerror("App", f"Error fatal:\n{e}")
