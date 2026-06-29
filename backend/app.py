import os
import sys
import pandas as pd
import traceback
import time
from flask import Flask, render_template, request, send_file

# --- CONFIGURACIÓN DE RUTAS ---
# Esto asegura que Python encuentre tus archivos locales en la carpeta backend
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# Intentamos importar los módulos locales con manejo de errores
try:
    from processor import extraer_datos_certificado
    from excel_filler import llenar_excel
    from merger import complementar_excels
    print("✅ Módulos locales cargados correctamente.")
except ImportError as e:
    print(f"❌ ERROR CRÍTICO DE IMPORTACIÓN: {e}")
    print("Asegúrate de que 'processor.py', 'excel_filler.py' y 'merger.py' estén en la carpeta 'backend'.")

app = Flask(__name__, template_folder='../frontend', static_folder='../frontend')

# Límite de 1GB para permitir subidas masivas de 1000+ archivos
app.config['MAX_CONTENT_LENGTH'] = 1000 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

# Crear carpetas necesarias
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# --- RUTAS DE NAVEGACIÓN ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/extraer_ui')
def extraer_ui(): 
    return render_template('extraer.html')

@app.route('/comparar_ui')
def comparar_ui(): 
    return render_template('comparar.html')

@app.route('/listar_ui')
def listar_ui(): 
    return render_template('listar.html')

# --- LÓGICA DE PROCESAMIENTO ---

@app.route('/action_extraer', methods=['POST'])
def action_extraer():
    """Paso 1: Extracción de 1000+ PDFs con limpieza de nombres"""
    try:
        if 'plantilla' not in request.files: return "Error: Sube una plantilla."
        
        plantilla = request.files['plantilla']
        pdfs = request.files.getlist('pdfs')
        
        if not pdfs or pdfs[0].filename == '': return "Error: No hay PDFs."

        ruta_p = os.path.join(UPLOAD_FOLDER, "temp_plantilla_p1.xlsx")
        plantilla.save(ruta_p)
        
        datos_para_excel = []
        total = len(pdfs)
        print(f"\n🚀 Iniciando lote de {total} archivos...")

        for i, f in enumerate(pdfs, start=1):
            if f and f.filename.lower().endswith('.pdf'):
                ruta_temp = os.path.join(UPLOAD_FOLDER, f.filename)
                f.save(ruta_temp)
                
                # Extraer (Limpia el nombre del archivo y lee el PDF)
                resultado = extraer_datos_certificado(ruta_temp)
                datos_para_excel.append(resultado)
                
                # Liberación de memoria y disco inmediata
                if os.path.exists(ruta_temp): os.remove(ruta_temp)
                
                # Reporte de progreso cada 50 archivos en la terminal
                if i % 50 == 0:
                    print(f"⏳ Progreso: {i}/{total} certificados procesados...")

        # Generar Excel final
        res = llenar_excel(datos_para_excel, ruta_p, "Paso1_Extraido_Masivo.xlsx")
        print(f"✅ Lote de {total} terminado con éxito.")
        
        return send_file(os.path.abspath(res), as_attachment=True)

    except Exception as e:
        err = traceback.format_exc()
        print(err)
        return f"<h3>Error en Paso 1:</h3><pre>{err}</pre>"

@app.route('/action_comparar', methods=['POST'])
def action_comparar():
    """Paso 2: Complementación con Base de Datos Maestra"""
    try:
        f_reciente = request.files['reciente']
        f_maestro = request.files['maestro']
        
        r_reciente = os.path.join(UPLOAD_FOLDER, "temp_reciente.xlsx")
        r_maestro = os.path.join(UPLOAD_FOLDER, "temp_maestro.xlsx")
        
        f_reciente.save(r_reciente)
        f_maestro.save(r_maestro)
        
        res = complementar_excels(r_reciente, r_maestro, "Resultado_Final_Completo.xlsx")
        
        if res is None: return "Error en la comparación de datos."
        return send_file(os.path.abspath(res), as_attachment=True)
    except Exception as e:
        err = traceback.format_exc()
        print(err)
        return f"<h3>Error en Paso 2:</h3><pre>{err}</pre>"

@app.route('/action_listar', methods=['POST'])
def action_listar():
    """Paso 3: Inventario simple de nombres"""
    try:
        pdfs = request.files.getlist('pdfs')
        lista = [{"No.": i, "Nombre del Archivo": f.filename} for i, f in enumerate(pdfs, start=1) if f.filename != '']
        
        df = pd.DataFrame(lista)
        ruta = os.path.join(OUTPUT_FOLDER, "Inventario_DocuFlow.xlsx")
        df.to_excel(ruta, index=False)
        return send_file(os.path.abspath(ruta), as_attachment=True)
    except Exception as e:
        return f"Error: {str(e)}"

# --- ARRANQUE DEL SISTEMA ---

if __name__ == '__main__':
    print("\n" + "="*50)
    print("   DOCUFLOW PRO - SISTEMA DE AUTOMATIZACIÓN")
    print("="*50)
    print("🌐 Accede en: http://127.0.0.1:5001")
    print("📌 Presiona Ctrl+C para apagar el servidor.")
    print("="*50 + "\n")
    
    # threaded=True permite que el servidor no se bloquee durante los 1000 PDFs
    app.run(debug=True, port=5001, threaded=True)