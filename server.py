import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
from openai import OpenAI
import google.generativeai as genai

# --- APP SETUP ---
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

# --- DATABASE MODELS ---
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
    body = db.Column(db.String(500), nullable=True)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    audio_url = db.Column(db.String(255), nullable=True)
    transcription = db.Column(db.Text, nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- ROUTES ---
@app.route('/')
@login_required
def index():
    users = User.query.all()
    groups = current_user.groups
    
    unread_counts = {}
    private_messages = Message.query.filter_by(recipient_id=current_user.id, is_read=False).all()
    for msg in private_messages:
        sender_username = db.session.get(User, msg.sender_id).username
        unread_counts[sender_username] = unread_counts.get(sender_username, 0) + 1
        
    for group in groups:
         group_unread = Message.query.filter_by(group_id=group.id, is_read=False).filter(Message.sender_id != current_user.id).count()
         if group_unread > 0:
            unread_counts[f'group_{group.id}'] = group_unread

    return render_template('index.html', current_user=current_user, users=users, groups=groups, unread_counts=unread_counts)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return "This username is already taken!"
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
            return "Invalid username or password!"
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
        return "Group name and members are required", 400
    if Group.query.filter_by(name=group_name).first():
        return "A group with this name already exists!", 400
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

@app.route('/group/<int:group_id>')
@login_required
def group_info(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Group not found or you are not a member", 404
    all_users = User.query.all()
    return render_template('group_info.html', group=group, all_users=all_users)

@app.route('/group/<int:group_id>/edit_name', methods=['POST'])
@login_required
def edit_group_name(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Access denied", 403
    new_name = request.form.get('group_name')
    if new_name and (group.name == new_name or not Group.query.filter_by(name=new_name).first()):
        group.name = new_name
        db.session.commit()
    return redirect(url_for('group_info', group_id=group_id))

@app.route('/group/<int:group_id>/edit_members', methods=['POST'])
@login_required
def edit_group_members(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Access denied", 403
    new_member_ids = {int(id) for id in request.form.getlist('members')}
    new_member_ids.add(current_user.id)
    group.members = User.query.filter(User.id.in_(new_member_ids)).all()
    db.session.commit()
    return redirect(url_for('group_info', group_id=group_id))

@app.route('/group/<int:group_id>/delete', methods=['POST'])
@login_required
def delete_group(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Access denied", 403
    Message.query.filter_by(group_id=group_id).delete()
    db.session.delete(group)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/history/<username>')
@login_required
def history(username):
    peer = User.query.filter_by(username=username).first_or_404()
    Message.query.filter_by(sender_id=peer.id, recipient_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    messages = db.session.query(Message).filter(
        or_((Message.sender_id == current_user.id) & (Message.recipient_id == peer.id),
            (Message.sender_id == peer.id) & (Message.recipient_id == current_user.id))
    ).order_by(Message.timestamp.asc()).all()
    
    messages_json = [{
        'sender': msg.author.username, 
        'message': msg.body, 
        'timestamp': msg.timestamp.isoformat() + "Z",
        'audio_url': msg.audio_url,
        'transcription': msg.transcription
    } for msg in messages]
    return jsonify(messages_json)

@app.route('/history/group/<int:group_id>')
@login_required
def group_history(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return "Group not found or you are not a member", 404
    messages = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()
    
    messages_json = [{
        'sender': msg.author.username, 
        'message': msg.body, 
        'timestamp': msg.timestamp.isoformat() + "Z",
        'audio_url': msg.audio_url,
        'transcription': msg.transcription
    } for msg in messages]
    return jsonify(messages_json)

@app.route('/send_audio', methods=['POST'])
@login_required
def send_audio():
    audio_file = request.files.get('audio')
    transcription_text = request.form.get('transcription', '')
    recipient_username = request.form.get('recipient')
    group_id = request.form.get('group_id')

    if not audio_file:
        return jsonify({"error": "No audio file"}), 400
    if not group_id and not recipient_username:
        return jsonify({"error": "No recipient specified"}), 400
    if not os.path.exists('static/uploads'):
        os.makedirs('static/uploads')

    filename = f"{uuid.uuid4()}.webm"
    filepath = os.path.join('static/uploads', filename)
    audio_file.save(filepath)
    
    audio_url = url_for('static', filename=f'uploads/{filename}', _external=True, _scheme='https')
    
    timestamp = datetime.utcnow()
    new_message = Message(
        sender_id=current_user.id,
        timestamp=timestamp,
        audio_url=audio_url,
        transcription=transcription_text
    )

    message_payload = {
        'sender': current_user.username,
        'timestamp': timestamp.isoformat() + "Z",
        'audio_url': audio_url,
        'transcription': transcription_text
    }
    
    try:
        if group_id:
            group = db.session.get(Group, int(group_id))
            if not group or current_user not in group.members:
                return jsonify({"error": "Group not found or access denied"}), 404
            new_message.group_id = group_id
            db.session.add(new_message)
            db.session.commit()
            
            message_payload['group_id'] = group_id
            room = f'group_{group_id}'
            socketio.emit('receive_voice_message', message_payload, to=room)
        
        elif recipient_username:
            recipient_obj = User.query.filter_by(username=recipient_username).first()
            if not recipient_obj:
                return jsonify({"error": "Recipient not found"}), 404
            new_message.recipient_id = recipient_obj.id
            db.session.add(new_message)
            db.session.commit()

            recipient_sid = user_sids.get(recipient_username)
            if recipient_sid:
                socketio.emit('receive_voice_message', message_payload, to=recipient_sid)
            
            sender_sid = user_sids.get(current_user.username)
            if sender_sid:
                socketio.emit('receive_voice_message', message_payload, to=sender_sid)

    except Exception as e:
        db.session.rollback()
        print(f"DATABASE ERROR while saving message: {e}")
        return jsonify({"error": "Database error"}), 500

    return jsonify({"success": True}), 200

@app.route('/edit_with_ai', methods=['POST'])
@login_required
def edit_with_ai():
    data = request.get_json()
    original_text = data.get('text')
    model_choice = data.get('model', 'gemini')

    if not original_text:
        return jsonify({'error': 'No text provided'}), 400

    try:
        edited_text = ""
        prompt = f"""
        You are a helpful chat assistant. Your task is to take the user's transcribed text, understand its core meaning and intent, and improve it.
        - Correct any grammar and spelling mistakes.
        - Improve the style and clarity to make it sound natural and well-written.
        - If the text is a question or an incomplete thought, complete it logically based on the context.
        - Your final response must ONLY be the improved text, with no additional commentary or explanations.

        Original text: "{original_text}"
        """

        if model_choice == 'gemini':
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key: raise ValueError("GEMINI_API_KEY environment variable not set")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            response = model.generate_content(prompt)
            edited_text = response.text
        else: # deepseek
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key: raise ValueError("DEEPSEEK_API_KEY environment variable not set")
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "You are a helpful chat assistant."},
                    {"role": "user", "content": prompt},
                ]
            )
            edited_text = response.choices[0].message.content
        
        return jsonify({'edited_text': edited_text})

    except Exception as e:
        print(f"Error calling {model_choice} API: {e}")
        return jsonify({'error': f'{model_choice} service failed'}), 500

# --- WEBSOCKET LOGIC ---
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
    
    sender_sid = user_sids.get(current_user.username)
    if sender_sid:
        emit('receive_private_message', message_payload, to=sender_sid)


@socketio.on('group_message')
@login_required
def handle_group_message(data):
    group_id = data['group_id']
    message_text = data['message']
    timestamp = datetime.utcnow()
    group = db.session.get(Group, int(group_id))
    if not group or current_user not in group.members:
        return
    new_message = Message(sender_id=current_user.id, group_id=group_id, body=message_text, timestamp=timestamp)
    db.session.add(new_message)
    db.session.commit()
    message_payload = {
        'sender': current_user.username,
        'message': message_text,
        'timestamp': timestamp.isoformat() + "Z",
        'group_id': group_id,
        'group_name': group.name
    }
    room = f'group_{group_id}'
    emit('receive_group_message', message_payload, to=room)
    emit('new_message_notification', {'group_id': group_id, 'group_name': group.name, 'sender': current_user.username}, to=room, skip_sid=request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True)