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
│   ├── resources/
│   │   └── pix_configuration.xml   # Эталонный каталог свойств и нотаций PIX (в .pmm as is)
│   └── services/
│       ├── drawio_parser.py        # Декомпрессия base64/zlib и XML-парсер
│       ├── bpmn_exporter.py        # Генератор OMG BPMN 2.0 XML с DI для Processet / PIX
│       ├── pmm_exporter.py         # Нативный пакет PIX Process Studio (.pmm ZIP из 3 XML)
│       ├── conformance_engine.py   # Движок сравнения Should-Be vs As-Is
│       └── exporters.py            # Генераторы Event Logs CSV и регламентов Excel
├── tests/
│   ├── test_api.py                 # Автоматические тесты жизненного цикла
│   └── test_methodology_export.py  # Регрессия на конвенции Методики (2-ILOVA / 4-ILOVA)
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
| `GET` | `/api/v1/import/{id}/export/pmm` | Экспорт нативного пакета **PIX Процессной студии** (`.pmm`) |
| `GET` | `/api/v1/import/{id}/export/event-log` | Экспорт **Event Logs CSV** (журнал событий) для Processet |
| `GET` | `/api/v1/import/{id}/export/regulation` | Экспорт таблицы регламента в **Excel CSV** (UTF-8 BOM) |
| `GET` | `/api/v1/import/{id}/export/pix-json` | Экспорт схемы и реестра в формат **PIX BPM JSON** |
| `GET` | `/api/v1/processes/` | Список всех загруженных процессов |
| `POST` | `/api/v1/processes/{id}/registry/cases` | Создание новой заявки/кейса в реестре PIX |
| `GET` | `/api/v1/analytics/{id}/mining` | Метрики Process Mining (SLA, петли возвратов, отклонения) |
| `GET` | `/api/v1/analytics/{id}/rpa-candidates` | Рейтинг шагов по потенциалу роботизации в PIX RPA |

---

## 🧩 Соглашения экспорта в PIX

Карты банка рисуются по Методике (приложение 2-ILOVA), и экспорт обязан
сохранять её словарь целиком, а не только «шаг — стрелка — шаг».

| Фигура на карте draw.io | Модель | BPMN 2.0 | PIX `.pmm` |
| :--- | :--- | :--- | :--- |
| мелкий таймер без связей, «5 min» | ST шага (`slaMinutes`) | `documentation` + граничный таймер-значок | `intermediate_event_catch_timer` 24×24 у грани шага |
| таймер в потоке, «Kutish vaqti 30 min» | `intermediateTimerEvent` | `intermediateCatchEvent` + `timerEventDefinition` | `intermediate_event_catch_timer` |
| `shape=datastore` (IABS, EHA, EDO) | `dataStore` | `dataStoreReference` | `dataStorage` |
| `mxgraph.bpmn.data2` (Dalolatnoma) | `dataObject` | `dataObjectReference` | `dataObject` |
| `shape=note` | `textAnnotation` | `textAnnotation` | `input` |
| пунктир к артефакту | `kind: association` | `bpmn:association` | `lineStyle="dotted"` |

Линии карты:

* draw.io хранит только те изломы, которые аналитик подвинул руками, а сам
  ведёт связь по осям (`edgeStyle=orthogonalEdgeStyle`). Ломаную восстанавливает
  `services/edge_routing.py` — он же используется на холсте, иначе схема в
  bpmn.io выглядит паутиной диагоналей;
* конец связи в draw.io может быть не привязан к фигуре, а задан точкой
  (`mxPoint as="sourcePoint"`). Такой конец притягивается к фигуре под ним
  (порог 30 px, при равном расстоянии выигрывает меньшая фигура);
* если ни один конец не опирается на шаг — это оформительская линия
  (`kind: annotationLine`, разделители этапов). На холсте она рисуется
  приглушённой, в BPMN и `.pmm` не выгружается: такой конструкции там нет.

Тонкости формата `.pmm`, сверенные с выгрузкой самой Процессной студии:

* узлы внутри `horizontalRoad` позиционируются **относительно дорожки**;
* подпись связи — атрибут **`Text`**, а не `label` (`label` студия игнорирует);
* `waypoint` — **полная ломаная**, включая точки на границах узлов; если
  изломов не было, waypoint не пишем и трассировку делает студия;
* `sourcePoint`/`targetPoint` необязательны — их лучше не задавать, чем задать
  наугад;
* `pm/configuration.xml` отдаётся эталонным файлом PIX без изменений:
  имена элементов нотаций (`dfd_process`, `c4_person`, `app_component`)
  заданы студией, и самодельный каталог рискует не пройти её валидацию.

### Часы со временем шага в выгрузке

Время операции (ST) и ожидания (WT) раньше уезжало только в
`bpmn:documentation`: в Процессной студии карта открывалась без единой цифры.
Теперь каждый шаг с проставленным временем получает **видимый значок**:

* в **BPMN 2.0** — некрывающий граничный таймер
  (`boundaryEvent` + `timerEventDefinition`, `cancelActivity="false"`) с
  подписью вида «45 мин» или «1 ч 30 мин · ожидание 15 мин». Импортёр рисует
  его на границе фигуры — там же, где часы стоят на исходной карте draw.io.
  Поток он не меняет: исходящих переходов у события нет, и ни один
  `sequenceFlow` на него не ссылается;
* в **`.pmm`** — отдельная мелкая фигура `intermediate_event_catch_timer`
  24×24 в правом нижнем углу шага, зажатая в границы дорожки. Граничных
  событий формат не знает, поэтому значок повторяет рисунок аналитика.

Прочитать это обратно умеет фронтенд: `app/src/lib/bpmn-import.ts` и
`app/src/lib/pmm-import.ts` возвращают время значка в ST/WT шага, и в окне
«Просмотр BPMN / PMM» сотрудник видит выгруженный файл с теми же часами, что
и на холсте.

Инварианты выгрузки BPMN проверяются в `tests/test_methodology_export.py`:
стартовое событие без входящих переходов, конечное без исходящих, ассоциации
только к артефактам, `flowNodeRef` только на узлы потока, порядок элементов
`laneSet* , flowElement* , artifact*`, значок длительности у каждого шага с
временем и вне потока управления.

---

## 🧪 Запуск тестов
```bash
cd backend
venv/bin/python3 -m unittest discover -s tests
```