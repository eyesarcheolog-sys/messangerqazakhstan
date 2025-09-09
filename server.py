# server.py (Финальная, исправленная версия)

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

# --- СВЯЗЫВАНИЕ РАСШИРЕНИЙ С ПРИЛОЖЕНИЕМ ---
socketio = SocketIO(app)
login_manager = LoginManager(app)
babel = Babel(app)
db.init_app(app)
migrate = Migrate(app, db)

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

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return _("This username is already taken!")
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
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            return _("Invalid username or password!")
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
        return _("Group name and members are required"), 400
    if Group.query.filter_by(name=group_name).first():
        return _("A group with this name already exists!"), 400
    new_group = Group(name=group_name)
    db.session.add(new_group)
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
        return _("Group not found or you are not a member"), 404
    all_users = User.query.all()
    return render_template('group_info.html', group=group, all_users=all_users)

@app.route('/group/<int:group_id>/edit_name', methods=['POST'])
@login_required
def edit_group_name(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return _("Access denied"), 403
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
        return _("Access denied"), 403
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
        return _("Access denied"), 403
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
    messages = db.session.query(Message).filter(or_((Message.sender_id == current_user.id) & (Message.recipient_id == peer.id), (Message.sender_id == peer.id) & (Message.recipient_id == current_user.id))).order_by(Message.timestamp.asc()).all()
    messages_json = [{'sender': msg.author.username, 'message': msg.body, 'timestamp': msg.timestamp.isoformat() + "Z", 'audio_url': msg.audio_url, 'transcription': msg.transcription} for msg in messages]
    return jsonify(messages_json)

@app.route('/history/group/<int:group_id>')
@login_required
def group_history(group_id):
    group = db.session.get(Group, group_id)
    if not group or current_user not in group.members:
        return _("Group not found or you are not a member"), 404
    messages = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()
    messages_json = [{'sender': msg.author.username, 'message': msg.body, 'timestamp': msg.timestamp.isoformat() + "Z", 'audio_url': msg.audio_url, 'transcription': msg.transcription} for msg in messages]
    return jsonify(messages_json)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    with app.app_context():
        return send_from_directory(os.path.join(app.root_path, 'uploads'), filename)

@app.route('/send_audio', methods=['POST'])
@login_required
def send_audio():
    audio_file = request.files.get('audio')
    transcription_text = request.form.get('transcription', '')
    recipient_username = request.form.get('recipient')
    group_id = request.form.get('group_id')
    if not audio_file: return jsonify({"error": _("No audio file")}), 400
    if not group_id and not recipient_username: return jsonify({"error": _("No recipient specified")}), 400
    upload_dir = os.path.join(app.root_path, 'uploads')
    if not os.path.exists(upload_dir): os.makedirs(upload_dir)
    filename = f"{uuid.uuid4()}.webm"
    filepath = os.path.join(upload_dir, filename)
    audio_file.save(filepath)
    audio_url = url_for('uploaded_file', filename=filename, _external=True, _scheme='https')
    timestamp = datetime.utcnow()
    new_message = Message(sender_id=current_user.id, timestamp=timestamp, audio_url=audio_url, transcription=transcription_text)
    message_payload = {'sender': current_user.username, 'timestamp': timestamp.isoformat() + "Z", 'audio_url': audio_url, 'transcription': transcription_text}
    try:
        if group_id:
            group = db.session.get(Group, int(group_id))
            if not group or current_user not in group.members: return jsonify({"error": _("Group not found or access denied")}), 404
            new_message.group_id = group_id
            db.session.add(new_message)
            db.session.commit()
            message_payload['group_id'] = group_id
            room = f'group_{group_id}'
            socketio.emit('receive_voice_message', message_payload, to=room)
        elif recipient_username:
            recipient_obj = User.query.filter_by(username=recipient_username).first()
            if not recipient_obj: return jsonify({"error": _("Recipient not found")}), 404
            new_message.recipient_id = recipient_obj.id
            db.session.add(new_message)
            db.session.commit()
            recipient_sid = user_sids.get(recipient_username)
            if recipient_sid: socketio.emit('receive_voice_message', message_payload, to=recipient_sid)
            sender_sid = user_sids.get(current_user.username)
            if sender_sid: socketio.emit('receive_voice_message', message_payload, to=sender_sid)
    except Exception as e:
        db.session.rollback()
        print(f"DATABASE ERROR while saving message: {e}")
        return jsonify({"error": _("Database error")}), 500
    return jsonify({"success": True}), 200

@app.route('/edit_with_ai', methods=['POST'])
@login_required
def edit_with_ai():
    data = request.get_json()
    original_text = data.get('text')
    model_choice = data.get('model', 'gemini')
    task_type = data.get('task_type', 'generate')
    if not original_text: return jsonify({'error': _('No text provided')}), 400
    try:
        edited_text = ""
        if task_type == 'improve':
            prompt = f"""
            Ты — умный ассистент-редактор. Твоя задача — взять текст пользователя и улучшить его.
            - Исправь все орфографические, пунктуационные и грамматические ошибки.
            - Улучши стиль и ясность, чтобы текст звучал естественно и грамотно.
            - **Не меняй основной смысл текста и не добавляй новой информации от себя.**
            - Твой ответ ВСЕГДА должен быть на том же языке, что и оригинальный текст.
            - ФОРМАТ ОТВЕТА: Только итоговый, отредактированный текст, без твоих комментариев.

            Оригинальный текст: "{original_text}"
            """
        else:
            prompt = original_text
        if model_choice == 'gemini':
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key: raise ValueError("GEMINI_API_KEY environment variable not set")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest', system_instruction="Ты — полезный ИИ-ассистент в чате. Отвечай на русском языке, если не указано иное.")
            response = model.generate_content(prompt)
            try: edited_text = response.text
            except ValueError:
                print("Gemini response blocked by safety settings.")
                edited_text = "[Ответ был заблокирован из-за настроек безопасности]"
        else:
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key: raise ValueError("DEEPSEEK_API_KEY environment variable not set")
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
            response = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "system", "content": "You are a helpful AI assistant. Respond in Russian unless the user asks for another language."}, {"role": "user", "content": prompt}])
            edited_text = response.choices[0].message.content
        return jsonify({'edited_text': edited_text})
    except Exception as e:
        print(f"Error calling {model_choice} API: {e}")
        return jsonify({'error': _('{model_choice} service failed').format(model_choice=model_choice)}), 500

@app.route('/assistant_history')
@login_required
def assistant_history():
    messages = AssistantMessage.query.filter_by(user_id=current_user.id).order_by(AssistantMessage.timestamp.asc()).all()
    history = [{'role': msg.role, 'content': msg.content} for msg in messages]
    return jsonify(history)

@app.route('/chat_with_assistant', methods=['POST'])
@login_required
def chat_with_assistant():
    data = request.get_json()
    user_prompt = data.get('prompt')
    if not user_prompt: return jsonify({'error': _('No prompt provided')}), 400
    try:
        user_message = AssistantMessage(user_id=current_user.id, role='user', content=user_prompt)
        db.session.add(user_message)
        db.session.flush()
        specialists = Assistant.query.filter_by(user_id=current_user.id, status='active').all()
        if not specialists:
            final_response = _('У вас нет активных ассистентов-специалистов. Пожалуйста, создайте и активируйте одного в панели управления.')
        else:
            specialist_list_for_prompt = "\n".join([f"- id: {s.id}, name: {s.name}, description: {s.description}" for s in specialists])
            orchestrator_prompt = f"""
            Ты — главный ассистент-диспетчер. Твоя задача — проанализировать запрос пользователя и выбрать ОДНОГО наиболее подходящего специалиста из списка ниже.
            В своем ответе ты должен указать ТОЛЬКО ID выбранного специалиста в формате "id: <число>". Никаких других слов или объяснений.

            Доступные специалисты:
            {specialist_list_for_prompt}

            Запрос пользователя: "{user_prompt}"
            """
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            orchestrator_model = genai.GenerativeModel('gemini-1.5-flash-latest')
            orchestrator_response = orchestrator_model.generate_content(orchestrator_prompt)
            try:
                response_text = orchestrator_response.text
                specialist_id_str = response_text.split('id:')[1].strip()
                specialist_id = int(specialist_id_str)
                specialist_assistant = db.session.get(Assistant, specialist_id)
                if specialist_assistant and specialist_assistant.user_id == current_user.id:
                    final_response = get_specialist_response(user_prompt, current_user, specialist_assistant)
                else:
                    final_response = _("Диспетчер выбрал несуществующего или чужого ассистента.")
            except (IndexError, ValueError, AttributeError):
                print(f"Orchestrator did not return a valid ID. Response: {orchestrator_response.text}")
                general_model = genai.GenerativeModel('gemini-1.5-pro-latest')
                response = general_model.generate_content(user_prompt)
                final_response = response.text
        assistant_response = AssistantMessage(user_id=current_user.id, role='assistant', content=final_response)
        db.session.add(assistant_response)
        db.session.commit()
        return jsonify({'response': final_response})
    except Exception as e:
        db.session.rollback()
        print(f"Error in chat_with_assistant route: {e}")
        return jsonify({'error': _('AI Assistant service failed')}), 500

@app.route('/js/translations.js')
def js_translations():
    translations = {
        "Please select a chat.": _("Please select a chat."),
        "Microphone error:": _("Microphone error:"),
        "AI Error:": _("AI Error:"),
        "An error occurred while contacting the AI.": _("An error occurred while contacting the AI."),
        "A network error has occurred. Please try again.": _("A network error has occurred. Please try again."),
        "Could not get a response from the AI.": _("Could not get a response from the AI."),
        "Recording: {seconds} sec.": _("Recording: {seconds} sec."),
        "Recording finished": _("Recording finished"),
        "Transcription ready": _("Transcription ready"),
        "Press 'Start' to begin recording": _("Press 'Start' to begin recording"),
        "AI is working...": _("AI is working..."),
        "Thinking...": _("Thinking..."),
        "Show text": _("Show text"),
        "Hide text": _("Hide text"),
        "Chat with {name}": _("Chat with {name}"),
        "Select a chat": _("Выберите чат")
    }
    js_code = f"window.translations = {json.dumps(translations)};"
    return Response(js_code, mimetype='application/javascript')

# --- ASSISTANTS ROUTES ---
@app.route('/assistants')
@login_required
def assistants_dashboard():
    user_assistants = Assistant.query.filter_by(user_id=current_user.id).all()
    return render_template('assistants.html', assistants=user_assistants)

@app.route('/assistants/create', methods=['GET'])
@login_required
def create_assistant():
    new_assistant = Assistant(name=_('Новый ассистент'), description=_('Краткое описание'), status='inactive', instructions=_('Ты — полезный ассистент.'), user_id=current_user.id)
    db.session.add(new_assistant)
    db.session.commit()
    return redirect(url_for('configure_assistant', assistant_id=new_assistant.id))

@app.route('/assistants/configure/<int:assistant_id>', methods=['GET', 'POST'])
@login_required
def configure_assistant(assistant_id):
    assistant = Assistant.query.filter_by(id=assistant_id, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        assistant.name = request.form.get('assistant_name')
        assistant.description = request.form.get('assistant_description')
        assistant.instructions = request.form.get('instructions')
        assistant.status = request.form.get('assistant_status')
        selected_tools = request.form.getlist('tools')
        assistant.tools = ','.join(selected_tools) if selected_tools else ''
        db.session.commit()
        return redirect(url_for('configure_assistant', assistant_id=assistant.id))
    return render_template('configure_assistant.html', assistant=assistant)

@app.route('/assistants/delete/<int:assistant_id>', methods=['POST'])
@login_required
def delete_assistant(assistant_id):
    assistant = Assistant.query.filter_by(id=assistant_id, user_id=current_user.id).first_or_404()
    db.session.delete(assistant)
    db.session.commit()
    return redirect(url_for('assistants_dashboard'))

@app.route('/assistants/my')
@login_required
def my_assistants_page():
    user_assistants = Assistant.query.filter_by(user_id=current_user.id).all()
    return render_template('my_assistants.html', assistants=user_assistants)

@app.route('/assistants/knowledge')
@login_required
def knowledge_base_page():
    return render_template('knowledge_base.html')

@app.route('/assistants/settings')
@login_required
def assistants_settings_page():
    return render_template('settings.html')

@app.route('/assistants/disconnect_google', methods=['POST'])
@login_required
def disconnect_google():
    user = db.session.get(User, current_user.id)
    user.google_credentials_json = None
    db.session.commit()
    return redirect(request.referrer or url_for('assistants_dashboard'))

# --- GOOGLE OAUTH ROUTES ---
@app.route('/authorize/google')
@login_required
def authorize_google():
    render_credentials_path = '/etc/secrets/google_credentials.json'
    local_credentials_path = os.path.join(app.root_path, 'google_credentials.json')
    if os.path.exists(render_credentials_path):
        credentials_path = render_credentials_path
    else:
        credentials_path = local_credentials_path
    flow = Flow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/calendar.events', 'https://www.googleapis.com/auth/tasks'], redirect_uri=url_for('oauth2callback_google', _external=True, _scheme='https'))
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback/google')
@login_required
def oauth2callback_google():
    render_credentials_path = '/etc/secrets/google_credentials.json'
    local_credentials_path = os.path.join(app.root_path, 'google_credentials.json')
    if os.path.exists(render_credentials_path):
        credentials_path = render_credentials_path
    else:
        credentials_path = local_credentials_path
    state = session.get('state')
    if not state or state != request.args.get('state'): return 'State mismatch error', 400
    flow = Flow.from_client_secrets_file(credentials_path, scopes=['https://www.googleapis.com/auth/calendar.events', 'https://www.googleapis.com/auth/tasks'], state=state, redirect_uri=url_for('oauth2callback_google', _external=True, _scheme='https'))
    flow.fetch_token(authorization_response=request.url)
    session.pop('state', None)
    credentials = flow.credentials
    current_user.google_credentials_json = credentials.to_json()
    db.session.commit()
    return redirect(url_for('assistants_dashboard'))

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
        if user_sids.get(current_user.username) == request.sid:
            del user_sids[current_user.username]
        emit('update_online_users', list(user_sids.keys()), broadcast=True)

@socketio.on('private_message')
@login_required
def handle_private_message(data):
    recipient_username = data['recipient']
    message_text = data['message']
    timestamp = datetime.utcnow()
    recipient_obj = User.query.filter_by(username=recipient_username).first()
    if not recipient_obj: return
    new_message = Message(sender_id=current_user.id, recipient_id=recipient_obj.id, body=message_text, timestamp=timestamp)
    db.session.add(new_message)
    db.session.commit()
    recipient_sid = user_sids.get(recipient_username)
    message_payload = {'sender': current_user.username, 'recipient': recipient_username, 'message': message_text, 'timestamp': timestamp.isoformat() + "Z"}
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
    if not group or current_user not in group.members: return
    new_message = Message(sender_id=current_user.id, group_id=group_id, body=message_text, timestamp=timestamp)
    db.session.add(new_message)
    db.session.commit()
    message_payload = {'sender': current_user.username, 'message': message_text, 'timestamp': timestamp.isoformat() + "Z", 'group_id': group_id, 'group_name': group.name}
    room = f'group_{group_id}'
    emit('receive_group_message', message_payload, to=room)
    emit('new_message_notification', {'group_id': group_id, 'group_name': group.name, 'sender': current_user.username}, to=room, skip_sid=request.sid)

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == '__main__':
    socketio.run(app, debug=True)