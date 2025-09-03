import os
import google.generativeai as genai
from flask_babel import gettext as _
from server import Assistant # Импортируем нашу модель Assistant из главного файла

def get_orchestrated_ai_response(user_prompt, user):
    """
    Эта функция-оркестратор управляет взаимодействием с ИИ.
    Шаг 1: Выбирает подходящего ассистента.
    Шаг 2: Получает ответ от выбранного ассистента.
    """
    # Шаг 1.1: Получаем список активных ассистентов пользователя из БД
    available_assistants = Assistant.query.filter_by(user_id=user.id, status='active').all()

    if not available_assistants:
        # Если у пользователя нет активных ассистентов, используем ответ по умолчанию
        # В будущем здесь можно будет использовать "Главного ассистента" с базовыми настройками
        return "У вас пока нет настроенных ассистентов."

    # Шаг 1.2: Формируем список ассистентов для промпта
    assistant_list_for_prompt = "\n".join(
        [f"- id: {a.id}, name: {a.name}, description: {a.description}" for a in available_assistants]
    )

    # Шаг 1.3: Создаем промпт для "Главного ассистента-диспетчера"
    selection_prompt = f"""
    Ты — главный ассистент-диспетчер. Проанализируй запрос пользователя и выбери ОДНОГО из доступных специалистов из списка ниже, который лучше всего подходит для выполнения задачи.
    В ответ дай ТОЛЬКО цифру — id выбранного специалиста. Не добавляй никаких других слов, текста или знаков препинания.

    Список доступных специалистов:
    {assistant_list_for_prompt}

    Запрос пользователя: "{user_prompt}"
    """

    try:
        # Шаг 1.4: Отправляем запрос к ИИ для выбора специалиста
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: raise ValueError("GEMINI_API_KEY is not set")
        genai.configure(api_key=api_key)
        
        selection_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        selection_response = selection_model.generate_content(selection_prompt)
        
        # Шаг 1.5: Обрабатываем ответ и выбираем ID ассистента
        selected_assistant_id_str = ''.join(filter(str.isdigit, selection_response.text))
        if not selected_assistant_id_str:
            raise ValueError("AI did not return a valid ID.")
        selected_assistant_id = int(selected_assistant_id_str)
        
        # Шаг 2.1: Находим выбранного ассистента в базе данных
        specialist_assistant = Assistant.query.filter_by(id=selected_assistant_id, user_id=user.id).first()

        if not specialist_assistant or not specialist_assistant.instructions:
            return "Выбранный ассистент не найден или не имеет инструкций."

        # Шаг 2.2: Отправляем финальный запрос к ИИ с инструкциями специалиста
        final_model = genai.GenerativeModel(
            'gemini-1.5-flash-latest',
            system_instruction=specialist_assistant.instructions
        )
        final_response = final_model.generate_content(user_prompt)

        return final_response.text

    except (ValueError, IndexError, TypeError) as e:
        print(f"Orchestrator Error: Could not parse assistant ID from response. Details: {e}")
        # Если ИИ вернул что-то не то (не цифру), или произошла другая ошибка,
        # возвращаем пользователю сообщение об ошибке.
        return "Извините, не удалось выбрать подходящего ассистента для вашего запроса. Попробуйте переформулировать."
    except Exception as e:
        print(f"Orchestrator general error: {e}")
        return _('AI Assistant service failed')