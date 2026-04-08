@echo off
rem === Activar el entorno virtual ===
call "C:\portal-asesora\.venv\Scripts\activate.bat"

rem === Crear carpeta de logs si no existe ===
if not exist "C:\portal-asesora\logs" mkdir "C:\portal-asesora\logs"

rem === Ir al directorio del proyecto ===
cd /d "C:\portal-asesora"

rem === Lanzar waitress apuntando al app principal ===
python -m waitress --listen=0.0.0.0:5000 app:app
