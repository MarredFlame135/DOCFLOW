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
    
    # Planes: 'free', 'intermedio', 'pro'
    subscription_tier = db.Column(db.String(50), default='free')  
    subscription_active = db.Column(db.Boolean, default=True)
    subscription_end_date = db.Column(db.DateTime, nullable=True)
    
    # --- NUEVOS CAMPOS DE INTEGRACIÓN CON STRIPE ---
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relación con sus sesiones activas
    sessions = db.relationship('UserSession', backref='user', cascade="all, delete-orphan", lazy=True)

    def get_plan_limits(self):
        """Retorna las limitaciones exactas del plan actual del usuario."""
        limits = {
            'free': {
                'max_pdfs': 10,
                'max_sessions': 1,
                'name': 'Básico Gratuito'
            },
            'intermedio': {
                'max_pdfs': 200,
                'max_sessions': 1,
                'name': 'Plan Intermedio'
            },
            'pro': {
                'max_pdfs': 1000,
                'max_sessions': 3,
                'name': 'Plan Avanzado Pro'
            }
        }
        return limits.get(self.subscription_tier, limits['free'])

    def es_pro(self):
        """Retorna True si tiene plan intermedio o pro activo."""
        if self.subscription_tier in ['intermedio', 'pro'] and self.subscription_active:
            if self.subscription_end_date is None:
                return True
            return self.subscription_end_date > datetime.utcnow()
        return False


class UserSession(db.Model):
    __tablename__ = 'user_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    session_token = db.Column(db.String(256), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)