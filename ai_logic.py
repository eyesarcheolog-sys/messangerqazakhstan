import os
import json
import google.generativeai as genai
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
        if not self.user.google_credentials_json:
            return None
        info = json.loads(self.user.google_credentials_json)
        creds = Credentials.from_authorized_user_info(info)
        return build(service_name, version, credentials=creds)

    def create_calendar_event(self, summary: str, start: str, end: str = None):
        service = self._get_google_service('calendar', 'v3')
        if not service:
            return _("Google Calendar access is not configured.")
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
            logging.info(f"Event created successfully: {created_event.get('id')}")
            return _("✅ Event '{summary}' created successfully for {start_time}.").format(
                summary=created_event.get('summary'),
                start_time=start_dt.strftime('%d %B at %H:%M')
            )
        except Exception as e:
            logging.error(f"Error creating calendar event: {e}")
            return _("Failed to create the calendar event.")

    def find_events(self, search_term: str):
        service = self._get_google_service('calendar', 'v3')
        if not service:
            return _("Google Calendar access is not configured.")
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
            if not events:
                return _("No events with the name '{search_term}' found for the upcoming week.").format(search_term=search_term)
            response_lines = [_("Here's what I found:")]
            for event in events:
                start_dt = datetime.fromisoformat(event['start'].get('dateTime').replace('Z', '+00:00'))
                response_lines.append(f"- '{event.get('summary')}' ({start_dt.strftime('%d %B at %H:%M')})")
            return "\n".join(response_lines)
        except Exception as e:
            logging.error(f"Error finding events: {e}")
            return _("An error occurred while searching for events.")

    def create_task(self, title: str, due: str = None):
        service = self._get_google_service('tasks', 'v1')
        if not service:
            return _("Google Tasks access is not configured.")
        task = {'title': title}
        if due:
            task['due'] = datetime.fromisoformat(due.replace('Z', '+00:00')).isoformat() + "Z"
        try:
            result = service.tasks().insert(tasklist='@default', body=task).execute()
            logging.info(f"Task created successfully: {result.get('id')}")
            return _("✅ Task '{title}' created successfully.").format(title=result.get('title'))
        except Exception as e:
            logging.error(f"Error creating task: {e}")
            return _("Failed to create the task.")

def get_specialist_response(user_prompt, user, assistant):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
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
        tools_for_model = [all_available_tools[name] for name in selected_tool_names if name in all_available_tools]

        model = genai.GenerativeModel(
            model_name='gemini-1.5-pro-latest',
            system_instruction=instructions,
            tools=tools_for_model if tools_for_model else None
        )
        
        request_content = chat_history + [{'role': 'user', 'parts': [user_prompt]}]
        
        response = model.generate_content(request_content)
        
        # The updated library handles function calls internally and returns the final text response,
        # which is a more robust way to avoid the 'whichOneof' error.
        return response.text

    except Exception as e:
        logging.error(f"Specialist response error: {e}", exc_info=True)
        return _('An error occurred in the specialist assistant.')
