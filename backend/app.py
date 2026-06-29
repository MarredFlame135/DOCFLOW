import os
import sys
import pandas as pd
import traceback
import time
import re
import uuid
import stripe  # SDK de Stripe
from datetime import datetime, timedelta  # Importación corregida para el registro temporal de tokens y planes
from flask import Flask, render_template, request, send_file, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

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
from models import db, User, UserSession
db.init_app(app)

# --- CONFIGURACIÓN DE STRIPE ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_tu_clave_secreta_aqui')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', 'whsec_tu_webhook_secret_aqui')

# Variables con los Price IDs de tus planes creados en el Dashboard de Stripe
STRIPE_PRICE_INTERMEDIO = os.environ.get('STRIPE_PRICE_INTERMEDIO', 'price_1X_ejemplo_intermedio')
STRIPE_PRICE_PRO = os.environ.get('STRIPE_PRICE_PRO', 'price_1Y_ejemplo_pro')

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
            subscription_tier="pro"  # Cuenta de administrador con plan PRO ilimitado
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

# --- VERIFICACIÓN DE SESIONES ÚNICAS SIMULTÁNEAS ---
@app.before_request
def verificar_sesion_activa():
    """Valida que la sesión actual siga activa en la base de datos."""
    # Permitir acceso al webhook de Stripe sin requerir login o verificación de sesión de usuario
    if request.endpoint == 'stripe_webhook':
        return

    if request.endpoint in ['static', 'logout', 'login', 'registro', 'index', 'planes_ui']:
        return
        
    if current_user.is_authenticated:
        token_actual = session.get('session_token')
        sesion_valida = UserSession.query.filter_by(user_id=current_user.id, session_token=token_actual).first()
        
        if not sesion_valida:
            logout_user()
            session.pop('session_token', None)
            flash('Tu sesión fue cerrada porque iniciaste sesión en otro dispositivo o tu plan no permite más conexiones simultáneas.', 'danger')
            return redirect(url_for('login'))

# --- RUTAS DE AUTENTICACIÓN ---

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
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
        
        # --- OFERTA DE LANZAMIENTO: 1 MES GRATIS DE PLAN INTERMEDIO ($249) ---
        un_mes_futuro = datetime.utcnow() + timedelta(days=30)
        
        nuevo_usuario = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            subscription_tier='intermedio',  # Rango intermedio de prueba gratis
            subscription_end_date=un_mes_futuro
        )
        
        db.session.add(nuevo_usuario)
        db.session.commit()
        
        flash('¡Cuenta creada con éxito! Se ha activado tu mes de prueba de PLAN INTERMEDIO gratuito.', 'success')
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
            
            # Generar identificador de sesión único
            token_sesion = str(uuid.uuid4())
            session['session_token'] = token_sesion
            
            # Guardar la sesión en la base de datos
            nueva_sesion = UserSession(user_id=usuario.id, session_token=token_sesion)
            db.session.add(nueva_sesion)
            db.session.commit()
            
            # Controlar sesiones concurrentes según su plan
            limites = usuario.get_plan_limits()
            max_sesiones = limites['max_sessions']
            
            sesiones_activas = UserSession.query.filter_by(user_id=usuario.id).order_by(UserSession.created_at.asc()).all()
            if len(sesiones_activas) > max_sesiones:
                exceso = len(sesiones_activas) - max_sesiones
                for i in range(exceso):
                    db.session.delete(sesiones_activas[i])
                db.session.commit()
            
            flash('Sesión iniciada.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    token_actual = session.get('session_token')
    if token_actual:
        UserSession.query.filter_by(session_token=token_actual).delete()
        db.session.commit()
        
    logout_user()
    session.pop('session_token', None)
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('login'))

# --- RUTAS DE NAVEGACIÓN ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
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

# --- ACCIONES DE COMPRA STRIPE (REEMPLAZA SIMULADOR ANTERIOR) ---

@app.route('/upgrade/<plan>')
@login_required
def upgrade_plan(plan):
    """Genera una sesión de pago real de Stripe Billing y redirige al usuario."""
    if plan not in ['intermedio', 'pro']:
        flash('Plan de pago no válido.', 'danger')
        return redirect(url_for('planes_ui'))
        
    # Mapeo al Price ID correspondiente en base al entorno
    price_id = STRIPE_PRICE_INTERMEDIO if plan == 'intermedio' else STRIPE_PRICE_PRO
    
    # Validación de seguridad por si no se configuraron las llaves
    if not stripe.api_key or stripe.api_key == 'sk_test_tu_clave_secreta_aqui' or price_id.startswith('price_1X_ejemplo'):
        flash('La pasarela de pago se encuentra en mantenimiento temporal. Intenta más tarde.', 'danger')
        return redirect(url_for('planes_ui'))

    try:
        user = User.query.get(current_user.id)
        
        # 1. Crear o asociar Cliente en Stripe
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.username,
                metadata={"user_id": user.id}
            )
            user.stripe_customer_id = customer.id
            db.session.commit()
            
        # 2. Generar el Checkout Session para suscripciones recurrentes
        checkout_session = stripe.checkout.Session.create(
            customer=user.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('stripe_success', _external=True),
            cancel_url=url_for('planes_ui', _external=True),
            metadata={
                'user_id': user.id,
                'plan': plan
            }
        )
        return redirect(checkout_session.url, code=303)
        
    except Exception as e:
        print(f"❌ Error al crear Stripe Session: {e}")
        flash('Ocurrió un error al procesar el enlace de compra. Contacte al administrador.', 'danger')
        return redirect(url_for('planes_ui'))

@app.route('/stripe-success')
@login_required
def stripe_success():
    flash('🎉 ¡Pago procesado con éxito! Tu suscripción se activará en tu panel de forma automática en breve.', 'success')
    return redirect(url_for('dashboard'))

# --- ENDPOINT DEL WEBHOOK DE STRIPE ---

@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Recibe y valida las notificaciones asíncronas de cobros de Stripe."""
    payload = request.data
    sig_header = request.headers.get('STRIPE_SIGNATURE') or request.headers.get('Stripe-Signature')
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        # Payload corrupto
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        # Firma inválida o sospecha de spoofing
        return 'Invalid signature', 400

    event_type = event['type']
    data_object = event['data']['object']

    # EVENTO 1: Checkout completado con éxito
    if event_type == 'checkout.session.completed':
        metadata = data_object.get('metadata', {})
        user_id = metadata.get('user_id')
        plan = metadata.get('plan')
        stripe_sub_id = data_object.get('subscription')
        stripe_cust_id = data_object.get('customer')

        if user_id and plan:
            user = User.query.get(int(user_id))
            if user:
                user.subscription_tier = plan
                user.subscription_active = True
                user.stripe_subscription_id = stripe_sub_id
                user.stripe_customer_id = stripe_cust_id
                # Establecer una fecha provisional amplia (32 días) que se actualiza con la factura
                user.subscription_end_date = datetime.utcnow() + timedelta(days=32)
                
                # Gestión de sesiones concurrentes: reducir el excedente si bajó de nivel
                limites = user.get_plan_limits()
                max_sesiones = limites['max_sessions']
                sesiones_activas = UserSession.query.filter_by(user_id=user.id).order_by(UserSession.created_at.asc()).all()
                if len(sesiones_activas) > max_sesiones:
                    exceso = len(sesiones_activas) - max_sesiones
                    for i in range(exceso):
                        db.session.delete(sesiones_activas[i])
                
                db.session.commit()
                print(f"✅ Webhook: Plan {plan.upper()} activado con éxito para el Usuario ID: {user_id}")

    # EVENTO 2 & 3: Cambios o actualizaciones en las suscripciones recurrentes (renovación/pago fallido)
    elif event_type in ['customer.subscription.updated', 'customer.subscription.deleted']:
        stripe_sub_id = data_object['id']
        user = User.query.filter_by(stripe_subscription_id=stripe_sub_id).first()

        if user:
            if event_type == 'customer.subscription.deleted':
                # Suscripción cancelada de por vida o impago absoluto
                user.subscription_tier = 'free'
                user.subscription_active = True
                user.subscription_end_date = None
                user.stripe_subscription_id = None
                db.session.commit()
                print(f"🛑 Webhook: Suscripción cancelada y revertida a GRATUITA para el Usuario ID: {user.id}")
            else:
                # Actualización de ciclo de pago o renovación mensual exitosa
                status = data_object.get('status')
                if status in ['active', 'trialing']:
                    user.subscription_active = True
                else:
                    # En mora, suspendida o incompleta
                    user.subscription_active = False
                
                # Leer fecha exacta del fin de periodo asignada por Stripe
                period_end_timestamp = data_object.get('current_period_end')
                if period_end_timestamp:
                    user.subscription_end_date = datetime.fromtimestamp(period_end_timestamp)
                
                db.session.commit()
                print(f"🔄 Webhook: Suscripción actualizada para Usuario ID: {user.id}. Estado actual en Stripe: {status}")

    return 'OK', 200

# --- ACCIÓN DE ADMINISTRACIÓN MANUAL ---

@app.route('/admin/promover', methods=['GET', 'POST'])
@login_required
def admin_promover():
    if current_user.email != 'admin@docuflow.com':
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        tiempo = request.form.get('tiempo')
        
        usuario = User.query.filter_by(email=email).first()
        if not usuario:
            flash('Usuario no encontrado.', 'danger')
            return redirect(url_for('admin_promover'))
            
        if tiempo == 'free':
            usuario.subscription_tier = 'free'
            usuario.subscription_end_date = None
            usuario.stripe_subscription_id = None
        elif tiempo == 'infinite':
            usuario.subscription_tier = 'pro'
            usuario.subscription_end_date = None
            usuario.stripe_subscription_id = None
        else:
            meses_otorgados = int(tiempo)
            usuario.subscription_tier = 'pro'
            usuario.subscription_active = True
            usuario.subscription_end_date = datetime.utcnow() + timedelta(days=meses_otorgados * 30)
            
        db.session.commit()
        flash(f'Suscripción del usuario {usuario.username} actualizada.', 'success')
        return redirect(url_for('admin_promover'))
        
    return render_template('admin_promover.html')

# --- LÓGICA DE PROCESAMIENTO ---

@app.route('/action_extraer', methods=['POST'])
@login_required
def action_extraer():
    try:
        if 'plantilla' not in request.files: return "Error: Sube una plantilla."
        plantilla = request.files['plantilla']
        pdfs = request.files.getlist('pdfs')
        if not pdfs or pdfs[0].filename == '': return "Error: No hay PDFs."

        total = len(pdfs)
        
        # --- CONTROL DE SUSCRIPCIÓN COMPLETO ---
        limites = current_user.get_plan_limits()
        max_pdfs = limites['max_pdfs']
        
        # Verificamos si excede el límite del plan asignado
        if total > max_pdfs:
            return """
            <h3>Límite de Suscripción Superado</h3>
            <p>Tu plan actual ({plan_name}) solo permite procesar un límite de {max_pdfs} certificados PDF por lote.</p>
            <p>Estás intentando procesar {total} archivos.</p>
            <p><a href='/planes_ui'>Mejora tu plan de suscripción aquí.</a></p>
            """.format(plan_name=limites['name'], max_pdfs=max_pdfs, total=total)

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