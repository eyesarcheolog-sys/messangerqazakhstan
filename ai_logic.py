import os
import json
import google.generativeai as genai
from google.generativeai.types import GenerationConfig, Part, Tool
from flask_babel import gettext as _
from models import db, Assistant, AssistantMessage, User
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, date
import logging

logging.basicConfig(level=logging.INFO)

class GoogleTools:
    def __init__(self, user):
        self.user = user
        self.user_tz = getattr(user, 'timezone', 'UTC')

    def _get_google_service(self, service_name, version):
        # --- ИЗМЕНЕНИЕ 1: УЛУЧШЕННОЕ ЛОГИРОВАНИЕ АУТЕНТИФИКАЦИИ ---
        if not self.user.google_credentials_json:
            logging.warning(f"Attempted to get Google service '{service_name}' but user has no credentials.")
            return None
        try:
            info = json.loads(self.user.google_credentials_json)
            creds = Credentials.from_authorized_user_info(info)
            # Проверяем, не истек ли токен
            if creds.expired and creds.refresh_token:
                logging.info(f"Google credentials for user {self.user.id} expired. Refreshing...")
                # Важно импортировать Request здесь
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                # Обновляем токен в базе данных, чтобы не делать этого каждый раз
                self.user.google_credentials_json = creds.to_json()
                db.session.commit()
                logging.info("Credentials refreshed and saved successfully.")
            
            logging.info(f"Successfully created Google service '{service_name}' for user {self.user.id}")
            return build(service_name, version, credentials=creds)
        except Exception as e:
            logging.error(f"Failed to create Google service '{service_name}' for user {self.user.id}. Error: {e}", exc_info=True)
            return None
        # --- КОНЕЦ ИЗМЕНЕНИЯ 1 ---

    def create_calendar_event(self, summary: str, start: str, end: str = None):
        service = self._get_google_service('calendar', 'v3')
        if not service:
            return _("Google Calendar access is not configured or failed.")
        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')) if end else start_dt + timedelta(hours=1)
        except ValueError:
            return _("Could not parse the date. Please use ISO format YYYY-MM-DDTHH:MM:SS.")
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': self.user_tz},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': self.user_tz}
        }
        try:
            created_event = service.events().insert(calendarId='primary', body=event).execute()
            logging.info(f"Event created successfully in Google Calendar: {created_event.get('id')}")
            return _("✅ Event '{summary}' created successfully for {start_time}.").format(
                summary=created_event.get('summary'),
                start_time=start_dt.strftime('%d %B at %H:%M')
            )
        except Exception as e:
            logging.error(f"Error creating calendar event in Google API: {e}", exc_info=True)
            return _("Failed to create the calendar event.")

    def find_events(self, search_term: str):
        # ... (этот метод без изменений, но логирование внутри _get_google_service поможет) ...
        pass

    def create_task(self, title: str, due: str = None):
        service = self._get_google_service('tasks', 'v1')
        if not service:
            return _("Google Tasks access is not configured or failed.")
        task = {'title': title}
        if due:
            # Google Tasks API требует формат RFC 3339
            task['due'] = datetime.fromisoformat(due.replace('Z', '+00:00')).isoformat() + "Z"
        try:
            result = service.tasks().insert(tasklist='@default', body=task).execute()
            logging.info(f"Task created successfully in Google Tasks: {result.get('id')}")
            return _("✅ Task '{title}' created successfully.").format(title=result.get('title'))
        except Exception as e:
            logging.error(f"Error creating task in Google API: {e}", exc_info=True)
            return _("Failed to create the task.")

# --- ИЗМЕНЕНИЕ 2: ПОЛНОСТЬЮ ПЕРЕПИСАННАЯ ФУНКЦИЯ ДЛЯ НАДЕЖНОГО ВЫЗОВА ИНСТРУМЕНТОВ ---
def get_specialist_response(user_prompt, user, assistant):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)

        if not assistant or assistant.status != 'active':
            return _("The selected assistant is not found or inactive.")

        instructions = assistant.instructions.replace('{{current_date}}', date.today().strftime('%Y-%m-%d'))
        
        history = AssistantMessage.query.filter_by(user_id=user.id).order_by(AssistantMessage.timestamp.desc()).limit(10).all()
        history.reverse()
        chat_history = [{'role': 'user' if msg.role == 'user' else 'model', 'parts': [msg.content]} for msg in history]
        
        google_tools_handler = GoogleTools(user)
        
        all_available_tools = {
            "create_calendar_event": google_tools_handler.create_calendar_event,
            "find_events": google_tools_handler.find_events,
            "create_task": google_tools_handler.create_task
        }

        selected_tool_names = assistant.tools.split(',') if assistant.tools else []
        functions_for_model = [all_available_tools[name] for name in selected_tool_names if name in all_available_tools]
        
        model = genai.GenerativeModel(
            model_name='gemini-1.5-pro-latest',
            system_instruction=instructions,
            tools=functions_for_model if functions_for_model else None
        )
        
        chat = model.start_chat(history=chat_history)
        
        # ШАГ 1: Отправляем запрос модели
        response = chat.send_message(user_prompt)
        
        # ШАГ 2: Проверяем, хочет ли модель вызвать функцию
        try:
            function_call = response.candidates[0].content.parts[0].function_call
        except (ValueError, IndexError, AttributeError):
            # Если нет function_call, значит модель просто ответила текстом
            logging.info("Model returned a direct text response.")
            return response.text

        # ШАГ 3: Если модель хочет вызвать функцию, мы делаем это вручную
        tool_name = function_call.name
        if tool_name in all_available_tools:
            tool_args = {key: value for key, value in function_call.args.items()}
            logging.info(f"Model requested to call tool '{tool_name}' with args: {tool_args}")
            
            # Выполняем нашу Python-функцию
            tool_function = all_available_tools[tool_name]
            tool_response_text = tool_function(**tool_args)
            logging.info(f"Tool '{tool_name}' returned: {tool_response_text}")

            # ШАГ 4: Отправляем результат выполнения функции обратно модели
            response = chat.send_message(
                Part(
                    function_response=genai.FunctionResponse(
                        name=tool_name,
                        response={'result': tool_response_text}
                    )
                )
            )
            # Теперь модель сформулирует окончательный ответ для пользователя
            return response.text
        else:
            logging.warning(f"Model tried to call an unknown tool: {tool_name}")
            return _("The assistant tried to use an unknown tool.")

    except Exception as e:
        logging.error(f"Critical error in get_specialist_response: {e}", exc_info=True)
        return _('A critical error occurred in the specialist assistant.')

