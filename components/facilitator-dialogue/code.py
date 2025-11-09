import subprocess
import json
import time

rounds = 100

def dialogue(model_a: str, model_b: str, initial_prompt: str):
    """
    Моделирует диалог между двумя компонентами MVP, используя mvp call.

    :param model_a: Имя первого компонента
    :param model_b: Имя второго компонента
    :param initial_prompt: Начальный вопрос для model_a
    :return: Список диалога
    """

    def call_component(component_name, user_input):
        payload = json.dumps({"question": user_input})
        try:
            result = subprocess.run(
                ["mvp", "call", component_name, "answer", payload],
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.stdout.strip()
        except Exception as e:
            return f"❌ Error calling {component_name}: {str(e)}"

    dialogue = []
    current_input = initial_prompt
    speaker_a = model_a
    speaker_b = model_b
    global rounds

    print(f"Initial prompt to {speaker_a}: {current_input}\n")

    for i in range(rounds):
        print(f"Round {i}\n")
        response_a = call_component(speaker_a, current_input)
        print(f"Speaking {speaker_a}:\n{response_a}\n")
        dialogue.append((response_a, current_input))
        current_input = response_a
        response_b = call_component(speaker_b, current_input)
        print(f"Speaking {speaker_b}:\n{response_b}\n")
        dialogue.append((response_b, current_input))
        current_input = response_b

    return dialogue

