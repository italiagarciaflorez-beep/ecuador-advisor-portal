# Portal Asesora (Flask + Google Sheets)

Portal interno estilo dashboard para asesoras de ELEDÉ:
- Login por ID (usuario=ID, contraseña=ID por defecto).
- Registro de pedidos con validación/autocompletado de **Material** desde Google Sheets.
- Histórico de pedidos.
- Acceso a documentos (links de Drive) por asesora.

> **Stack:** Flask (Python), gspread, Service Account, Google Sheets como BD.

---

## 1) Estructura del Google Sheet **"Prueba Ecuador"**

Crea (o reutiliza) el Spreadsheet y agrega estas pestañas y columnas (fila 1 = encabezados exactos):

### `ID` (usuarios)
| ID | nombre | password_hash |
|---|---|---|
| 97802 | ITALIA GARCIA FLOREZ | *(opcional; si está vacío la clave será el mismo ID)* |

> Para generar `password_hash` puedes usar un cuaderno Python local con:
>
> ```python
> from werkzeug.security import generate_password_hash
> print(generate_password_hash("97802"))
> ```

### `Data` (productos)
| Material | Texto breve material | Valor matriz |
|---|---|---|
| 606513 | PANTALON BO1 L AZ48 | S 0005 |

> Son las tres columnas que muestras en tu captura.

### `Pedidos` (cabecera)
| order_id | user_id | fecha | campaña | estado | notas | total_items |
|---|---|---|---|---|---|---|

> **El sistema agrega filas automáticamente**.

### `Items` (detalle)
| order_id | line | material | descripcion | valor_matriz | cantidad |
|---|---|---|---|---|---|

### `Docs` (opcional: documentos por asesora)
| user_id | nombre_archivo | url |
|---|---|---|
| 97802 | Remisión 10149179 | https://drive.google.com/file/d/XXXXXXXX/view |

> Puedes pegar el enlace compartido de Drive. Si prefieres, comparte una carpeta con la cuenta del **Service Account** y pega aquí las URLs.

---

## 2) Variables de entorno

Crea un archivo `.env` (para desarrollo local) a partir de `.env.example`:

```
FLASK_SECRET_KEY=some-random-secret
GOOGLE_SHEETS_SPREADSHEET_ID=<<ID del spreadsheet "Prueba Ecuador">>

# Opción A: SERVICE_ACCOUNT_JSON como JSON plano
SERVICE_ACCOUNT_JSON={...}

# Opción B (recomendada en Render): SERVICE_ACCOUNT_JSON_B64 con el JSON en base64
SERVICE_ACCOUNT_JSON_B64=ewo...
```

### ¿Cómo obtengo el **Spreadsheet ID**?
De la URL del Sheet:
```
https://docs.google.com/spreadsheets/d/1HjJDto5...WJeoA/edit
                               ^^^^^^^^^^^^^^^^^
```

### Da permiso al Service Account
Comparte el Spreadsheet con el correo `xxx@xxx.iam.gserviceaccount.com` de tu Service Account **como Editor**.

---

## 3) Ejecutar localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Crea .env con tus variables
flask --app app run  # o: python app.py
```

Navega a: http://127.0.0.1:5000

---

## 4) Despliegue en **Render** (gratis)

> Requiere tener el código en un repositorio Git (GitHub/GitLab/Bitbucket).

1. **Sube** este proyecto a tu repo.
2. En Render: **New +** → **Web Service** → "Connect repository".
3. **Runtime:** Python 3.11 (Render lo infiere con `requirements.txt`).
4. **Build Command:** `pip install -r requirements.txt`
5. **Start Command:** `gunicorn app:app`
6. **Environment:**
   - Agrega `FLASK_SECRET_KEY` (cualquier cadena aleatoria).
   - `GOOGLE_SHEETS_SPREADSHEET_ID` con el ID del sheet **Prueba Ecuador**.
   - **Una** de las dos: `SERVICE_ACCOUNT_JSON` (pegas el JSON entero) **o** `SERVICE_ACCOUNT_JSON_B64`.
7. Deploy. Render expondrá tu URL pública.

### (Opcional) Convertir el JSON a base64
Para evitar problemas de comillas al pegar el JSON en Render:
```bash
# Linux/Mac
base64 service-account.json | pbcopy  # copia al portapapeles
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json")) | Set-Clipboard
```
Luego pega el valor en la variable `SERVICE_ACCOUNT_JSON_B64`.

---

## 5) Cómo se valida el Material

- El campo **Material** usa autocompletado contra `/api/materials?q=` consultando la pestaña `Data`.
- Al elegir un código válido se traen **Texto breve material** y **Valor matriz**.
- Al guardar, se valida que cada material exista. Se escribe en `Pedidos` y `Items`.

---

## 6) Seguridad

- Sesión por cookies (HTTPOnly, SameSite=Lax).
- Si la columna `password_hash` está presente y con valor, se valida con hash (Werkzeug).
- Si no hay hash, por defecto **usuario=ID** y **contraseña=ID** (política que piden).

> Recomendado: migrar progresivamente a `password_hash` en la pestaña `ID`.

---

## 7) Estructura del proyecto

```
portal-asesora/
├─ app.py
├─ sheets_repo.py
├─ security.py
├─ requirements.txt
├─ runtime.txt
├─ .env.example
├─ static/
│  └─ app.css
└─ templates/
   ├─ base.html
   ├─ login.html
   ├─ dashboard.html
   └─ new_order.html
```

---

## 8) Personalización rápida de UI

Edita `static/app.css` para colores, bordes y tamaños. La maqueta es tipo dashboard con menú lateral.

---

## 9) Notas y mejoras futuras

- Adjuntar automáticamente remisiones/facturas a cada pedido guardando **link de Drive** en `Items` o `Pedidos`.
- Estado del pedido (Aprobado/Enviado/Anulado) administrado desde otra pestaña con validación.
- Exportación a PDF/Excel por pedido.
- Roles: *administrativo* con vistas agregadas.
