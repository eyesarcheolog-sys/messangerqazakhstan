from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime
from sqlalchemy import or_

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-super-secret-key-that-no-one-knows'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///messenger.db'
db = SQLAlchemy(app)
socketio = SocketIO(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

user_sids = {}

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='author', lazy=True)
    received_messages = db.relationship('Message', foreign_keys='Message.recipient_id', backref='recipient_user', lazy=True)

    def __repr__(self):
        return f'<User {self.username}>'

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    def __repr__(self):
        return f'<Message {self.body}>'

@login_manager.user_loader
def load_user(user_id):
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

# --- API ДЛЯ ЗАГРУЗКИ ИСТОРИИ ---
@app.route('/history/<username>')
@login_required
def history(username):
    peer = User.query.filter_by(username=username).first_or_404()
    
    messages = db.session.query(Message).filter(
        or_(
            (Message.sender_id == current_user.id) & (Message.recipient_id == peer.id),
            (Message.sender_id == peer.id) & (Message.recipient_id == current_user.id)
        )
    ).order_by(Message.timestamp.asc()).all()
    
    messages_json = [
        {'sender': msg.author.username, 'message': msg.body}
        for msg in messages
    ]
    
    return jsonify(messages_json)

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
    
    recipient_obj = User.query.filter_by(username=recipient_username).first()
    if not recipient_obj:
        return

    new_message = Message(sender_id=current_user.id, recipient_id=recipient_obj.id, body=message_text)
    db.session.add(new_message)
    db.session.commit()

    recipient_sid = user_sids.get(recipient_username)
    message_payload = {'sender': current_user.username, 'message': message_text}
    
    if recipient_sid:
        emit('receive_private_message', message_payload, to=recipient_sid)
    
    emit('receive_private_message', message_payload, to=request.sid)

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)