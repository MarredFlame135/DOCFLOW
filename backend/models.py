# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # Variables de suscripción
    subscription_tier = db.Column(db.String(50), default='free')  # 'free', 'pro'
    subscription_active = db.Column(db.Boolean, default=True)
    subscription_end_date = db.Column(db.DateTime, nullable=True)  # Si es None, es Permanente
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def es_pro(self):
        """Retorna True si el usuario tiene el rango PRO activo y vigente (o permanente)."""
        if self.subscription_tier == 'pro' and self.subscription_active:
            if self.subscription_end_date is None:
                return True  # Acceso ilimitado / Permanente (como el admin)
            return self.subscription_end_date > datetime.utcnow()
        return False