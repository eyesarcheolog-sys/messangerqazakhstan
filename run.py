# run.py (Наш новый главный файл)

from gevent import monkey
monkey.patch_all()

import os
import uuid
import json
from flask import Flask, session, request, render_template, redirect, url_for, jsonify, send_from_directory, Response
from flask_babel import Babel, gettext as _
from models import db, User, Group, Message, Assistant, Knowledge, AssistantMessage
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime
from sqlalchemy import or_, func
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import google.generativeai as genai
from ai_logic import get_specialist_response
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# --- ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ И РАСШИРЕНИЙ ---
app = Flask(__name__, instance_relative_config=True)
socketio = SocketIO(app)
login_manager = LoginManager(app)
babel = Babel(app)
migrate = Migrate(app, db)
db.init_app(app)

# --- КОНФИГУРАЦИЯ ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_very_long_and_super_secret_key_123!@#')
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or f"sqlite:///{os.path.join(app.instance_path, 'messenger.db')}"
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True, "pool_recycle": 300}
app.config['LANGUAGES'] = {'en': 'English', 'ru': 'Русский', 'kk': 'Қазақша'}
app.config['BABEL_DEFAULT_LOCALE'] = 'ru'

try:
    os.makedirs(app.instance_path)
except OSError:
    pass

# --- НАСТРОЙКА РАСШИРЕНИЙ ---
login_manager.login_view = 'login'
login_manager.login_message = _("Please log in to access this page.")

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@babel.localeselector
def get_locale():
    lang = request.args.get('lang')
    if lang in app.config.get('LANGUAGES', {}):
        session['lang'] = lang
    return session.get('lang', request.accept_languages.best_match(app.config.get('LANGUAGES', {}).keys()))

user_sids = {}

@app.context_processor
def inject_conf_var():
    return dict(AVAILABLE_LANGUAGES=app.config['LANGUAGES'], CURRENT_LANGUAGE=get_locale())

# --- МАРШРУТЫ (ROUTES) ---
# (Весь код из server.py, который был ниже импортов, теперь здесь)
@app.route('/')
@login_required
def index():
    users = User.query.all()
    groups = current_user.groups
    unread_counts = {}
    private_unread = db.session.query(Message.sender_id, func.count(Message.id)).join(User, User.id == Message.sender_id).filter(Message.recipient_id == current_user.id, Message.is_read == False).group_by(Message.sender_id).all()
    user_map = {user.id: user.username for user in users}
    for sender_id, count in private_unread:
        sender_username = user_map.get(sender_id)
        if sender_username: unread_counts[sender_username] = count
    if groups:
        group_ids = [g.id for g in groups]
        group_unread = db.session.query(Message.group_id, func.count(Message.id)).filter(Message.group_id.in_(group_ids), Message.is_read == False, Message.sender_id != current_user.id).group_by(Message.group_id).all()
        for group_id, count in group_unread: unread_counts[f'group_{group_id}'] = count
    return render_template('index.html', current_user=current_user, users=users, groups=groups, unread_counts=unread_counts)

# ... (Вставьте сюда ВСЕ остальные маршруты из вашего server.py, от @app.route('/register'...) до конца файла) ...
# ... (включая все обработчики сокетов @socketio.on(...)) ...

# --- КОНЕЦ ФАЙЛА ---