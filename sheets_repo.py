import time
import unicodedata
from datetime import datetime
from typing import List, Dict, Any, Optional

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.readonly'
          ]

# ---------------- Utilidades generales ----------------

class TTLCache:
    """Cache en memoria con TTL simple para reducir lecturas al Sheet."""
    def __init__(self, ttl_seconds=300):
        self.ttl = ttl_seconds
        self.store = {}

    def get(self, key):
        now = time.time()
        item = self.store.get(key)
        if not item:
            return None
        value, ts = item
        if now - ts > self.ttl:
            self.store.pop(key, None)
            return None
        return value

    def set(self, key, value):
        self.store[key] = (value, time.time())

def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def _norm(s: str) -> str:
    """minúsculas, sin tildes, sin dobles espacios."""
    s = _strip_accents((s or '').strip().lower())
    s = ' '.join(s.split())
    return s

def _as_int(x, default=1) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default

def _as_float(x) -> Optional[float]:
    """Convierte strings tipo '21.999' o '21,999' a float; devuelve None si no se puede."""
    if x is None:
        return None
    s = str(x).strip()
    if s == '':
        return None
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None

def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def _as_str(x) -> str:
    """Convierte a cadena de texto, manejando valores None."""
    return '' if x is None else str(x)

# ---------------- Repositorio Sheets ----------------

class SheetsRepo:
    """Capa de acceso a Google Sheets para usuarios, productos, pedidos e items."""

    # Canónicos para escritura (sin acentos para evitar conflictos)
    ORDERS_HEADERS_CANON = ["order_id", "user_id", "fecha", "campana", "estado", "nombre_asesora", "total_items"]
    ITEMS_HEADERS_CANON  = ["user_id", "order_id", "line", "codigo", "descripcion", "Talla", "cantidad"]

    # Lo que leen tus templates / app
    ORDERS_HEADERS_OUT = ["order_id", "user_id", "fecha", "campaña", "estado", "Nombre asesora", "total_items"]
    ITEMS_HEADERS_OUT  = ["user_id", "order_id", "line", "Código", "descripcion", "Talla", "cantidad"]

    def __init__(self, creds_json: dict, spreadsheet_id: str,
                 ids_sheet='ID', products_sheet='Data',
                 orders_sheet='Pedidos', items_sheet='Items',
                 docs_sheet='Docs'):
        creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
        client = gspread.authorize(creds)
        self.spreadsheet = client.open_by_key(spreadsheet_id)

        self.cache = TTLCache(ttl_seconds=180)

        self.ids_ws      = self.spreadsheet.worksheet(ids_sheet)
        self.products_ws = self.spreadsheet.worksheet(products_sheet)
        self.orders_ws   = self.spreadsheet.worksheet(orders_sheet)
        self.items_ws    = self.spreadsheet.worksheet(items_sheet)
        try:
            self.docs_ws = self.spreadsheet.worksheet(docs_sheet)
        except Exception:
            self.docs_ws = None

    # --------------- helpers de lectura/escritura ---------------

    def _records(self, ws) -> List[Dict[str, Any]]:
        vals = ws.get_all_values()
        if not vals:
            return []
        headers = vals[0]
        out = []
        for row in vals[1:]:
            rec = {}
            for i, h in enumerate(headers):
                rec[h] = row[i] if i < len(row) else ''
            out.append(rec)
        return out

    def _ensure_headers(self, ws, expected_canon: list):
        vals = ws.get_all_values()
        if not vals:
            ws.update('1:1', [expected_canon])
            return
        real = vals[0]
        if len(real) < len(expected_canon):
            ws.update('1:1', [expected_canon])

    def _read_rows_normalized(self, ws, output_headers: list) -> List[Dict[str, Any]]:
        vals = ws.get_all_values()
        if not vals:
            return []
        real_headers = vals[0]
        real_norm = [_norm(h) for h in real_headers]

        out = []
        pos = []
        for h in output_headers:
            n = _norm(h)
            try:
                pos.append(real_norm.index(n))
            except ValueError:
                pos.append(None)

        for row in vals[1:]:
            rec = {}
            for h, i in zip(output_headers, pos):
                rec[h] = row[i] if i is not None and i < len(row) else ''
            out.append(rec)
        return out

    # --------------- USUARIOS ---------------

    def get_user_by_id(self, user_id: str):
        rows = self._records(self.ids_ws)
        for r in rows:
            if str(r.get('ID') or r.get('id') or '').strip() == str(user_id).strip():
                return {
                    'ID': r.get('ID') or r.get('id'),
                    'nombre': r.get('nombre') or r.get('Nombre') or '',
                    'role': r.get('rol') or r.get('role') or 'asesora',
                    'password_hash': r.get('password_hash') or '',
                }
        return None

    def set_user_password_hash(self, user_id: str, new_hash: str) -> bool:
        """Actualiza C (password_hash) en la fila del ID dado."""
        ws = self.ids_ws
        rows = ws.get_all_values()
        if not rows:
            return False
        header = [h.strip().lower() for h in rows[0]]
        col_idx = {name: idx for idx, name in enumerate(header)}
        col_id = col_idx.get('id', 0)
        col_ph = col_idx.get('password_hash', 2)

        for i, row in enumerate(rows[1:], start=2):
            try:
                if _as_str(row[col_id]).strip() == _as_str(user_id).strip():
                    ws.update_cell(i, col_ph + 1, new_hash)
                    return True
            except IndexError:
                continue
        return False

    def list_advisors(self) -> List[Dict[str, str]]:
        """
        Devuelve [{value,label}] usando **exclusivamente** las columnas:
        F: 'Nombre completo' y G: 'Apellidos' de la hoja ID.
        Si no existen ambas cabeceras, devuelve [].
        """
        key = 'advisors:list:F+G'
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        vals = self.ids_ws.get_all_values()
        if not vals:
            return []
        headers = vals[0]
        norm_headers = [_norm(h) for h in headers]

        try:
            idx_nombre = norm_headers.index('nombre completo')
            idx_apells = norm_headers.index('apellidos')
        except ValueError:
            self.cache.set(key, [])
            return []

        out: List[Dict[str, str]] = []
        for row in vals[1:]:
            nombre = (row[idx_nombre] if idx_nombre < len(row) else '').strip()
            apells = (row[idx_apells] if idx_apells < len(row) else '').strip()
            full = f"{nombre} {apells}".strip()
            if full:
                out.append({'value': full, 'label': full})

        seen = set()
        uniq: List[Dict[str, str]] = []
        for it in out:
            if it['value'] in seen:
                continue
            seen.add(it['value'])
            uniq.append(it)

        self.cache.set(key, uniq)
        return uniq

    # --------------- PRODUCTOS (Data) ---------------

    def _load_products(self) -> List[Dict[str, Any]]:
        """
        Carga productos de la pestaña Data con tolerancia de encabezados.
        Incluye: Código, Texto breve material, Talla, Precio_Catalogo, Precio_Factura.
        """
        key = 'products:all'
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        rows = self._records(self.products_ws)
        prods: List[Dict[str, Any]] = []
        for r in rows:
            rn = {_norm(k): v for k, v in r.items()}
            codigo = rn.get('codigo') or r.get('Código') or r.get('Codigo') or r.get('Material') or ''
            desc   = rn.get('texto breve material') or r.get('Texto breve material') or ''
            talla  = rn.get('talla') or r.get('Talla') or ''
            pcat   = (rn.get('precio_catalogo') or rn.get('precio catalogo') or rn.get('precio catálogo')
                      or r.get('Precio_Catalogo') or r.get('Precio catalogo') or r.get('Precio Catálogo') or '')
            pfac   = (rn.get('precio_factura') or rn.get('precio factura')
                      or r.get('Precio_Factura') or r.get('Precio factura') or '')

            prods.append({
                'Código': str(codigo).strip(),
                'Texto breve material': str(desc).strip(),
                'Talla': str(talla).strip(),
                'Precio_Catalogo': str(pcat).strip(),
                'Precio_Factura': str(pfac).strip(),
            })

        self.cache.set(key, prods)
        return prods

    def search_products(self, q: str, limit=12):
        """Búsqueda simple por código o descripción (case-insensitive)."""
        if not q:
            return []
        ql = q.lower()
        out = []
        for p in self._load_products():
            if ql in p['Código'].lower() or ql in p['Texto breve material'].lower():
                out.append({
                    'code': p['Código'],
                    'label': f"{p['Código']} – {p['Texto breve material']} ({p['Talla']})"
                })
            if len(out) >= limit:
                break
        return out

    def get_product_by_code(self, code: str):
        """Obtiene un producto exacto por código; incluye precios si existen."""
        key = f'product:{code}'
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        for p in self._load_products():
            if p['Código'] == code:
                self.cache.set(key, p)
                return p
        self.cache.set(key, None)
        return None
    
    
    def _material_map(self) -> Dict[str, str]:
        
        key = "material:map"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        data_vals = self.products_ws.get_all_values()
        if not data_vals or len(data_vals) <= 1:
            self.cache.set(key, {})
            return {}

        header = data_vals[0]
        header_norm = [_norm(h) for h in header]

        try:
            codigo_col = header_norm.index(_norm("Código"))
        except ValueError:
            # fallback si la columna se llama distinto
            try:
                codigo_col = header_norm.index(_norm("codigo"))
            except ValueError:
                self.cache.set(key, {})
                return {}

        # Material puede llamarse Material o Texto breve material según tu Data
        material_col = None
        for cand in ["Material", "material"]:
            n = _norm(cand)
            if n in header_norm:
                material_col = header_norm.index(n)
                break

        if material_col is None:
            self.cache.set(key, {})
            return {}

        m = {}
        for row in data_vals[1:]:
            if len(row) > max(codigo_col, material_col):
                cod = str(row[codigo_col]).strip()
                mat = str(row[material_col]).strip()
                if cod:
                    m[cod] = mat

        self.cache.set(key, m)
        return m


    def _get_material_by_codigo(self, codigo: str) -> str:
        """Lookup en memoria. CERO lecturas a Sheets por ítem."""
        if not codigo:
            return ""
        return self._material_map().get(str(codigo).strip(), "")

    
    
    

    # --------------- PEDIDOS / ITEMS ---------------

    def _next_order_id(self) -> str:
        return datetime.now().strftime('P%Y%m%d%H%M%S')

    def create_order(self, user_id: str, campaign: str, advisor_name: str,
                     items: List[Dict[str, Any]]) -> str:
        """
        Crea un pedido (cabecera + detalle). Devuelve order_id.
        (6º campo ahora guarda 'Nombre asesora')
        """
        order_id = self._next_order_id()
        fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        estado = 'Nuevo'
        total_items = sum(_as_int(i.get('cantidad'), 1) for i in items)

        self._ensure_headers(self.orders_ws, self.ORDERS_HEADERS_CANON)
        self._ensure_headers(self.items_ws,  self.ITEMS_HEADERS_CANON)

        # Cabecera A..G (6º = nombre_asesora)
        header_row = [order_id, user_id, fecha, campaign, estado, advisor_name, total_items]
        self.orders_ws.append_row(header_row, value_input_option='USER_ENTERED', table_range='A1:G1')

        # Detalle A..M 
        line = 1
        rows = []
        for it in items:
            # ✅ EXTRAER VARIABLES
            codigo = str(it.get('code') or it.get('codigo') or '')
            descripcion = str(it.get('descripcion') or '')
            talla = str(it.get('Talla') or '')
            cantidad = _as_int(it.get('cantidad'), 1)
            
            material = self._get_material_by_codigo(codigo)
            
            rows.append([
                user_id,        # A: user_id
                order_id,       # B: order_id
                line,           # C: line
                codigo,         # D: Código
                descripcion,    # E: descripcion
                talla,          # F: Talla
                cantidad,       # G: cantidad
                '',             # H: stock disponible (VACÍO)
                cantidad,       # I: Cantidad pedida (= cantidad)
                '',             # J: cantidad_inventario (VACÍO)
                advisor_name,   # K: Nombre asesora
                '',             # L: Documento de ventas (VACÍO)
                material,       # M: Material AHORA SÍ ESTÁ DEFINIDO
                    
                
            ])
            line += 1
        if rows:
            self.items_ws.append_rows(rows, value_input_option='USER_ENTERED', table_range='A1:M1')

        return order_id

    def update_order(self, order_id: str, items: List[Dict[str, Any]]) -> bool:
        """
        Actualiza los ítems de un pedido existente.
        - Elimina todos los ítems viejos asociados a ese order_id
        - Inserta los nuevos ítems
        - Actualiza total_items en la cabecera del pedido
        """
        try:
            # Obtener la cabecera del pedido
            header = self.get_order_header(order_id)
            if not header:
                raise ValueError(f"Pedido {order_id} no encontrado")

            user_id = header.get('user_id', '')

            # ========== ELIMINAR ÍTEMS VIEJOS ==========
            items_vals = self.items_ws.get_all_values()
            if items_vals and len(items_vals) > 1:
                rows_to_delete = []
                for i in range(1, len(items_vals)):
                    row = items_vals[i]
                    # Columna B (índice 1) contiene order_id
                    if len(row) > 1 and row[1].strip() == order_id.strip():
                        rows_to_delete.append(i + 1)  # +1 porque gspread usa 1-based

                # Eliminar en orden inverso para evitar desplazamientos
                for row_num in reversed(rows_to_delete):
                    try:
                        self.items_ws.delete_rows(row_num)
                    except Exception as e:
                        print(f"Error deleting row {row_num}: {e}")

            # ========== INSERTAR NUEVOS ÍTEMS ==========
            if items:
                # ✅ Obtener nombre de asesora del header
                nombre_asesora = header.get('nombre_asesora') or header.get('Nombre asesora', '')
    
                line = 1
                new_rows = []
                for it in items:
                    codigo = str(it.get('Codigo') or it.get('codigo') or '').strip()
                    descripcion = str(it.get('descripcion') or '').strip()
                    talla = str(it.get('Talla') or '').strip()
                    cantidad = _as_int(it.get('cantidad'), 1)
                        
                      
                    material = self._get_material_by_codigo(codigo)
                    new_rows.append([
                        user_id,         # A: user_id
                        order_id,        # B: order_id
                        line,            # C: line (consecutivo)
                        codigo,          # D: Código
                        descripcion,     # E: descripcion
                        talla,           # F: Talla
                        cantidad,        # G: cantidad
                        '',              # H: stock disponible (VACÍO)
                        cantidad,        # I: Cantidad pedida (= cantidad) ✅
                        '',              # J: cantidad_inventario (VACÍO)
                        nombre_asesora,    # K: Nombre asesora ✅
                        '',              # L: Documento de ventas (VACÍO)
                        material,        # M: Material (buscado en DATA) ✅
                    ])
                    line += 1
                self.items_ws.append_rows(new_rows, value_input_option='USER_ENTERED',table_range='A1:M1')

            # ========== ACTUALIZAR TOTAL_ITEMS EN CABECERA ==========
            orders_vals = self.orders_ws.get_all_values()
            if orders_vals and len(orders_vals) > 1:
                total_items = sum(_as_int(i.get('cantidad'), 1) for i in items)
                
                for i in range(1, len(orders_vals)):
                    row = orders_vals[i]
                    # Columna A (índice 0) contiene order_id
                    if len(row) > 0 and row[0].strip() == order_id.strip():
                        # Columna G (índice 6) contiene total_items
                        try:
                            self.orders_ws.update_cell(i + 1, 7, total_items)  # +1 porque gspread usa 1-based
                        except Exception as e:
                            print(f"Error updating total_items: {e}")
                        break

            # Limpiar cache para que se recarguen los datos
            self.cache.store.clear()
            
            return True

        except Exception as e:
            print(f"Error updating order: {e}")
            import traceback
            traceback.print_exc()
            return False

    def list_orders(self, user_id: str, limit: Optional[int]=None):
        rows = self._read_rows_normalized(self.orders_ws, self.ORDERS_HEADERS_OUT)
        old_rows = self._read_rows_normalized(
            self.orders_ws,
            ["order_id", "user_id", "fecha", "campaña", "estado", "notes", "total_items"]
        ) if any(not r.get('Nombre asesora') for r in rows) else []

        out = []
        for r in rows:
            if str(r.get('user_id') or '').strip() == user_id:
                nombre_asesora = r.get('Nombre asesora', '')
                if not nombre_asesora and old_rows:
                    for orow in old_rows:
                        if orow.get('order_id') == r.get('order_id'):
                            nombre_asesora = orow.get('notes', '')
                            break
                out.append({
                    'order_id': r.get('order_id', ''),
                    'fecha': r.get('fecha', ''),
                    'campaña': r.get('campaña', '') or r.get('campana', ''),
                    'estado': r.get('estado', ''),
                    'total_items': r.get('total_items', ''),
                    'Nombre asesora': nombre_asesora,
                })
        out = sorted(out, key=lambda x: _parse_date(x.get('fecha')) or x.get('fecha', ''), reverse=True)
        return out[:limit] if limit else out 

    def list_orders_all(self, limit: Optional[int]=None):
        rows = self._read_rows_normalized(self.orders_ws, self.ORDERS_HEADERS_OUT)
        old_rows = self._read_rows_normalized(
            self.orders_ws,
            ["order_id", "user_id", "fecha", "campaña", "estado", "notes", "total_items"]
        ) if any(not r.get('Nombre asesora') for r in rows) else []

        out = []
        for r in rows:
            nombre_asesora = r.get('Nombre asesora', '')
            if not nombre_asesora and old_rows:
                for orow in old_rows:
                    if orow.get('order_id') == r.get('order_id'):
                        nombre_asesora = orow.get('notes', '')
                        break
            out.append({
                'order_id': r.get('order_id', ''),
                'user_id': r.get('user_id', ''),
                'fecha': r.get('fecha', ''),
                'campaña': r.get('campaña', '') or r.get('campana', ''),
                'estado': r.get('estado', ''),
                'total_items': r.get('total_items', ''),
                'Nombre asesora': nombre_asesora,
            })
        out = sorted(out, key=lambda x: _parse_date(x.get('fecha')) or x.get('fecha', ''), reverse=True)
        return out[:limit] if limit else out

    # --------------- DOCUMENTOS ---------------

    def list_documents(self, user_id: str):
        if not self.docs_ws:
            return []
        rows = self._records(self.docs_ws)
        docs = []
        for r in rows:
            if str(r.get('user_id') or r.get('ID') or r.get('asesora_id') or '').strip() == user_id:
                docs.append({
                    'nombre': r.get('nombre_archivo') or r.get('Nombre') or 'Documento',
                    'url': r.get('url') or r.get('enlace') or r.get('link') or ''
                })
        return docs

    def get_order_header(self, order_id: str):
        # Esquema nuevo con "Nombre asesora"
        rows_new = self._read_rows_normalized(self.orders_ws, self.ORDERS_HEADERS_OUT)
        # Histórico con "notes" para fallback y para mostrar "Notas" si existiera
        rows_old = self._read_rows_normalized(self.orders_ws,
                   ["order_id", "user_id", "fecha", "campaña", "estado", "notes", "total_items"])

        for r in rows_new:
            if str(r.get('order_id', '')).strip() == str(order_id).strip():
                nombre_asesora = r.get('Nombre asesora', '')
                notes_val = ''
                if not nombre_asesora or True:
                    for orow in rows_old:
                        if str(orow.get('order_id', '')).strip() == str(order_id).strip():
                            if not nombre_asesora:
                                nombre_asesora = orow.get('notes', '')
                            notes_val = orow.get('notes', '')
                            break
                return {
                    'order_id': r.get('order_id', ''),
                    'user_id': r.get('user_id', ''),
                    'fecha': r.get('fecha', ''),
                    'campaña': r.get('campaña', '') or r.get('campana', ''),
                    'estado': r.get('estado', ''),
                    'nombre_asesora': nombre_asesora,
                    'Nombre asesora': nombre_asesora,
                    'notes': notes_val,
                    'total_items': r.get('total_items', ''),
                }
        # Solo viejo:
        for r in rows_old:
            if str(r.get('order_id', '')).strip() == str(order_id).strip():
                return {
                    'order_id': r.get('order_id', ''),
                    'user_id': r.get('user_id', ''),
                    'fecha': r.get('fecha', ''),
                    'campaña': r.get('campaña', '') or r.get('campana', ''),
                    'estado': r.get('estado', ''),
                    'nombre_asesora': r.get('notes', ''),
                    'Nombre asesora': r.get('notes', ''),
                    'notes': r.get('notes', ''),
                    'total_items': r.get('total_items', ''),
                }
        return None

    def get_order_items(self, order_id: str):
        """
        Devuelve lista de ítems (detalle) para ese order_id.
        """
        rows = self._read_rows_normalized(self.items_ws, self.ITEMS_HEADERS_OUT)
        out = []
        for r in rows:
            if str(r.get('order_id', '')).strip() == str(order_id).strip():
                out.append({
                    'Codigo': r.get('Código', '') or r.get('codigo', ''),
                    'descripcion': r.get('descripcion', ''),
                    'Talla': r.get('Talla', ''),
                    'cantidad': _as_int(r.get('cantidad'), 1),
                    'line': _as_int(r.get('line'), 0),
                })
        out.sort(key=lambda x: x.get('line', 0))
        return out

    def get_order_with_items(self, order_id: str):
        header = self.get_order_header(order_id)
        if not header:
            return None
        items = self.get_order_items(order_id)
        return {'header': header, 'items': items}

    def delete_order_from_sheets(self, order_id):
        """Elimina un pedido completo (cabecera e ítems) de Google Sheets."""
        orders_worksheet = self.orders_ws
        items_worksheet = self.items_ws

        # Buscar el pedido en la hoja de pedidos
        order_cell = orders_worksheet.find(order_id)

        if order_cell:
            orders_worksheet.delete_rows(order_cell.row)
        else:
            raise ValueError("Pedido no encontrado en la hoja de Pedidos de Google Sheets")

        # Ahora, eliminar los ítems asociados en la hoja de items
        items = items_worksheet.get_all_values()
        header = items[0]

        order_id_index = header.index("order_id")

        rows_to_delete = []
        for i, row in enumerate(items[1:], start=2):
            if row[order_id_index] == order_id:
                rows_to_delete.append(i)

        for row in reversed(rows_to_delete):
            items_worksheet.delete_rows(row)

        # Limpiar cache
        self.cache.store.clear()

        return True
    
    def log_audit(self, user_id: str, action: str, details: str):
        """
        Registra acciones en la hoja AuditLog.
        Si no existe, la crea.
        """
        try:
            # Intentar obtener o crear la hoja
            try:
                audit_ws = self.spreadsheet.worksheet('AuditLog')
            except Exception:
                audit_ws = self.spreadsheet.add_worksheet('AuditLog', rows=3000, cols=5)
                audit_ws.update('1:1', [['timestamp', 'user_id', 'action', 'details', 'ip']])
            
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Agregar registro
            audit_ws.append_row([
                timestamp,
                user_id,
                action,
                details,
                'N/A'  # IP se podría obtener de request.remote_addr
            ], value_input_option='USER_ENTERED')
            
            print(f"[AUDIT] Logged: {action} by {user_id}")
            
        except Exception as e:
            print(f"[AUDIT ERROR] Could not log: {e}")

    def list_all_users(self):
        """Devuelve lista de todos los usuarios"""
        rows = self._records(self.ids_ws)
        users = []
        for r in rows:
            users.append({
                'ID': r.get('ID') or r.get('id'),
                'nombre': r.get('nombre') or r.get('Nombre') or '',
                'role': r.get('rol') or r.get('role') or 'asesora',
                'has_password': bool((r.get('password_hash') or '').strip())
            })
        return users












