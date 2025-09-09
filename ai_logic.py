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
from google.generativeai import protos

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# --- Функции-инструменты ---

def get_google_service(user, service_name, version):
    if not user.google_credentials_json:
        return None
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    return build(service_name, version, credentials=creds)

def create_calendar_event(user, summary: str, start: str, end: str = None):
    """Создает новое событие в Google Календаре. Время должно быть в формате ISO 8601."""
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    
    try:
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')) if end else start_dt + timedelta(hours=1)
    except ValueError:
        return _("Не удалось распознать дату. Используйте формат ISO YYYY-MM-DDTHH:MM:SS.")

    event = {
        'summary': summary,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'UTC'},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'UTC'}
    }
    
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        logging.info(f"Event created: {created_event.get('htmlLink')}")
        return _("✅ Событие '{summary}' успешно создано.").format(summary=created_event.get('summary'))
    except Exception as e:
        logging.error(f"Error creating calendar event: {e}")
        return _("Не удалось создать событие в календаре.")

def find_events(user, search_term: str):
    """Находит события в Google Календаре по ключевым словам."""
    service = get_google_service(user, 'calendar', 'v3')
    if not service: return _("Доступ к Google Календарю не настроен.")
    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(days=7)).isoformat() + "Z"
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
        if not events: return _("На ближайшую неделю событий с названием '{search_term}' не найдено.").format(search_term=search_term)
        
        response_lines = [_("Вот что мне удалось найти:")]
        for event in events:
            start_dt = datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
            response_lines.append(f"- '{event.get('summary')}' ({start_dt.strftime('%d %B в %H:%M')})")
        return "\n".join(response_lines)
    except Exception as e:
        logging.error(f"Error finding events: {e}")
        return _("Произошла ошибка при поиске событий.")

def create_task(user, title: str, due: str = None):
    """Создает новую задачу в Google Tasks. Срок выполнения (due) должен быть в формате ISO 8601."""
    service = get_google_service(user, 'tasks', 'v1')
    if not service: return _("Доступ к Google Tasks не настроен.")
    task = {'title': title}
    if due:
        task['due'] = datetime.fromisoformat(due.replace('Z', '+00:00')).isoformat() + "Z"
    try:
        result = service.tasks().insert(tasklist='@default', body=task).execute()
        logging.info(f"Task created: {result.get('id')}")
        return _("✅ Задача '{title}' успешно создана.").format(title=result.get('title'))
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
        
        tools = {
            "create_calendar_event": create_calendar_event,
            "find_events": find_events,
            "create_task": create_task
        }
        
        model = genai.GenerativeModel(
            model_name='gemini-1.5-pro-latest',
            system_instruction=instructions
        )
        chat = model.start_chat(history=chat_history)
        
        # Передаем сами функции в send_message, чтобы библиотека сгенерировала описание
        tools_for_model_call = []
        if any(keyword in assistant.name.lower() for keyword in ['календарь', 'события', 'встреча']):
             tools_for_model_call.extend([tools["create_calendar_event"], tools["find_events"]])
        if any(keyword in assistant.name.lower() for keyword in ['задачи', 'задач']):
             tools_for_model_call.append(tools["create_task"])
        
        response = chat.send_message(user_prompt, tools=tools_for_model_call)

        if response.candidates and response.candidates[0].content.parts and response.candidates[0].content.parts[0].function_call:
            function_call = response.candidates[0].content.parts[0].function_call
            tool_name = function_call.name
            
            if tool_name in tools:
                executor = tools[tool_name]
                tool_args = {key: value for key, value in function_call.args.items()}
                
                tool_response_text = executor(user=user, **tool_args)
                
                function_response = protos.Part(
                    function_response=protos.FunctionResponse(
                        name=tool_name,
                        response={'result': tool_response_text}
                    )
                )
                
                final_response = chat.send_message(function_response)
                return final_response.text
            else:
                return _("Модель попыталась вызвать неизвестный инструмент.")
        else:
            return response.text

    except Exception as e:
        logging.error(f"Specialist response error: {e}")
        return _('Произошла ошибка в работе ассистента-специалиста.')