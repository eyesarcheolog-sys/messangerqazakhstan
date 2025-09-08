# ai_logic.py

import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import db, Assistant, AssistantMessage, User
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, date
from pytz import timezone as pytz_timezone, utc
import logging
from functools import partial

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# --- Инструменты для Gemini (эти функции не изменились) ---
def get_google_service(user, service_name, version):
    if not user.google_credentials_json:
        return None
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    return build(service_name, version, credentials=creds)

def find_events(user, search_term):
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    now = datetime.now(utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=7)).isoformat()
    try:
        events_result = service.events().list(
            calendarId='primary',
            q=search_term,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        if not events:
            return _("На ближайшую неделю событий с названием '{search_term}' не найдено.").format(search_term=search_term)
        
        response_lines = [_("Вот что мне удалось найти:")]
        for event in events:
            start_str = event['start'].get('dateTime', event['start'].get('date'))
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            formatted_start = start_dt.strftime('%d %B в %H:%M')
            summary = event.get('summary', _('Без названия'))
            event_id = event['id']
            response_lines.append(f"- '{summary}' ({formatted_start}) - ID: {event_id}")
        
        return "\n".join(response_lines)
    except Exception as e:
        logging.error(f"Error finding events: {e}")
        return _("Произошла ошибка при поиске событий.")

def find_and_delete_event(user, event_id):
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        summary = event.get('summary', _('Без названия'))
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return _("✅ Событие '{summary}' успешно удалено!").format(summary=summary)
    except Exception as e:
        logging.error(f"Error deleting event with ID {event_id}: {e}")
        return _("Не удалось удалить событие. Пожалуйста, скопируйте и вставьте ID полностью.")

def find_and_update_event(user, event_id, new_start, new_end=None):
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    try:
        event_to_update = service.events().get(calendarId='primary', eventId=event_id).execute()
        event_to_update['start']['dateTime'] = new_start
        if new_end:
            event_to_update['end']['dateTime'] = new_end
        updated_event = service.events().update(calendarId='primary', eventId=event_to_update['id'], body=event_to_update).execute()
        return _("✅ Событие '{summary}' успешно перенесено!").format(summary=updated_event.get('summary'))
    except Exception as e:
        logging.error(f"Error updating event with ID {event_id}: {e}")
        return _("Не удалось обновить событие. Пожалуйста, скопируйте и вставьте ID полностью.")

def create_task(user, title, due=None):
    service = get_google_service(user, 'tasks', 'v1')
    if not service: return _("Доступ к Google Tasks не настроен.")
    task = {'title': title}
    if due:
        try:
            due_dt_object = datetime.fromisoformat(due.replace('Z', '+00:00'))
            task['due'] = due_dt_object.isoformat()
        except ValueError:
            logging.warning(f"Could not parse due date '{due}', creating task without it.")

    try:
        result = service.tasks().insert(tasklist='@default', body=task).execute()
        return _("✅ Задача успешно создана: '{task_title}'").format(task_title=result.get('title'))
    except Exception as e:
        logging.error(f"Error creating task: {e}")
        return _("Не удалось создать задачу.")

def create_calendar_event(user, summary, start, end=None):
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    
    user_tz_str = getattr(user, 'timezone', 'UTC')
    
    try:
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
        if end:
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
        else:
            end_dt = start_dt + timedelta(hours=1)
    except ValueError:
        return _("Не удалось распознать дату или время. Пожалуйста, укажите их в формате ISO (YYYY-MM-DDTHH:MM:SS).")

    event = {
        'summary': summary,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': user_tz_str},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': user_tz_str},
    }
    
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return _("✅ Событие успешно создано: '{summary}' на {start_time}.").format(
            summary=created_event.get('summary'),
            start_time=start_dt.strftime('%d %B в %H:%M')
        )
    except Exception as e:
        logging.error(f"Error creating calendar event: {e}")
        return _("Не удалось создать событие в календаре.")

# <<< ГЛАВНАЯ ФУНКЦИЯ-СПЕЦИАЛИСТ, ПОЛНОСТЬЮ ПЕРЕРАБОТАНА >>>
def get_specialist_response(user_prompt, user, assistant):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        genai.configure(api_key=api_key)

        if not assistant or assistant.status != 'active':
            return _("Выбранный ассистент не найден или неактивен.")
        
        # 1. Подставляем текущую дату в инструкции
        instructions = assistant.instructions.replace('{{current_date}}', date.today().strftime('%Y-%m-%d'))
        
        # 2. Загружаем историю чата
        last_messages = AssistantMessage.query.filter_by(user_id=user.id).order_by(AssistantMessage.timestamp.desc()).limit(10).all()
        last_messages.reverse()
        history = [{'role': msg.role, 'parts': [msg.content]} for msg in last_messages]
        
        # 3. "Прячем" аргумент user от модели с помощью partial
        # Это решает ошибку "multiple values for keyword argument 'user'"
        available_tools = {
            "create_calendar_event": partial(create_calendar_event, user),
            "find_events": partial(find_events, user),
            "find_and_delete_event": partial(find_and_delete_event, user),
            "create_task": partial(create_task, user),
        }
        
        tools_for_model = []
        if any(keyword in assistant.name.lower() for keyword in ['календар', 'события', 'встреча']):
            tools_for_model.extend([
                available_tools["create_calendar_event"], 
                available_tools["find_events"], 
                available_tools["find_and_delete_event"]
            ])
        if any(keyword in assistant.name.lower() for keyword in ['задачи', 'задач']):
            tools_for_model.append(available_tools["create_task"])

        model = genai.GenerativeModel(
            'gemini-1.5-pro-latest', 
            system_instruction=instructions, 
            tools=tools_for_model
        )
        
        # 4. Включаем автоматический вызов функций - это более надежный способ
        # Он решает ошибку "Could not convert part.function_call to text"
        chat = model.start_chat(history=history, enable_automatic_function_calling=True)
        response = chat.send_message(user_prompt)
        
        return response.text

    except Exception as e:
        logging.error(f"Specialist response error: {e}")
        return _('Произошла ошибка в работе ассистента-специалиста.')