import os
import json
import google.generativeai as genai
from flask_babel import gettext as _
from models import Assistant
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

def find_and_delete_event(user, data):
    """ Находит и удаляет событие в Google Календаре. """
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    service = build('calendar', 'v3', credentials=creds)

    search_term = data.get('search_term')
    time_min = datetime.now().isoformat() + 'Z'
    
    events_result = service.events().list(calendarId='primary', q=search_term,
                                        timeMin=time_min, maxResults=1,
                                        singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        return _("Не удалось найти событие '{search_term}' для удаления.").format(search_term=search_term)

    event_to_delete = events[0]
    event_id = event_to_delete['id']
    event_summary = event_to_delete.get('summary', 'Без названия')
    
    service.events().delete(calendarId='primary', eventId=event_id).execute()
    
    return _("✅ Событие '{event_summary}' успешно удалено!").format(event_summary=event_summary)

def create_task(user, data):
    """ Создает задачу в Google Tasks, возможно с датой выполнения. """
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    service = build('tasks', 'v1', credentials=creds)
    task = {'title': data.get('title')}
    if data.get('due'):
        due_dt_object = datetime.fromisoformat(data.get('due'))
        task['due'] = due_dt_object.strftime('%Y-%m-%dT%H:%M:%S') + ".000Z"
    result = service.tasks().insert(tasklist='@default', body=task).execute()
    return _("✅ Задача успешно создана: '{task_title}'").format(task_title=result.get('title'))

def find_and_update_event(user, data):
    """ Находит и обновляет событие в Google Календаре. """
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    service = build('calendar', 'v3', credentials=creds)
    search_term = data.get('search_term')
    time_min = datetime.now().isoformat() + 'Z'
    events_result = service.events().list(calendarId='primary', q=search_term, timeMin=time_min, maxResults=1, singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])
    if not events:
        return _("Не удалось найти событие '{search_term}' для обновления.").format(search_term=search_term)
    event_to_update = events[0]
    event_to_update['start']['dateTime'] = data.get('new_start')
    event_to_update['end']['dateTime'] = data.get('new_end')
    updated_event = service.events().update(calendarId='primary', eventId=event_to_update['id'], body=event_to_update).execute()
    return _("✅ Событие '{event_summary}' успешно перенесено!").format(event_summary=updated_event.get('summary'))

def create_calendar_event(user, data):
    """ Создает событие в Google Календаре с проверкой времени и цветом. """
    info = json.loads(user.google_credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    service = build('calendar', 'v3', credentials=creds)
    start_time_str = data.get('start')
    end_time_str = data.get('end')
    if start_time_str and not end_time_str:
        start_time_obj = datetime.fromisoformat(start_time_str)
        end_time_obj = start_time_obj + timedelta(hours=1)
        end_time_str = end_time_obj.isoformat()
    event = {
        'summary': data.get('summary', 'Без названия'),
        'start': {'dateTime': start_time_str, 'timeZone': 'Asia/Makassar'},
        'end': {'dateTime': end_time_str, 'timeZone': 'Asia/Makassar'},
        'colorId': data.get('colorId', '1')
    }
    created_event = service.events().insert(calendarId='primary', body=event).execute()
    return _("✅ Событие успешно создано! '{event_summary}'").format(event_summary=created_event.get('summary'))

def get_orchestrated_ai_response(user_prompt, user):
    """
    Эта функция-оркестратор управляет взаимодействием с ИИ.
    Шаг 1: Выбирает подходящего ассистента.
    Шаг 2: Получает ответ от выбранного ассистента и выполняет действие.
    """
    available_assistants = Assistant.query.filter_by(user_id=user.id).all()
    active_assistants = [a for a in available_assistants if a.instructions]
    if not active_assistants: return _("У вас пока нет настроенных ассистентов с инструкциями.")
    assistant_list_for_prompt = "\n".join([f"- id: {a.id}, name: {a.name}, description: {a.description}" for a in active_assistants])
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
        if not selected_assistant_id_str: raise ValueError("AI did not return a valid numeric ID.")
        selected_assistant_id = int(selected_assistant_id_str)
        specialist_assistant = Assistant.query.filter_by(id=selected_assistant_id, user_id=user.id).first()
        if not specialist_assistant or not specialist_assistant.instructions: return _("Ассистент с ID {assistant_id} не найден...").format(assistant_id=selected_assistant_id)

        if 'календар' in specialist_assistant.name.lower():
            today_date = datetime.now().strftime("%Y-%m-%d")
            instructions_with_date = specialist_assistant.instructions.replace("{{current_date}}", today_date)
            final_model = genai.GenerativeModel('gemini-1.5-flash-latest', system_instruction=instructions_with_date)
            response_text = final_model.generate_content(user_prompt).text
            start_index = response_text.find('{')
            end_index = response_text.rfind('}')
            clean_json_string = response_text[start_index:end_index+1] if start_index != -1 and end_index != -1 else "{}"
            
            try:
                response_json = json.loads(clean_json_string)
                intent = response_json.get("intent")
                data = response_json.get("data")

                if intent == "create_event":
                    return create_calendar_event(user, data)
                elif intent == "create_task":
                    return create_task(user, data)
                elif intent == "update_event":
                    return find_and_update_event(user, data)
                elif intent == "delete_event":
                    return find_and_delete_event(user, data)
                else:
                    return _("Не удалось определить намерение. Попробуйте переформулировать.")
            except (json.JSONDecodeError, AttributeError):
                 return _("Ассистент вернул ответ в неверном формате. Попробуйте еще раз.")
        else:
            final_model = genai.GenerativeModel('gemini-1.5-flash-latest', system_instruction=specialist_assistant.instructions)
            return final_model.generate_content(user_prompt).text
            
    except Exception as e:
        print(f"Orchestrator general error: {e}")
        return _('Произошла ошибка в работе ассистента.')