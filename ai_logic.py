# ai_logic.py

import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import db, Assistant, AssistantMessage, User
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from pytz import timezone as pytz_timezone, utc
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# --- Вспомогательные функции (теперь это "инструменты" для Gemini) ---
def get_google_service(user, service_name, version):
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    return build(service_name, version, credentials=creds)

def find_events(user, search_term):
    service = get_google_service(user, 'calendar', 'v3')
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
    task = {'title': title}
    if due:
        due_dt_object = datetime.fromisoformat(due)
        task['due'] = due_dt_object.strftime('%Y-%m-%dT%H:%M:%S') + ".000Z"
    try:
        result = service.tasks().insert(tasklist='@default', body=task).execute()
        return _("✅ Задача успешно создана: '{task_title}'").format(task_title=result.get('title'))
    except Exception as e:
        logging.error(f"Error creating task: {e}")
        return _("Не удалось создать задачу.")

def create_calendar_event(user, summary, start, end=None):
    service = get_google_service(user, 'calendar', 'v3')
    
    user_tz_str = getattr(user, 'timezone', None) or 'UTC'
    
    event = {
        'summary': summary,
        'start': {'dateTime': start, 'timeZone': user_tz_str},
        'end': {'dateTime': end or (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(), 'timeZone': user_tz_str},
        'colorId': '1'
    }
    
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return _("✅ Событие успешно создано: '{summary}' на {start_time}.").format(
            summary=created_event.get('summary'),
            start_time=datetime.fromisoformat(start).strftime('%d %B в %H:%M')
        )
    except Exception as e:
        logging.error(f"Error creating calendar event: {e}")
        return _("Не удалось создать событие в календаре. Пожалуйста, убедитесь, что вы предоставили корректное время.")


def get_orchestrated_ai_response(user_prompt, user, assistant_id):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        genai.configure(api_key=api_key)

        # 1. Получаем историю чата
        last_messages = AssistantMessage.query.filter_by(user_id=user.id).order_by(AssistantMessage.timestamp.desc()).limit(10).all()
        last_messages.reverse()
        history = [
            {'role': 'user' if msg.role == 'user' else 'model', 'parts': [msg.content]}
            for msg in last_messages
        ]

        # 2. Получаем инструкции ассистента из базы данных
        assistant = Assistant.query.filter_by(id=assistant_id, user_id=user.id).first()
        if not assistant or not assistant.instructions:
            return _("У этого ассистента нет инструкций. Пожалуйста, настройте его в панели управления.")

        # 3. Определяем инструменты, доступные этому ассистенту
        # Этот словарь будет расти по мере добавления новых мини-ассистентов
        available_tools = {
            "create_calendar_event": genai.FunctionDeclaration(
                name="create_calendar_event",
                description="Создает новое событие в Google Календаре. Принимает название, время начала и опционально время окончания.",
                parameters=genai.Schema(
                    type=genai.Schema.Type.OBJECT,
                    properties={
                        "summary": genai.Schema(type=genai.Schema.Type.STRING),
                        "start": genai.Schema(type=genai.Schema.Type.STRING),
                        "end": genai.Schema(type=genai.Schema.Type.STRING),
                    },
                    required=["summary", "start"],
                ),
            ),
            "find_events": genai.FunctionDeclaration(
                name="find_events",
                description="Находит события в Google Календаре по ключевым словам. Используется для поиска встреч или задач.",
                parameters=genai.Schema(
                    type=genai.Schema.Type.OBJECT,
                    properties={
                        "search_term": genai.Schema(type=genai.Schema.Type.STRING),
                    },
                    required=["search_term"],
                ),
            ),
            "find_and_delete_event": genai.FunctionDeclaration(
                name="find_and_delete_event",
                description="Удаляет событие из Google Календаря по его уникальному ID.",
                parameters=genai.Schema(
                    type=genai.Schema.Type.OBJECT,
                    properties={
                        "event_id": genai.Schema(type=genai.Schema.Type.STRING),
                    },
                    required=["event_id"],
                ),
            ),
            "create_task": genai.FunctionDeclaration(
                name="create_task",
                description="Создает новую задачу в Google Tasks. Требует название задачи.",
                parameters=genai.Schema(
                    type=genai.Schema.Type.OBJECT,
                    properties={
                        "title": genai.Schema(type=genai.Schema.Type.STRING),
                        "due": genai.Schema(type=genai.Schema.Type.STRING),
                    },
                    required=["title"],
                ),
            ),
        }

        # 4. Выбираем инструменты на основе имени ассистента
        tools_for_model = []
        if 'календар' in assistant.name.lower() or 'события' in assistant.name.lower():
            tools_for_model.append(available_tools["create_calendar_event"])
            tools_for_model.append(available_tools["find_events"])
            tools_for_model.append(available_tools["find_and_delete_event"])
        if 'задачи' in assistant.name.lower():
            tools_for_model.append(available_tools["create_task"])

        # 5. Инициализируем модель с инструкциями и инструментами
        model = genai.GenerativeModel('gemini-1.5-pro-latest', 
                                    system_instruction=assistant.instructions, 
                                    tools=tools_for_model)
        
        chat = model.start_chat(history=history)
        response = chat.send_message(user_prompt)

        # 6. Обрабатываем ответ ИИ
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            tool_name = tool_call.name
            tool_args = {k: v for k, v in tool_call.args.items()}
            tool_response = locals()[tool_name](user, **tool_args)
            return tool_response
        else:
            return response.text

    except Exception as e:
        logging.error(f"Orchestrator general error: {e}")
        return _('Произошла ошибка в работе ассистента.')