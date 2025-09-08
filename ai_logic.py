# ai_logic.py

import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import db, Assistant, AssistantMessage, User
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, date
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)

class GoogleTools:
    """Класс-контейнер для всех инструментов, работающих с Google API."""
    def __init__(self, user):
        self.user = user
        # <<< ИСПРАВЛЕНИЕ: Получаем таймзону пользователя один раз
        self.user_tz = getattr(user, 'timezone', 'UTC') 

    def _get_google_service(self, service_name, version):
        if not self.user.google_credentials_json:
            return None
        info = json.loads(self.user.google_credentials_json)
        creds = Credentials.from_authorized_user_info(info)
        return build(service_name, version, credentials=creds)

    def create_calendar_event(self, summary: str, start: str, end: str = None):
        """Создает новое событие в Google Календаре. Время должно быть в формате ISO 8601."""
        service = self._get_google_service('calendar', 'v3')
        if not service: return _("Доступ к Google Календарю не настроен.")
        
        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')) if end else start_dt + timedelta(hours=1)
        except ValueError:
            return _("Не удалось распознать дату. Используйте формат ISO YYYY-MM-DDTHH:MM:SS.")

        # <<< ИСПРАВЛЕНИЕ: Добавляем 'timeZone' в запрос к Google API
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': self.user_tz},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': self.user_tz}
        }
        
        try:
            created_event = service.events().insert(calendarId='primary', body=event).execute()
            return _("✅ Событие '{summary}' успешно создано на {start_time}.").format(
                summary=created_event.get('summary'),
                start_time=start_dt.strftime('%d %B в %H:%M')
            )
        except Exception as e:
            logging.error(f"Error creating calendar event: {e}")
            return _("Не удалось создать событие в календаре.")

    def find_events(self, search_term: str):
        """Находит события в Google Календаре по ключевым словам."""
        service = self._get_google_service('calendar', 'v3')
        if not service: return _("Доступ к Google Календарю не настроен.")
        
        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=7)).isoformat() + "Z"

        try:
            events_result = service.events().list(calendarId='primary', q=search_term, timeMin=time_min, timeMax=time_max, maxResults=5, singleEvents=True, orderBy='startTime').execute()
            events = events_result.get('items', [])
            if not events: return _("На ближайшую неделю событий с названием '{search_term}' не найдено.").format(search_term=search_term)
            
            response_lines = [_("Вот что мне удалось найти:")]
            for event in events:
                start_dt = datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
                response_lines.append(f"- '{event.get('summary')}' ({start_dt.strftime('%d %B в %H:%M')})")
            return "\n".join(response_lines)
        except Exception as e:
            logging.error(f"Error finding events: {e}")
            return _("Произошла ошибка при поиске событий.")

    def create_task(self, title: str, due: str = None):
        """Создает новую задачу в Google Tasks. Срок выполнения (due) должен быть в формате ISO 8601."""
        service = self._get_google_service('tasks', 'v1')
        if not service: return _("Доступ к Google Tasks не настроен.")
        task = {'title': title}
        if due:
            task['due'] = datetime.fromisoformat(due.replace('Z', '+00:00')).isoformat() + "Z"
        try:
            result = service.tasks().insert(tasklist='@default', body=task).execute()
            return _("✅ Задача успешно создана: '{task_title}'").format(task_title=result.get('title'))
        except Exception as e:
            logging.error(f"Error creating task: {e}")
            return _("Не удалось создать задачу.")


def get_specialist_response(user_prompt, user, assistant):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: raise ValueError("GEMINI_API_KEY environment variable not set")
        genai.configure(api_key=api_key)

        if not assistant or assistant.status != 'active':
            return _("Выбранный ассистент не найден или неактивен.")
        
        instructions = assistant.instructions.replace('{{current_date}}', date.today().strftime('%Y-%m-%d'))
        
        history = AssistantMessage.query.filter_by(user_id=user.id).order_by(AssistantMessage.timestamp.desc()).limit(10).all()
        history.reverse()
        chat_history = [{'role': 'user' if msg.role == 'user' else 'model', 'parts': [msg.content]} for msg in history]
        
        google_tools = GoogleTools(user)

        tools_for_model = []
        if any(keyword in assistant.name.lower() for keyword in ['календар', 'события', 'встреча']):
            tools_for_model.extend([google_tools.create_calendar_event, google_tools.find_events])
        if any(keyword in assistant.name.lower() for keyword in ['задачи', 'задач']):
            tools_for_model.append(google_tools.create_task)

        model = genai.GenerativeModel(
            model_name='gemini-1.5-pro-latest',
            system_instruction=instructions,
            tools=tools_for_model
        )
        
        chat = model.start_chat(history=chat_history, enable_automatic_function_calling=True)
        response = chat.send_message(user_prompt)
        
        return response.text

    except Exception as e:
        logging.error(f"Specialist response error: {e}")
        return _('Произошла ошибка в работе ассистента-специалиста.')