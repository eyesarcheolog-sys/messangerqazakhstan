# app_factory.py
import os
from flask import Flask, session, request
from flask_babel import Babel
from models import db
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_login import LoginManager

# --- ИНИЦИАЛИЗАЦИЯ РАСШИРЕНИЙ ---
# Мы создаем экземпляры здесь, но связываем их с приложением внутри фабрики
migrate = Migrate()
socketio = SocketIO()
login_manager = LoginManager()
babel = Babel()

def create_app():
    """Создает и конфигурирует экземпляр приложения Flask."""
    app = Flask(__name__, instance_relative_config=True)

    # --- КОНФИГУРАЦИЯ ---
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-development-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///messenger.db')
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    app.config['LANGUAGES'] = {'en': 'en', 'ru': 'ru', 'kk': 'kk'}
    app.config['BABEL_DEFAULT_LOCALE'] = 'ru'

    # --- СВЯЗЫВАНИЕ РАСШИРЕНИЙ С ПРИЛОЖЕНИЕМ ---
    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app)
    login_manager.init_app(app)
    babel.init_app(app)

    # --- НАСТРОЙКА LOGIN MANAGER ---
    login_manager.login_view = 'login'
    from flask_babel import gettext as _
    login_manager.login_message = _("Please log in to access this page.")
    
    from models import User
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # --- НАСТРОЙКА BABEL ---
    @babel.localeselector
    def get_locale():
        lang = request.args.get('lang')
        if lang in app.config['LANGUAGES']:
            session['lang'] = lang
        return session.get('lang', request.accept_languages.best_match(app.config['LANGUAGES'].keys()))

    with app.app_context():
        # --- РЕГИСТРАЦИЯ МАРШРУТОВ И СОКЕТОВ ---
        # Импортируем здесь, чтобы избежать циклического импорта
        import server 
        
    return app