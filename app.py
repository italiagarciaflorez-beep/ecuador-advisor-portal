import os
import json
import base64
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

from sheets_repo import SheetsRepo
from security import check_password, login_required, admin_required, validate_password_policy

# .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def load_service_account_json():
    raw = os.getenv("SERVICE_ACCOUNT_JSON")
    b64 = os.getenv("SERVICE_ACCOUNT_JSON_B64")
    if b64 and not raw:
        raw = base64.b64decode(b64).decode("utf-8")
    if not raw:
        raise RuntimeError("Missing SERVICE_ACCOUNT_JSON or SERVICE_ACCOUNT_JSON_B64 in environment.")
    return json.loads(raw)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"

@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
if not SPREADSHEET_ID:
    raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID is required")

# Mantén la firma original: creds_json primero, luego spreadsheet_id
repo = SheetsRepo(load_service_account_json(), SPREADSHEET_ID)

# -------------------- Auth --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    user_id = (request.form.get("user_id") or "").strip()
    password = (request.form.get("password") or "").strip()

    if not user_id or not password:
        flash("Ingresa tu ID en usuario y contraseña.", "error")
        return render_template("login.html")

    user = repo.get_user_by_id(user_id)
    if not user:
        flash("ID no válido.", "error")
        return render_template("login.html")

    stored_hash = (user.get("password_hash") or "").strip()
    if stored_hash:
        valid = check_password(password, stored_hash)
    else:
        valid = (password == user_id)

    if not valid:
        flash("Credenciales inválidas.", "error")
        return render_template("login.html")

    session.clear()
    session["user_id"] = user_id
    session["user_name"] = user.get("nombre") or user_id
    session["role"] = user.get("role", "asesora")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -------------------- Portal --------------------
@app.route("/admin")
@login_required
def admin_panel():
    """Panel de administración - Solo para admin"""
    if session.get("role") not in ("adm", "admin"):
        flash("Acceso denegado: requiere privilegios de administrador", "error")
        return redirect(url_for("dashboard"))
    
    # Obtener todos los pedidos
    all_orders = repo.list_orders_all(limit=5000)
    
    # ========== ESTADÍSTICAS POR CAMPAÑA ==========
    campaigns = {}
    for order in all_orders:
        camp = order.get('campaña', 'Sin campaña')
        if camp not in campaigns:
            campaigns[camp] = {'count': 0, 'total_items': 0}
        campaigns[camp]['count'] += 1
        campaigns[camp]['total_items'] += int(order.get('total_items', 0) or 0)
    
    # ========== ESTADÍSTICAS POR ASESORA (CORREGIDO) ==========
    # Ahora agrupa por NOMBRE de asesora, no por user_id
    advisors = {}
    for order in all_orders:
        advisor_name = order.get('Nombre asesora', 'Sin nombre')
        
        # Normalizar nombre (quitar espacios extra, mayúsculas)
        advisor_key = advisor_name.strip().upper()
        
        if advisor_key not in advisors:
            advisors[advisor_key] = {
                'name': advisor_name,  # Nombre original (con formato)
                'count': 0,            # Cantidad de pedidos
                'total_items': 0       # Total de items pedidos
            }
        
        advisors[advisor_key]['count'] += 1
        advisors[advisor_key]['total_items'] += int(order.get('total_items', 0) or 0)
    
    # Ordenar por cantidad de pedidos (de mayor a menor)
    top_advisors = sorted(
        advisors.items(), 
        key=lambda x: x[1]['total_items'], 
        reverse=True
    )[:10]
    
    # Contar asesoras únicas
    total_advisors = len(advisors)
    
    return render_template("admin_panel.html",
                         total_orders=len(all_orders),
                         campaigns=campaigns,
                         top_advisors=top_advisors,
                         total_advisors=total_advisors)  # ← NUEVO


@app.route("/admin/export")
@login_required
def export_orders():
    """Exporta Items especificando rango explícito"""
    if session.get("role") not in ("adm", "admin"):
        return jsonify({"error": "No autorizado"}), 403
    
    try:
        import csv
        from io import StringIO
        from flask import make_response
        
        print("[EXPORT] Leyendo Items con rango específico...")
        
        # ✅ LEER CON RANGO EXPLÍCITO en lugar de get_all_values()
        items_ws = repo.items_ws
        
        # Leer desde A1 hasta la última columna y fila 1000
        # Esto fuerza a Sheets API a leer TODO
        all_values = items_ws.get('A1:P1000')  # P es más allá de N para asegurar
        
        if not all_values:
            flash("La hoja Items está vacía", "error")
            return redirect(url_for("admin_panel"))
        
        print(f"[EXPORT] ✅ Leídas {len(all_values)} filas desde Google Sheets")
        
        # Crear CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Escribir todas las filas
        rows_written = 0
        for row in all_values:
            # Solo escribir filas que tengan al menos algo en las primeras columnas
            if len(row) > 0 and row[0].strip():
                writer.writerow(row)
                rows_written += 1
        
        print(f"[EXPORT] ✅ Exportadas {rows_written} filas")
        
        # Auditoría
        try:
            repo.log_audit(
                user_id=session['user_id'],
                action='EXPORT_ITEMS',
                details=f"Exported {rows_written-1} items from Items sheet (range A1:P1000)"
            )
        except Exception as e:
            print(f"[EXPORT] ⚠️ Error en auditoría: {e}")
        
        # Enviar archivo
        csv_content = output.getvalue()
        response = make_response(csv_content)
        response.headers["Content-Disposition"] = "attachment; filename=items_completo.csv"
        response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        
        print(f"[EXPORT] ✅ Archivo enviado")
        return response
    
    except Exception as e:
        print(f"[EXPORT] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error al exportar: {e}", "error")
        return redirect(url_for("admin_panel"))

@app.route("/")
@login_required
def dashboard():
    if session.get("role") in ("adm", "admin"):
        orders = repo.list_orders_all(limit=5000)
    else:
        orders = repo.list_orders(session["user_id"], limit=5000)

    # si manejas docs asociados:
    try:
        docs = repo.list_documents(session["user_id"])
    except Exception:
        docs = []

    return render_template("dashboard.html",
                           orders=orders,
                           docs=docs,
                           user_name=session.get("user_name"))


@app.route("/new-order", methods=["GET", "POST"])
@login_required
def new_order():
    if request.method == "POST":
        user_id = session["user_id"]
        campaign = (request.form.get("campaign") or "").strip()
        advisor_name = (request.form.get("advisor_name") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        if not advisor_name and notes:
            advisor_name = notes

        items_json = request.form.get("items_json", "[]")
        try:
            items = json.loads(items_json)
        except Exception:
            items = []

        if not items:
            flash("Agrega al menos un ítem.", "error")
            return render_template("new_order.html")

        order_id = repo.create_order(
            user_id=user_id, campaign=campaign, advisor_name=advisor_name, items=items
        )
        
        # ✅ MEJORA: Auditoría de creación
        total_items = sum(int(item.get('cantidad', 1)) for item in items)
        total_productos = len(items)  # Cantidad de productos diferentes
        
        repo.log_audit(
            user_id=session['user_id'],
            action='CREATE_ORDER',
            details=f"Created order {order_id} - Campaign: {campaign} - Advisor: {advisor_name} - Items: {total_items}"
        )
        
        # ✅ NUEVO: Guardar información para mostrar en modal
        session['last_order'] = {
            'order_id': order_id,
            'campaign': campaign,
            'advisor_name': advisor_name,
            'total_items': total_items,
            'total_productos': total_productos,
            'items': items  # ✅ VERIFICAR QUE ESTA LÍNEA EXISTA
        }
        
        flash(f"Pedido {order_id} creado.", "ok")
        return redirect(url_for("dashboard"))

    return render_template("new_order.html")


@app.route('/delete-order/<order_id>', methods=['POST'])
@login_required
def delete_order(order_id):
    """
    Elimina un pedido del sistema con auditoría.
    
    ORDEN CORRECTO:
    1. Verificar existencia
    2. Verificar permisos
    3. Registrar auditoría (ANTES de borrar)
    4. Eliminar
    5. Confirmar
    """
    try:
        # PASO 1: Verificar que el pedido existe
        data = repo.get_order_with_items(order_id)
        if not data:
            flash('❌ Pedido no encontrado', 'error')
            return redirect(url_for('dashboard'))
        
        header = data["header"]
        
        # PASO 2: Verificar permisos
        is_admin = session.get("role") in ("adm", "admin")
        is_owner = header.get("user_id") == session.get("user_id")
        
        if not is_admin and not is_owner:
            flash('⛔ No tienes permiso para eliminar este pedido', 'error')
            return redirect(url_for('dashboard'))
        
        # PASO 3: ✅ AUDITORÍA - ANTES de eliminar
        repo.log_audit(
            user_id=session['user_id'],
            action='DELETE_ORDER',
            details=f"Deleted order {order_id} (owner: {header.get('user_id')}, campaign: {header.get('campaña')}, items: {header.get('total_items')})"
        )
        
        # PASO 4: Eliminar
        repo.delete_order_from_sheets(order_id)
        
        # PASO 5: Confirmar
        flash('✅ Pedido eliminado correctamente', 'success')
        
    except Exception as e:
        print(f"[ERROR] Delete order failed: {e}")
        flash(f'❌ Ocurrió un error al eliminar el pedido: {e}', 'danger')
    
    return redirect(url_for('dashboard'))


# -------------------- API --------------------

@app.route("/api/materials")
@login_required
def api_materials():
    q = (request.args.get("q") or "").strip()
    return jsonify(repo.search_products(q, limit=12))

@app.route("/api/validate-code", methods=["POST"])
@login_required
def api_validate_code():
    """
    NUEVO ENDPOINT: Valida si un código existe en la base de datos.
    
    Recibe: { "code": "ABC123" }
    Devuelve: { "valid": true/false, "error": "mensaje si no es válido" }
    
    CAMBIO: Nuevo endpoint para validación en tiempo real.
    """
    try:
        data = request.get_json() or {}
        code = (data.get("code") or "").strip()
        
        if not code:
            return jsonify({
                "valid": False,
                "error": "El código no puede estar vacío"
            }), 400
        
        product = repo.get_product_by_code(code)
        
        if not product:
            return jsonify({
                "valid": False,
                "error": f"Código '{code}' no encontrado en la base de datos"
            }), 404
        
        return jsonify({
            "valid": True,
            "error": None
        }), 200
        
    except Exception as e:
        print(f"Error validating code: {e}")
        return jsonify({
            "valid": False,
            "error": "Error al validar el código"
        }), 500

@app.route("/api/product/<code>")
@login_required
def api_product(code):
    p = repo.get_product_by_code(code.strip())
    if not p:
        return jsonify({"error": "No encontrado"}), 404

    descripcion = (
        p.get("Texto breve material") or p.get("Descripción") or p.get("descripcion") or ""
    )
    talla = p.get("Talla") or ""

    def as_number(x):
        """
        Convierte a float sin destruir el '.' decimal.
        Soporta:
        - US:   '1,234.56' -> 1234.56  (quita comas de miles)
        - EU:   '1.234,56' -> 1234.56  (quita puntos de miles y cambia coma por punto)
        - Simple: '19.99'  -> 19.99
        """
        if x is None:
            return None
        s = str(x).strip()
        if s == "":
            return None

        if "," in s and "." in s:
            # Si el último separador es coma, asume formato europeo '1.234,56'
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "")   # quita miles
                s = s.replace(",", ".")  # coma -> punto decimal
            else:
                s = s.replace(",", "")   # quita miles estilo US
        elif "," in s and "." not in s:
            # Solo coma presente: puede ser decimal europeo '123,45'
            s = s.replace(",", ".")
        else:
            # Solo puntos o sin separadores: dejar tal cual
            pass

        try:
            return float(s)
        except Exception:
            return None

    # PRECIOS TAL CUAL (ya en USD en tu hoja Data)
    precio_catalogo = as_number(p.get("Precio_Catalogo") or p.get("precio_catalogo") or p.get("Precio catalogo"))
    precio_factura  = as_number(p.get("Precio_Factura")  or p.get("precio_factura")  or p.get("Precio factura"))

    return jsonify({
        "code": p.get("Código", "") or p.get("code", ""),
        "descripcion": descripcion,
        "Talla": talla,
        "precio_catalogo": f"{precio_catalogo:.2f}" if precio_catalogo is not None else None,
        "precio_factura": f"{precio_factura:.2f}" if precio_factura is not None else None,
    })

@app.route("/api/orders/<order_id>")
@login_required
def api_get_order(order_id):
    data = repo.get_order_with_items(order_id.strip())
    if not data:
        return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404
    header = data["header"]
    if session.get("role") not in ("adm", "admin") and header.get("user_id") != session.get("user_id"):
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return jsonify({"ok": True, "data": data})

@app.route("/api/update-order/<order_id>", methods=["POST"])
@login_required
def api_update_order(order_id):
    """
    Actualiza los ítems de un pedido existente.
    Recibe JSON con { items: [...] }
    """
    data = repo.get_order_with_items(order_id.strip())
    if not data:
        return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404

    header = data["header"]
    # Verificar autorización: admin o propietario del pedido
    if session.get("role") not in ("adm", "admin") and header.get("user_id") != session.get("user_id"):
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    try:
        payload = request.get_json() or {}
        items = payload.get("items", [])

        if not items:
            return jsonify({"ok": False, "error": "El pedido debe tener al menos un ítem"}), 400

        # ✅ NUEVO: Guardar estado ANTES de actualizar (para auditoría)
        old_items_count = len(data.get("items", []))
        old_total_items = sum(int(item.get('cantidad', 0)) for item in data.get("items", []))

        success = repo.update_order(order_id.strip(), items)
        if success:
            # Obtener los datos actualizados del pedido
            updated_data = repo.get_order_with_items(order_id.strip())
            
            # ✅ NUEVO: Calcular estado DESPUÉS de actualizar
            new_items_count = len(items)
            new_total_items = sum(int(item.get('cantidad', 1)) for item in items)
            
            # ✅ NUEVO: Registrar auditoría
            repo.log_audit(
                user_id=session['user_id'],
                action='EDIT_ORDER',
                details=f"Edited order {order_id} (owner: {header.get('user_id')}, campaign: {header.get('campaña')}) - Products: {old_items_count}→{new_items_count}, Items: {old_total_items}→{new_total_items}"
            )
            
            return jsonify({
                "ok": True, 
                "message": "Pedido actualizado",
                "data": updated_data
            })
        else:
            return jsonify({"ok": False, "error": "No se pudo actualizar el pedido"}), 500

    except Exception as e:
        print(f"Error updating order: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    
    

# Listado de asesoras para el <select> del formulario (solo F y G del sheet ID)
@app.route("/api/advisors")
@login_required
def api_advisors():
    items = repo.list_advisors()
    return jsonify({"ok": True, "data": items})

@app.route("/api/clear-last-order", methods=["POST"])
@login_required
def clear_last_order():
    """Limpia los datos del último pedido de la sesión"""
    session.pop('last_order', None)
    return jsonify({"success": True})

@app.route("/healthz")
def healthz():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)