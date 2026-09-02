import random
import uuid
from faker import Faker

fake = Faker()

def generate_mock_from_schema(schema: dict, all_schemas: dict) -> any:
    """Рекурсивно генерирует фейковые данные на основе OpenAPI схемы."""
    
    if not schema:
        return None

    # Если это ссылка на другую схему ($ref)
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        target_schema = all_schemas.get(ref_name, {})
        return generate_mock_from_schema(target_schema, all_schemas)

    schema_type = schema.get("type")
    
    # Обработка AnyOf / AllOf (берем первый попавшийся вариант)
    if "anyOf" in schema:
        return generate_mock_from_schema(schema["anyOf"][0], all_schemas)
    
    if schema_type == "object" or "properties" in schema:
        obj = {}
        for prop_name, prop_schema in schema.get("properties", {}).items():
            obj[prop_name] = generate_mock_from_schema(prop_schema, all_schemas)
        return obj

    if schema_type == "array":
        # Генерируем от 1 до 3 элементов для массивов
        items_schema = schema.get("items", {})
        count = random.randint(1, 3)
        return [generate_mock_from_schema(items_schema, all_schemas) for _ in range(count)]

    # Базовые типы
    if schema_type == "string":
        # Проверяем Enum
        if "enum" in schema:
            return random.choice(schema["enum"])
            
        # Проверяем description для умной генерации (эвристика)
        desc = schema.get("description", "").lower()
        if "email" in desc or "email" in schema.get("title", "").lower():
            return fake.company_email()
        if "uuid" in desc or "id" in desc.split():
            return str(uuid.uuid4())
        if "quote" in desc or "text" in desc:
            return fake.paragraph(nb_sentences=2)
            
        return fake.word().capitalize()

    if schema_type == "integer":
        return random.randint(1, 100)

    if schema_type == "number":
        return round(random.uniform(1.0, 100.0), 2)

    if schema_type == "boolean":
        return random.choice([True, False])

    return "auto-mocked-value"

