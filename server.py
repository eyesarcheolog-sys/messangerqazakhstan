# Импортируем Flask для создания веб-приложения и render_template для работы с html
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
# Импортируем SQLAlchemy
from flask_sqlalchemy import SQLAlchemy

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ И БАЗЫ ДАННЫХ ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-super-secret-key-that-no-one-knows'
# Указываем путь к файлу нашей базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///messenger.db'
db = SQLAlchemy(app)
socketio = SocketIO(app)


# --- МОДЕЛЬ ПОЛЬЗОВАТЕЛЯ ---
# Описываем, как будет выглядеть таблица пользователей в базе данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

    def __repr__(self):
        return '<User %r>' % self.username


# --- СТАРЫЙ КОД ОСТАЕТСЯ НИЖЕ ---

# Когда пользователь заходит на главную страницу...
@app.route('/')
def index():
    # ...мы отправляем ему файл index.html из папки 'templates'
    return render_template('index.html')

# Когда сервер получает от клиента событие 'send_message'...
@socketio.on('send_message')
def handle_message(data):
    # ...мы печатаем его в консоль сервера...
    print('Получено сообщение: ' + str(data))
    # ...и отправляем его обратно всем подключенным клиентам
    emit('receive_message', data, broadcast=True)

# Эта часть нужна, только если мы запускаем файл напрямую (python server.py)
if __name__ == '__main__':
    # Перед первым запуском нужно создать таблицы в базе данных
    with app.app_context():
        db.create_all()
    # Запускаем сервер для локальной разработки
    socketio.run(app, debug=True)