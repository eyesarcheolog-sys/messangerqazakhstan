import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import Assistant, AssistantMessage # <-- Убедитесь, что AssistantMessage импортирован
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone

# --- Все вспомогательные функции (get_google_service, find_events, и т.д.) остаются без изменений ---
def get_google_service(user, service_name, version):
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    return build(service_name, version, credentials=creds)

def find_events(user, data):
    service = get_google_service(user, 'calendar', 'v3')
    search_term = data.get('search_term')
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=7)).isoformat()
    events_result = service.events().list(calendarId='primary', q=search_term, timeMin=time_min, timeMax=time_max, maxResults=5, singleEvents=True, orderBy='startTime').execute()
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
        response_lines.append(f"- '{summary}' ({formatted_start}) - ID: {event_id}") # Убрал сокращение ID
    response_lines.append(_("\nЧто вы хотите сделать? (например, 'удали событие с ID ...')"))
    return "\n".join(response_lines)

def find_and_delete_event(user, data):
    service = get_google_service(user, 'calendar', 'v3')
    event_id = data.get('event_id')
    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        summary = event.get('summary', _('Без названия'))
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return _("✅ Событие '{summary}' успешно удалено!").format(summary=summary)
    except Exception:
        return _("Не удалось удалить событие. Пожалуйста, скопируйте и вставьте ID полностью.").format(event_id=event_id)

def find_and_update_event(user, data):
    service = get_google_service(user, 'calendar', 'v3')
    event_id = data.get('event_id')
    try:
        event_to_update = service.events().get(calendarId='primary', eventId=event_id).execute()
        event_to_update['start']['dateTime'] = data.get('new_start')
        event_to_update['end']['dateTime'] = data.get('new_end')
        updated_event = service.events().update(calendarId='primary', eventId=event_to_update['id'], body=event_to_update).execute()
        return _("✅ Событие '{summary}' успешно перенесено!").format(summary=updated_event.get('summary'))
    except Exception:
         return _("Не удалось обновить событие. Пожалуйста, скопируйте и вставьте ID полностью.").format(event_id=event_id)

def create_task(user, data):
    service = get_google_service(user, 'tasks', 'v1')
    task = {'title': data.get('title')}
    if data.get('due'):
        due_dt_object = datetime.fromisoformat(data.get('due'))
        task['due'] = due_dt_object.strftime('%Y-%m-%dT%H:%M:%S') + ".000Z"
    result = service.tasks().insert(tasklist='@default', body=task).execute()
    return _("✅ Задача успешно создана: '{task_title}'").format(task_title=result.get('title'))

def create_calendar_event(user, data):
    service = get_google_service(user, 'calendar', 'v3')
    start_time_str = data.get('start')
    end_time_str = data.get('end')
    if start_time_str and not end_time_str:
        start_time_obj = datetime.fromisoformat(start_time_str)
        end_time_obj = start_time_obj + timedelta(hours=1)
        end_time_str = end_time_obj.isoformat()
    event = {
        'summary': data.get('summary', _('Без названия')),
        'start': {'dateTime': start_time_str, 'timeZone': 'Asia/Makassar'},
        'end': {'dateTime': end_time_str, 'timeZone': 'Asia/Makassar'},
        'colorId': data.get('colorId', '1')
    }
    created_event = service.events().insert(calendarId='primary', body=event).execute()
    return _("✅ Событие успешно создано! '{summary}'").format(summary=created_event.get('summary'))


# --- ГЛАВНАЯ ФУНКЦИЯ-ОРКЕСТРАТОР (полностью заменяем) ---
def get_orchestrated_ai_response(user_prompt, user):
    # --- НАЧАЛО ИЗМЕНЕНИЯ: Получаем контекст диалога ---
    last_messages = AssistantMessage.query.filter_by(user_id=user.id).order_by(AssistantMessage.timestamp.desc()).limit(2).all()
    last_messages.reverse() # Восстанавливаем хронологический порядок

    context = ""
    if last_messages:
        context_lines = []
        for msg in last_messages:
            role = "Пользователь" if msg.role == 'user' else "Ассистент"
            context_lines.append(f"{role}: {msg.content}")
        context = "\n".join(context_lines)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    available_assistants = Assistant.query.filter_by(user_id=user.id).all()
    active_assistants = [a for a in available_assistants if a.instructions]
    if not active_assistants: return _("У вас пока нет настроенных ассистентов с инструкциями.")
    assistant_list_for_prompt = "\n".join([f"- id: {a.id}, name: {a.name}, description: {a.description}" for a in active_assistants])
    
    # --- НАЧАЛО ИЗМЕНЕНИЯ: Добавляем контекст в промпт ---
    selection_prompt = f"""
    Проанализируй ПОСЛЕДНИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ, учитывая ПРЕДЫДУЩИЙ ДИАЛОГ, и выбери ОДНОГО специалиста для выполнения задачи.
    В ответ дай ТОЛЬКО цифру — id выбранного специалиста.

    ПРЕДЫДУЩИЙ ДИАЛОГ:
    {context}

    СПИСОК СПЕЦИАЛИСТОВ:
    {assistant_list_for_prompt}

    ПОСЛЕДНИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ: "{user_prompt}"
    """
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        selection_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        selection_response = selection_model.generate_content(selection_prompt)
        selected_assistant_id_str = ''.join(filter(str.isdigit, selection_response.text))
        selected_assistant_id = int(selected_assistant_id_str)
        specialist_assistant = Assistant.query.filter_by(id=selected_assistant_id, user_id=user.id).first()

        # --- НАЧАЛО ИЗМЕНЕНИЯ: Небольшая проверка на случай, если ассистент не найден ---
        if not specialist_assistant:
             return _("Не удалось выбрать подходящего ассистента для вашего запроса.")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        if 'календар' in specialist_assistant.name.lower():
            today_date = datetime.now().strftime("%Y-%m-%d")
            instructions_with_date = specialist_assistant.instructions.replace("{{current_date}}", today_date)
            final_model = genai.GenerativeModel('gemini-1.5-flash-latest', system_instruction=instructions_with_date)
            response_text = final_model.generate_content(f"ПРЕДЫДУЩИЙ ДИАЛОГ:\n{context}\n\nЗАПРОС:\n{user_prompt}").text
            
            clean_json_string = response_text[response_text.find('{'):response_text.rfind('}')+1]
            response_json = json.loads(clean_json_string)
            intent = response_json.get("intent")
            data = response_json.get("data")

            if data and data.get('event_id'):
                if intent == "delete_event":
                    return find_and_delete_event(user, data)
                elif intent == "update_event":
                    return find_and_update_event(user, data)

            if intent in ["delete_event", "update_event", "find_events"]:
                return find_events(user, data)
            elif intent == "create_event":
                return create_calendar_event(user, data)
            elif intent == "create_task":
                return create_task(user, data)
            else:
                return _("Не удалось определить намерение.")
        else:
            final_model = genai.GenerativeModel('gemini-1.5-flash-latest', system_instruction=specialist_assistant.instructions)
            return final_model.generate_content(user_prompt).text
            
    except Exception as e:
        print(f"Orchestrator general error: {e}")
        return _('Произошла ошибка в работе ассистента.')