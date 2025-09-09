from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# Важно: мы инициализируем db здесь, но свяжем его с app в server.py
db = SQLAlchemy()

group_members = db.Table('group_members',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('group.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='author', lazy=True)
    groups = db.relationship('Group', secondary=group_members, lazy='subquery',
                             backref=db.backref('members', lazy=True))
    assistants = db.relationship('Assistant', backref='owner', lazy=True)
    google_credentials_json = db.Column(db.Text, nullable=True)

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    messages = db.relationship('Message', backref='group', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)
    body = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    audio_url = db.Column(db.String(255), nullable=True)
    transcription = db.Column(db.Text, nullable=True)

class Assistant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    status = db.Column(db.String(20), nullable=False, default='inactive')
    instructions = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # --- НАЧАЛО: НОВОЕ ПОЛЕ ДЛЯ ИНСТРУМЕНТОВ ---
    tools = db.Column(db.String(500), nullable=True) # Хранит названия инструментов через запятую
    # --- КОНЕЦ НОВОГО ПОЛЯ ---
    
    knowledge_sources = db.relationship('Knowledge', backref='assistant', lazy=True, cascade="all, delete-orphan")

class Knowledge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    assistant_id = db.Column(db.Integer, db.ForeignKey('assistant.id'), nullable=False)

# --- НАЧАЛО: Новый класс для хранения истории чата с ассистентом ---
class AssistantMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'user' или 'assistant'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    owner = db.relationship('User', backref=db.backref('assistant_messages', lazy=True))
# --- КОНЕЦ НОВОГО КЛАССА ---
