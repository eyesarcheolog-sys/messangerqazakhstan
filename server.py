from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-super-secret-key-that-no-one-knows'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///messenger.db'
db = SQLAlchemy(app)
socketio = SocketIO(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Словарь для отслеживания онлайн-пользователей: {'имя_пользователя': 'session_id'}
user_sids = {}

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

@login_manager.user_loader
def load_user(user_id):
    # SQLAlchemy 2.0 рекомендует использовать db.session.get() вместо User.query.get()
    return db.session.get(User, int(user_id))

# --- МАРШРУТЫ (ROUTES) ---
@app.route('/')
@login_required
def index():
    users = User.query.all()
    return render_template('index.html', current_user=current_user, users=users)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return "Это имя пользователя уже занято!"
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect(url_for('index'))
        else:
            return "Неверное имя пользователя или пароль!"
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- ЛОГИКА WEBSOCKET ---
@socketio.on('connect')
@login_required
def handle_connect():
    user_sids[current_user.username] = request.sid
    print(f"User {current_user.username} connected with sid {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated and current_user.username in user_sids:
        del user_sids[current_user.username]
        print(f"User {current_user.username} disconnected")

@socketio.on('private_message')
@login_required
def handle_private_message(data):
    recipient_username = data['recipient']
    message_text = data['message']
    
    recipient_sid = user_sids.get(recipient_username)
    
    message_payload = {
        'sender': current_user.username,
        'message': message_text
    }
    
    if recipient_sid:
        emit('receive_private_message', message_payload, to=recipient_sid)
    
    emit('receive_private_message', message_payload, to=request.sid)
    print(f"Message from {current_user.username} to {recipient_username}: {message_text}")

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Для локального запуска используется стандартный сервер
    socketio.run(app, debug=True)