# app_factory.py

import os
from flask import Flask, session, request
from flask_babel import Babel
from models import db
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_login import LoginManager

# --- ИЗМЕНЕНИЕ: ОБЪЕКТЫ СОЗДАЮТСЯ ЗДЕСЬ, НА ГЛОБАЛЬНОМ УРОВНЕ ---
app = Flask(__name__, instance_relative_config=True)
migrate = Migrate()
socketio = SocketIO()
login_manager = LoginManager()
babel = Babel()

# --- ИЗМЕНЕНИЕ: ФУНКЦИЯ ВЫНЕСЕНА НА ГЛОБАЛЬНЫЙ УРОВЕНЬ ---
def get_locale():
    # Эта функция теперь доступна для импорта из других файлов
    lang = request.args.get('lang')
    if lang in app.config.get('LANGUAGES', {}):
        session['lang'] = lang
    return session.get('lang', request.accept_languages.best_match(app.config.get('LANGUAGES', {}).keys()))

def create_flask_app():
    """Конфигурирует существующий экземпляр приложения Flask и регистрирует маршруты."""
    
    # --- КОНФИГУРАЦИЯ ---
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-development-secret-key')
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url or f"sqlite:///{os.path.join(app.instance_path, 'messenger.db')}"
    
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = { "pool_pre_ping": True, "pool_recycle": 300 }
    app.config['LANGUAGES'] = {'en': 'English', 'ru': 'Русский', 'kk': 'Қазақша'}
    app.config['BABEL_DEFAULT_LOCALE'] = 'ru'
    
    # Создаем папку instance, если ее нет
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # --- СВЯЗЫВАНИЕ РАСШИРЕНИЙ С ПРИЛОЖЕНИЕМ ---
    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app)
    login_manager.init_app(app)
    babel.init_app(app, locale_selector=get_locale)

    # --- НАСТРОЙКА LOGIN MANAGER ---
    login_manager.login_view = 'login'
    from flask_babel import gettext as _
    login_manager.login_message = _("Please log in to access this page.")
    
    from models import User
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        # --- РЕГИСТРАЦИЯ МАРШРУТОВ И СОКЕТОВ ---
        import server 
        
    return app