import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-super-secret-key-that-no-one-knows'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///messenger.db')
db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

user_sids = {}

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
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

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    messages = db.relationship('Message', backref='group', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)
    body = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- МАРШРУТЫ (ROUTES) ---
@app.route('/')
@login_required
def index():
    users = User.query.all()
    groups = current_user.groups
    return render_template('index.html', current_user=current_user, users=users, groups=groups)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return "Это имя пользователя уже занято!"
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
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
        if user and check_password_hash(user.password, password):
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

@app.route('/create_group', methods=['POST'])
@login_required
def create_group():
    group_name = request.form.get('group_name')
    member_ids = request.form.getlist('members')
    if not group_name or not member_ids:
        return "Необходимо название и участники", 400
    if Group.query.filter_by(name=group_name).first():
        return "Группа с таким названием уже существует!", 400
    new_group = Group(name=group_name)
    db.session.add(new_group)
    db.session.commit()
    creator = db.session.get(User, current_user.id)
    new_group.members.append(creator)
    for user_id in member_ids:
        user = db.session.get(User, int(user_id))
        if user:
            new_group.members.append(user)
    db.session.commit()
    return redirect(url_for('index'))

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
        {'sender': msg.author.username, 'message': msg.body, 'timestamp': msg.timestamp.isoformat() + "Z"}
        for msg in messages
    ]
    return jsonify(messages_json)

@app.route('/history/group/<int:group_id>')
@login_required
def group_history(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Группа не найдена или у вас нет доступа", 404
    messages = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()
    messages_json = [
        {'sender': msg.author.username, 'message': msg.body, 'timestamp': msg.timestamp.isoformat() + "Z"}
        for msg in messages
    ]
    return jsonify(messages_json)

@app.route('/group/<int:group_id>')
@login_required
def group_info(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Группа не найдена или у вас нет доступа", 404
    all_users = User.query.all()
    return render_template('group_info.html', group=group, all_users=all_users)

# --- ЛОГИКА WEBSOCKET ---
@socketio.on('connect')
@login_required
def handle_connect():
    user_sids[current_user.username] = request.sid
    for group in current_user.groups:
        join_room(f'group_{group.id}')
    emit('update_online_users', list(user_sids.keys()), broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated and current_user.username in user_sids:
        for group in current_user.groups:
            leave_room(f'group_{group.id}')
        if current_user.username in user_sids:
            del user_sids[current_user.username]
        emit('update_online_users', list(user_sids.keys()), broadcast=True)

@socketio.on('private_message')
@login_required
def handle_private_message(data):
    recipient_username = data['recipient']
    message_text = data['message']
    timestamp = datetime.utcnow()
    recipient_obj = User.query.filter_by(username=recipient_username).first()
    if not recipient_obj:
        return
    new_message = Message(sender_id=current_user.id, recipient_id=recipient_obj.id, body=message_text, timestamp=timestamp)
    db.session.add(new_message)
    db.session.commit()
    recipient_sid = user_sids.get(recipient_username)
    message_payload = {
        'sender': current_user.username,
        'recipient': recipient_username,
        'message': message_text,
        'timestamp': timestamp.isoformat() + "Z"
    }
    if recipient_sid:
        emit('receive_private_message', message_payload, to=recipient_sid)
        emit('new_message_notification', {'sender': current_user.username}, to=recipient_sid)
    emit('receive_private_message', message_payload, to=request.sid)

@socketio.on('group_message')
@login_required
def handle_group_message(data):
    group_id = data['group_id']
    message_text = data['message']
    timestamp = datetime.utcnow()
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return
    new_message = Message(sender_id=current_user.id, group_id=group_id, body=message_text, timestamp=timestamp)
    db.session.add(new_message)
    db.session.commit()
    message_payload = {
        'sender': current_user.username,
        'message': message_text,
        'timestamp': timestamp.isoformat() + "Z",
        'group_id': group_id
    }
    emit('receive_group_message', message_payload, to=f'group_{group_id}')

if __name__ == '__main__':
    socketio.run(app, debug=True)