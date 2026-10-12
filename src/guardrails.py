"""Модуль защиты от промпт-инъекций, джейлбрейков и вредоносных запросов (SRE Guardrails)."""

import re
from typing import Tuple, Optional


# Регулярные выражения для детекции попыток манипуляции системным промптом (Prompt Injection / Jailbreak)
INJECTION_PATTERNS = [
    # Попытки забыть/отменить инструкции (RU)
    r"\bзабудь\b[\s\S]{0,30}\b(правил[а-я]*|инструкци[а-я]*|ограничени[а-я]*|промпт[а-я]*|настройк[а-я]*)",
    r"\bигнорируй\b[\s\S]{0,30}\b(правил[а-я]*|инструкци[а-я]*|ограничени[а-я]*|промпт[а-я]*|системн[а-я]*)",
    r"\bотбрось\b[\s\S]{0,30}\b(правил[а-я]*|инструкци[а-я]*|ограничени[а-я]*)",
    r"\bсними\b[\s\S]{0,30}\b(ограничени[а-я]*|фильтр[а-я]*|запрет[а-я]*)",
    r"\bотключи\b[\s\S]{0,30}\b(безопасност[а-я]*|фильтр[а-я]*|цензур[а-я]*|ограничени[а-я]*)",
    r"\b(ты больше не|ты теперь не|перестань быть)\b[\s\S]{0,30}\b(ассистент|sre|помощник)",
    r"\b(ты теперь|действуй как|притворись|веди себя как)\b[\s\S]{0,30}\b(dan|jailbreak|человек|хакер|убийца|бот без правил|всемогущ[а-я]*)",
    r"\b(режим разработчика|developer mode|god mode|jailbreak mode)\b",
    r"\b(покажи|раскрой|выведи|процитируй)\b[\s\S]{0,30}\b(системный промпт|system prompt|системные инструкции|свои инструкции)",

    # Попытки забыть/отменить инструкции (EN)
    r"\bignore\b[\s\S]{0,30}\b(all|any|previous)?\s*(rules|instructions|guidelines|prompts|constraints)",
    r"\bdisregard\b[\s\S]{0,30}\b(all|any|previous)?\s*(rules|instructions|guidelines|prompts)",
    r"\bforget\b[\s\S]{0,30}\b(all|previous)?\s*(rules|instructions|guidelines)",
    r"\byou are now\b[\s\S]{0,30}\b(dan|unfiltered|jailbroken|free|evil)",
    r"\bact as\b[\s\S]{0,30}\b(dan|jailbreak|unfiltered|an unrestricted)",
    r"\bbypass\b[\s\S]{0,30}\b(safety|guardrails|filter|restrictions)",
    r"\b(reveal|print|show|output)\b[\s\S]{0,30}\b(system prompt|instructions|initial prompt)",
]

# Регулярные выражения для детекции опасного контента (Оружие, Насилие, Взрывчатка, Вредоносная деятельность)
DANGEROUS_PATTERNS = [
    # Оружие и боеприпасы
    r"\b(где|как)\b[\s\S]{0,30}\b(достать|купить|найти|заказать|приобрести|взять|сделать|собрать|изготовить)\b[\s\S]{0,30}\b(оружи[еяю]|ствол[а-я]*|пистолет[а-я]*|автомат[а-я]*|винтовк[а-я]*|ружь[еёя][а-я]*|патрон[а-я]*|боеприпас[а-я]*|глок|ак-?47|взрывчатк[а-я]*|бомб[а-я]*)",
    r"\bкупить\b[\s\S]{0,25}\b(оружи[еяю]|ствол[а-я]*|пистолет[а-я]*|автомат[а-я]*|боевые патроны|взрывчатк[а-я]*|бомб[а-я]*)",
    r"\b(черный рынок|даркнет)\b[\s\S]{0,30}\b(оружи[ея]|ствол[а-я]*|пистолет[а-я]*)",
    r"\b(изготовлени[ея]|чертеж[а-я]*|сборк[а-я])\b[\s\S]{0,30}\b(оружи[ея]|пистолет[а-я]*|взрывчатк[а-я]*|бомб[а-я]*|сву|взрывного устройства)",
    r"\b(самодельн[а-я]*|кустарн[а-я]*)\b[\s\S]{0,25}\b(оружи[ея]|пистолет[а-я]*|бомб[а-я]*|взрывчатк[а-я]*)",
    r"\b(огнестрельн[а-я]* оружи[ея]|боевое оружие)\b",

    # Оружие (EN)
    r"\b(how|where)\s+to\s+(get|buy|find|obtain|make|build)\b[\s\S]{0,25}\b(gun|guns|weapon|weapons|firearm|firearms|pistol|rifle|bomb|explosive|ammo|ammunition)",
    r"\bbuy\s+(a\s+)?(gun|firearm|pistol|rifle|weapon|explosive|bomb)\b",
    r"\b(make|build|assemble)\s+(a\s+)?(bomb|explosive|improvised weapon|firearm)\b",

    # Насилие, теракты, яды, убийства
    r"\b(как|способ|план)\b[\s\S]{0,25}\b(убить|застрелить|взорвать|отравить|зарезать|покалечить)\b",
    r"\b(совершить|организовать)\b[\s\S]{0,25}\b(теракт|нападение|убийство)\b",

    # Вредоносное ПО / атаки вне SRE
    r"\b(напиши|создай|дай)\b[\s\S]{0,25}\b(вирус|троян|стиллер|шифровальщик|ransomware|keylogger|малварь)\b",
    r"\b(взломай|заддось|положи сайт|атакуй)\b[\s\S]{0,25}\b(чужой|сервер|пентагон|госуслуги|соседа)\b",
]

# Стандартный официальный ответ при срабатывании защиты
SAFETY_REFUSAL_MESSAGE = (
    "### Запрос отклонен системой безопасности (SRE Guardrails)\n\n"
    "Я являюсь **специализированным SRE AI-ассистентом** платформы анализа инцидентов и логов. "
    "Моя задача — помогать инженерам в расследовании технических аварий, анализе стектрейсов, "
    "настройке мониторинга и составлении регламентов (Runbooks).\n\n"
    "**Политикой платформы строго запрещено:**\n"
    "1. Обсуждение, приобретение или изготовление оружия, боеприпасов, взрывчатых веществ и опасных материалов.\n"
    "2. Насилие, причинение вреда или содействие противоправным действиям.\n"
    "3. Попытки изменения системной роли, снятия ограничений или выполнения инструкций вне IT/SRE контекста.\n\n"
    "*Пожалуйста, сформулируйте вопрос, связанный с диагностикой инфраструктуры, анализом логов или DevOps.*"
)


def normalize_text_for_security(text: str) -> str:
    """Нормализует текст: удаляет лишние пробелы между буквами и спецсимволы обхода."""
    if not text:
        return ""
    cleaned = text.lower()
    # Убираем разделительные точки/тире/пробелы при попытках обхода (например, о.р.у.ж.и.е)
    # Если в слове буквы разделены одиночными пробелами: "о р у ж и е" -> "оружие"
    spaced_word_pattern = r"(?<=\b[а-яa-z])\s+(?=[а-яa-z]\b)"
    cleaned = re.sub(spaced_word_pattern, "", cleaned)
    return cleaned


def validate_user_prompt(user_text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Проверяет пользовательский запрос на наличие промпт-инъекций и запрещенных тем.
    
    Возвращает:
      (is_safe: bool, violation_type: Optional[str], refusal_message: Optional[str])
    """
    if not user_text or not user_text.strip():
        return True, None, None

    normalized = normalize_text_for_security(user_text)

    # 1. Проверка на попытки Prompt Injection / Jailbreak
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, "prompt_injection", SAFETY_REFUSAL_MESSAGE

    # 2. Проверка на запрещенный опасный контент (Оружие, Насилие, Взрывчатка)
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, "dangerous_content", SAFETY_REFUSAL_MESSAGE

    return True, None, None


def validate_assistant_output(output_text: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет сгенерированный ответ LLM на случай, если модель все же поддалась инъекции.
    
    Возвращает:
      (is_safe: bool, safe_text: Optional[str])
    """
    if not output_text:
        return True, output_text

    norm = output_text.lower()

    # Подозрительные ответы, где модель предлагает достать оружие или расспрашивает город/место для этого
    suspicious_weapon_compliance = [
        r"(в каком|укажите|назовите)\s+(городе|регионе|стране)[\s\S]{0,30}(оружи|пистолет|ствол|покупк)",
        r"(купить|достать|приобрести)\s+(оружие|пистолет|ствол|автомат)[\s\S]{0,30}(можно в|магазин|разрешени)",
        r"(черном рынке|даркнет)[\s\S]{0,30}(оружи|ствол)",
    ]

    for pat in suspicious_weapon_compliance:
        if re.search(pat, norm, re.IGNORECASE):
            return False, SAFETY_REFUSAL_MESSAGE

    return True, output_text
