# SQB Process Hub — Python FastAPI Backend

Бэкенд-сервис платформы автоматизации бизнес-процессов **АКБ «Узпромстройбанк» (SQB)**.

Обеспечивает сквозную цепочку:
`Draw.io / BPMN XML` ➔ `PIX BPM Реестры и Регламент` ➔ `Infomaximum Processet (BPMN 2.0 + Event Logs)`.

---

## 🚀 Архитектура и стек технологий
* **Python 3.10+ / 3.14**
* **FastAPI** — высокопроизводительный асинхронный REST API фреймворк.
* **Pydantic v2** — строгая валидация и типизация моделей данных.
* **Uvicorn** — ASGI веб-сервер.
* **zlib / ElementTree** — парсинг сжатых deflate-raw схем Draw.io и спецификации OMG BPMN 2.0.

---

## 📂 Структура проекта

```
backend/
├── app/
│   ├── main.py                     # Точка входа FastAPI, CORS, healthcheck
│   ├── models/
│   │   └── process.py              # Pydantic v2 схемы (BusinessProcess, PIX Registry, Mining)
│   ├── routers/
│   │   ├── processes.py            # CRUD процессов и кейсов реестров PIX
│   │   ├── import_export.py        # Парсер draw.io/BPMN + экспорт в XML, CSV, JSON
│   │   └── analytics.py            # Process Mining аналитика и кандидаты PIX RPA
│   └── services/
│       ├── drawio_parser.py        # Декомпрессия base64/zlib и XML-парсер
│       ├── bpmn_exporter.py        # Генератор OMG BPMN 2.0 XML с DI для Processet
│       ├── conformance_engine.py   # Движок сравнения Should-Be vs As-Is
│       └── exporters.py            # Генераторы Event Logs CSV и регламентов Excel
├── tests/
│   └── test_api.py                 # Автоматические тесты жизненного цикла
├── requirements.txt                # Зависимости
└── start.sh                        # Скрипт быстрого запуска
```

---

## ⚡ Быстрый запуск

### 1. Запуск через bash-скрипт:
```bash
cd backend
chmod +x start.sh
./start.sh
```

### 2. Запуск вручную:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📖 Документация API (Swagger / OpenAPI)
* **Интерактивный Swagger UI**: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)
* **ReDoc спецификация**: [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)
* **OpenAPI JSON**: [http://localhost:8000/api/openapi.json](http://localhost:8000/api/openapi.json)

---

## 🛠 Ключевые эндпоинты

| Метод | Эндпоинт | Описание |
| :--- | :--- | :--- |
| `POST` | `/api/v1/import/file` | Загрузка файла `.drawio`, `.bpmn`, `.xml` и создание процесса |
| `POST` | `/api/v1/import/xml` | Парсинг исходного XML диаграммы из тела запроса |
| `GET` | `/api/v1/import/{id}/export/bpmn` | Экспорт в эталонный **BPMN 2.0 XML** для Infomaximum Processet |
| `GET` | `/api/v1/import/{id}/export/event-log` | Экспорт **Event Logs CSV** (журнал событий) для Processet |
| `GET` | `/api/v1/import/{id}/export/regulation` | Экспорт таблицы регламента в **Excel CSV** (UTF-8 BOM) |
| `GET` | `/api/v1/import/{id}/export/pix-json` | Экспорт схемы и реестра в формат **PIX BPM JSON** |
| `GET` | `/api/v1/processes/` | Список всех загруженных процессов |
| `POST` | `/api/v1/processes/{id}/registry/cases` | Создание новой заявки/кейса в реестре PIX |
| `GET` | `/api/v1/analytics/{id}/mining` | Метрики Process Mining (SLA, петли возвратов, отклонения) |
| `GET` | `/api/v1/analytics/{id}/rpa-candidates` | Рейтинг шагов по потенциалу роботизации в PIX RPA |

---

## 🧪 Запуск тестов
```bash
cd backend
venv/bin/python3 -m unittest discover -s tests
```
