import os
import google.generativeai as genai
from flask_babel import gettext as _
# ИЗМЕНЕНИЕ: Импортируем из нового файла models.py
from models import Assistant 

def get_orchestrated_ai_response(user_prompt, user):
    """
    Эта функция-оркестратор управляет взаимодействием с ИИ.
    Шаг 1: Выбирает подходящего ассистента.
    Шаг 2: Получает ответ от выбранного ассистента.
    """
    # ... (остальной код функции остается БЕЗ ИЗМЕНЕНИЙ) ...
    # Шаг 1.1: Получаем список активных ассистентов пользователя из БД
    available_assistants = Assistant.query.filter_by(user_id=user.id, status='active').all()

    if not available_assistants:
        return "У вас пока нет настроенных ассистентов."

    assistant_list_for_prompt = "\n".join(
        [f"- id: {a.id}, name: {a.name}, description: {a.description}" for a in available_assistants]
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
            return f"Ассистент с ID {selected_assistant_id} не найден или не имеет инструкций."

        final_model = genai.GenerativeModel(
            'gemini-1.5-flash-latest',
            system_instruction=specialist_assistant.instructions
        )
        final_response = final_model.generate_content(user_prompt)

        return final_response.text

    except (ValueError, IndexError, TypeError) as e:
        print(f"Orchestrator Error: Could not parse assistant ID from response. Details: {e}")
        return "Извините, не удалось выбрать подходящего ассистента. Попробуйте переформулировать."
    except Exception as e:
        print(f"Orchestrator general error: {e}")
        return _('AI Assistant service failed')