# Импортируем Flask для создания веб-приложения и render_template для работы с html
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

# Создаем экземпляр веб-приложения
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-super-secret-key-that-no-one-knows'
# Создаем экземпляр SocketIO
socketio = SocketIO(app)

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
    # Запускаем сервер для локальной разработки
    socketio.run(app, debug=True)