import os
import sys
import pandas as pd
import traceback
import time
import re
from flask import Flask, render_template, request, send_file, redirect, url_for, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
# Cerca de la línea 6, actualiza la importación de datetime:
from datetime import datetime, timedelta
# --- CONFIGURACIÓN DE RUTAS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    from processor import extraer_datos_certificado
    from excel_filler import llenar_excel
    from merger import complementar_excels
    print("✅ Módulos locales cargados correctamente.")
except ImportError as e:
    print(f"❌ ERROR CRÍTICO DE IMPORTACIÓN: {e}")

app = Flask(__name__, template_folder='../frontend', static_folder='../frontend')
app.config['MAX_CONTENT_LENGTH'] = 2000 * 1024 * 1024

# --- BASE DE DATOS Y SEGURIDAD ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'clave-secreta-para-desarrollo-local-12345')

db_url = os.environ.get('DATABASE_URL', 'sqlite:///docuflow.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar Base de Datos y Flask-Login
from models import db, User
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, inicia sesión para acceder a esta función."
login_manager.login_message_category = "info"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# --- CREACIÓN AUTOMÁTICA DE TABLAS Y ADMINISTRADOR ---
with app.app_context():
    db.create_all()
    admin_existente = User.query.filter_by(email="admin@docuflow.com").first()
    if not admin_existente:
        hashed_pw = generate_password_hash("AdminDocFlow2026!", method='pbkdf2:sha256')
        admin = User(
            username="admin",
            email="admin@docuflow.com",
            password_hash=hashed_pw,
            subscription_tier="pro"
        )
        db.session.add(admin)
        db.session.commit()
        print("👑 Usuario administrador de prueba creado exitosamente.")

# --- SISTEMA DE LIMPIEZA AUTOMÁTICA DE DISCO ---
ULTIMA_LIMPIEZA = 0

def limpiar_archivos_antiguos(max_minutos=15):
    ahora = time.time()
    limite = ahora - (max_minutos * 60)
    for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path) and not filename.startswith('.'):
                    try:
                        mtime = os.path.getmtime(file_path)
                        if mtime < limite:
                            os.remove(file_path)
                            print(f"🧹 Limpieza: Archivo temporal eliminado -> {filename}")
                    except Exception as e:
                        print(f"⚠️ Error al eliminar {filename}: {e}")

@app.before_request
def verificar_limpieza_temporal():
    global ULTIMA_LIMPIEZA
    ahora = time.time()
    if ahora - ULTIMA_LIMPIEZA > 300:
        limpiar_archivos_antiguos(max_minutos=15)
        ULTIMA_LIMPIEZA = ahora

# --- RUTAS DE AUTENTICACIÓN ---
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        password_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
        if not re.match(password_regex, password):
            flash('La contraseña debe tener mínimo 8 caracteres, incluir una mayúscula, una minúscula y un número.', 'danger')
            return redirect(url_for('registro'))
        
        user_exists = User.query.filter((User.username == username) | (User.email == email)).first()
        if user_exists:
            flash('El usuario o correo electrónico ya está registrado.', 'danger')
            return redirect(url_for('registro'))
            
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        # --- OFERTA DE LANZAMIENTO: 1 MES PRO GRATIS ---
        un_mes_futuro = datetime.utcnow() + timedelta(days=30)
        
        nuevo_usuario = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            subscription_tier='pro',  # Comienza como PRO de inmediato
            subscription_end_date=un_mes_futuro  # Expira en 30 días
        )
        
        db.session.add(nuevo_usuario)
        db.session.commit()
        
        flash('¡Cuenta creada con éxito! Se ha activado tu mes de prueba PRO gratuito de 30 días.', 'success')
        return redirect(url_for('login'))
        
    return render_template('registro.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        usuario = User.query.filter_by(email=email).first()
        if usuario and check_password_hash(usuario.password_hash, password):
            login_user(usuario)
            flash('Sesión iniciada.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('login'))

# --- RUTAS DE NAVEGACIÓN ---

@app.route('/')
def index():
    """Lobby / Landing Page Pública (No requiere inicio de sesión)"""
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """Panel de control privado con las herramientas (Requiere inicio de sesión)"""
    return render_template('dashboard.html')

@app.route('/extraer_ui')
@login_required
def extraer_ui(): 
    return render_template('extraer.html')

@app.route('/comparar_ui')
@login_required
def comparar_ui(): 
    return render_template('comparar.html')

@app.route('/listar_ui')
@login_required
def listar_ui(): 
    return render_template('listar.html')

@app.route('/planes_ui')
def planes_ui():
    return render_template('planes.html')

# --- ACCIÓN DE SIMULACIÓN DE COMPRA (PRO) ---

@app.route('/upgrade_pro')
@login_required
def upgrade_pro():
    """Simulación de pago de Stripe. Actualiza el usuario a plan PRO."""
    try:
        user = User.query.get(current_user.id)
        user.subscription_tier = 'pro'
        db.session.commit()
        flash('🎉 ¡Suscripción actualizada con éxito! Ahora eres un usuario PRO de DocFlow.', 'success')
        return redirect(url_for('planes_ui'))
    except Exception as e:
        flash('Ocurrió un error al procesar el pago ficticio.', 'danger')
        return redirect(url_for('planes_ui'))

# --- LÓGICA DE PROCESAMIENTO ---

@app.route('/action_extraer', methods=['POST'])
@login_required
def action_extraer():
    try:
        if 'plantilla' not in request.files: return "Error: Sube una plantilla."
        plantilla = request.files['plantilla']
        pdfs = request.files.getlist('pdfs')
        if not pdfs or pdfs[0].filename == '': return "Error: No hay PDFs."

    
        
    # Busca esta sección dentro de /action_extraer en app.py:
        total = len(pdfs)
        
        # Cambiamos el condicional antiguo por el nuevo método dinámico
        if not current_user.es_pro() and total > 10:
            return """
            <h3>Plan Gratuito Superado</h3>
            <p>El plan gratuito solo permite procesar un límite de 10 certificados PDF por lote.</p>
            <p><a href='/planes_ui'>Mejora tu plan a Pro aquí para procesar archivos ilimitados.</a></p>
            """

        ruta_p = os.path.join(UPLOAD_FOLDER, "temp_plantilla_p1.xlsx")
        plantilla.save(ruta_p)
        
        datos_para_excel = []
        for i, f in enumerate(pdfs, start=1):
            if f and f.filename.lower().endswith('.pdf'):
                ruta_temp = os.path.join(UPLOAD_FOLDER, f.filename)
                f.save(ruta_temp)
                resultado = extraer_datos_certificado(ruta_temp)
                datos_para_excel.append(resultado)
                if os.path.exists(ruta_temp): os.remove(ruta_temp)

        res = llenar_excel(datos_para_excel, ruta_p, "Paso1_Extraido_Masivo.xlsx")
        return send_file(os.path.abspath(res), as_attachment=True)
    except Exception as e:
        err = traceback.format_exc()
        return f"<h3>Error en Paso 1:</h3><pre>{err}</pre>"

@app.route('/action_comparar', methods=['POST'])
@login_required
def action_comparar():
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
        return f"<h3>Error en Paso 2:</h3><pre>{err}</pre>"

@app.route('/action_listar', methods=['POST'])
@login_required
def action_listar():
    try:
        pdfs = request.files.getlist('pdfs')
        lista = [{"No.": i, "Nombre del Archivo": f.filename} for i, f in enumerate(pdfs, start=1) if f.filename != '']
        df = pd.DataFrame(lista)
        ruta = os.path.join(OUTPUT_FOLDER, "Inventario_DocuFlow.xlsx")
        df.to_excel(ruta, index=False)
        return send_file(os.path.abspath(ruta), as_attachment=True)
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5001, threaded=True)

@app.route('/admin/promover', methods=['GET', 'POST'])
@login_required
def admin_promover():
    # Seguridad estricta: Solo el administrador oficial puede entrar aquí
    if current_user.email != 'admin@docuflow.com':
        flash('Acceso denegado. No tienes permisos para acceder al Panel Admin.', 'danger')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        tiempo = request.form.get('tiempo')
        
        usuario = User.query.filter_by(email=email).first()
        if not usuario:
            flash('Usuario no encontrado. Verifica el correo.', 'danger')
            return redirect(url_for('admin_promover'))
            
        if tiempo == 'free':
            # Degradar a plan gratuito estándar
            usuario.subscription_tier = 'free'
            usuario.subscription_end_date = None
            flash(f'El usuario {usuario.username} ha sido degradado al plan gratuito.', 'info')
        elif tiempo == 'infinite':
            # Otorgar acceso permanente de por vida
            usuario.subscription_tier = 'pro'
            usuario.subscription_end_date = None
            flash(f'👑 ¡Otorgado! El usuario {usuario.username} ahora tiene PRO de por vida.', 'success')
        else:
            # Otorgar meses específicos (30 días por cada uno)
            meses_otorgados = int(tiempo)
            usuario.subscription_tier = 'pro'
            usuario.subscription_active = True
            usuario.subscription_end_date = datetime.utcnow() + timedelta(days=meses_otorgados * 30)
            flash(f'Suscripción PRO del usuario {usuario.username} extendida exitosamente por {meses_otorgados} mes(es).', 'success')
            
        db.session.commit()
        return redirect(url_for('admin_promover'))
        
    return render_template('admin_promover.html')