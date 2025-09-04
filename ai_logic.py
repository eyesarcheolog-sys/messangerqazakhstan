import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import Assistant
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

def create_calendar_event(user, event_data_json):
    """
    Создает событие в Google Календаре пользователя.
    :param user: Объект текущего пользователя (для получения credentials).
    :param event_data_json: Строка JSON от ИИ-ассистента.
    :return: Строка с результатом операции.
    """
    if not user.google_credentials_json:
        return _("Ошибка: Google Календарь не подключен. Пожалуйста, подключите его в настройках ассистента.")

    try:
        # 1. Загружаем учетные данные из базы данных
        creds_data = json.loads(user.google_credentials_json)
        creds = Credentials(**creds_data)

        # 2. Создаем клиент для работы с API
        service = build('calendar', 'v3', credentials=creds)

        # 3. Парсим JSON от ассистента
        event_data = json.loads(event_data_json)

        # 4. Формируем тело запроса для API
        event = {
            'summary': event_data.get('summary', 'Без названия'),
            'start': {
                'dateTime': event_data.get('start'),
                'timeZone': 'Asia/Makassar', #ВАЖНО: Укажите ваш часовой пояс. Например, 'Europe/Amsterdam' или 'Asia/Almaty'
            },
            'end': {
                'dateTime': event_data.get('end'),
                'timeZone': 'Asia/Makassar', #ВАЖНО: Укажите ваш часовой пояс
            },
        }

        # 5. Отправляем запрос на создание события
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        return _("✅ Событие успешно создано! '{event_summary}'").format(event_summary=created_event.get('summary'))

    except json.JSONDecodeError:
        return _("Извините, не удалось распознать данные для события. Попробуйте переформулировать запрос.")
    except Exception as e:
        print(f"Google Calendar API error: {e}")
        return _("Произошла ошибка при работе с Google Календарем: {error}").format(error=e)


def get_orchestrated_ai_response(user_prompt, user):
    """
    Эта функция-оркестратор управляет взаимодействием с ИИ.
    Шаг 1: Выбирает подходящего ассистента.
    Шаг 2: Получает ответ от выбранного ассистента.
    """
    available_assistants = Assistant.query.filter_by(user_id=user.id).all()

    # Фильтруем ассистентов, чтобы убрать тех, у кого нет инструкций
    active_assistants = [a for a in available_assistants if a.instructions]

    if not active_assistants:
        return _("У вас пока нет настроенных ассистентов с инструкциями.")

    assistant_list_for_prompt = "\n".join(
        [f"- id: {a.id}, name: {a.name}, description: {a.description}" for a in active_assistants]
    )

    selection_prompt = f"""
    Ты — главный ассистент-диспетчер. Проанализируй запрос пользователя и выбери ОДНОГО из доступных специалистов из списка ниже, который лучше всего подходит для выполнения задачи.
    В ответ дай ТОЛЬКО цифру — id выбранного специалиста. Не добавляй никаких других слов, текста или знаков препинания.

    Список доступных специалистов:
    {assistant_list_for_prompt}

    Запрос пользователя: "{user_prompt}"
    """

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: raise ValueError("GEMINI_API_KEY is not set")
        genai.configure(api_key=api_key)
        
        selection_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        selection_response = selection_model.generate_content(selection_prompt)
        
        selected_assistant_id_str = ''.join(filter(str.isdigit, selection_response.text))
        if not selected_assistant_id_str:
            raise ValueError("AI did not return a valid numeric ID.")
        selected_assistant_id = int(selected_assistant_id_str)
        
        specialist_assistant = Assistant.query.filter_by(id=selected_assistant_id, user_id=user.id).first()

        if not specialist_assistant or not specialist_assistant.instructions:
            return _("Ассистент с ID {assistant_id} не найден или не имеет инструкций.").format(assistant_id=selected_assistant_id)

        # Проверяем, является ли выбранный ассистент специалистом по календарю
        if 'календар' in specialist_assistant.name.lower():
            
            # Подставляем текущую дату в инструкции
            today_date = datetime.now().strftime("%Y-%m-%d")
            instructions_with_date = specialist_assistant.instructions.replace("{{current_date}}", today_date)

            final_model = genai.GenerativeModel(
                'gemini-1.5-flash-latest',
                system_instruction=instructions_with_date
            )
            # Получаем от ИИ JSON-ответ
            json_response = final_model.generate_content(user_prompt)
            
            # Вызываем нашу новую функцию для создания события
            return create_calendar_event(user, json_response.text)

        else:
            # Для всех остальных ассистентов логика остается прежней
            final_model = genai.GenerativeModel(
                'gemini-1.5-flash-latest',
                system_instruction=specialist_assistant.instructions
            )
            final_response = final_model.generate_content(user_prompt)
            return final_response.text

    except (ValueError, IndexError, TypeError) as e:
        print(f"Orchestrator Error: Could not parse assistant ID from response. Details: {e}")
        return _("Извините, не удалось выбрать подходящего ассистента. Попробуйте переформулировать.")
    except Exception as e:
        print(f"Orchestrator general error: {e}")
        return _('AI Assistant service failed')