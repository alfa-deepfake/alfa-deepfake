# ALFA-DEEPFAKE

```text
╭───────────────────────────────────────────────────────╮
│ █████  █      █████  █████                            │
│ █   █  █      █      █   █                            │
│ █████  █      ████   █████                            │
│ █   █  █      █      █   █                            │
│ █   █  █████  █      █   █                            │
│                                                       │
│ ████   █████  █████  ████   █████  █████  █  █  █████ │
│ █   █  █      █      █   █  █      █   █  █ █   █     │
│ █   █  ████   ████   ████   ████   █████  ██    ████  │
│ █   █  █      █      █      █      █   █  █ █   █     │
│ ████   █████  █████  █      █      █   █  █  █  █████ │
╰───────────────────────────────────────────────────────╯
```

`ALFA-DEEPFAKE` - рабочее пространство для realtime demo/research пайплайна:
локальный ноутбук захватывает микрофон и камеру, отправляет поток на кластер,
кластер выполняет audio/video inference, а локальные проверки оценивают риск
виртуальной камеры, replay/freeze-сценариев и проблем с подписью потока.

## Состав репозитория

Этот репозиторий - верхнеуровневый workspace с сабмодулями:

- `deepfake-audio-video-inference` - realtime stream server/client, RVC audio
  inference, video adapter для Deep-Live-Cam.
- `deepfake-virtualcam-check` - explainable scoring для virtual camera, replay,
  freeze, suspicious timing/encoding и stream signatures.
- `deepfake-media-transport` - общий wire protocol и TCP framing для media
  gateway.
- `deepfake-stream-signature` - HMAC-based подпись stream packets для demo
  проверки целостности.
- `deepfake-riskapi` - FastAPI/MongoDB API для приема check statuses и score
  objects.

## Быстрый старт

Инициализировать сабмодули:

```bash
git submodule update --init --recursive
```

Создать локальный venv для клиента на ноутбуке:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-client.txt
```

Создать общий venv на кластере:

```bash
cd /home/master/work/alfa-deepfake
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Запуск

Основная точка входа:

```bash
python3 main.py
```

Launcher предлагает presets:

- `1. Media stream` - cluster server + SSH tunnel + local stream client.
- `2. Virtualcam check` - cluster server + local TCP proxy scoring +
  local stream client.
- `3. RiskAPI only` - только локальный RiskAPI.
- `4. Custom` - ручной выбор процессов.

Перед стартом launcher показывает план процессов: `cwd`, команды, ожидания
портов и локальные Python-модули, которые нужны клиенту.

## Типичный сценарий

Для обычного audio/video stream:

1. Запустить `python3 main.py`.
2. Выбрать preset `1`.
3. Оставить default SSH-настройки или изменить их.
4. При stale server на `13000` выбрать restart.
5. Подтвердить запуск.

Для проверки virtual camera:

1. Запустить `python3 main.py`.
2. Выбрать preset `2`.
3. В `Source device label` указать реальный источник, например
   `OBS Virtual Camera`.
4. Дождаться JSON score от `deepfake-virtualcam-check`.

Known virtual camera labels, например `OBS Virtual Camera`, считаются сильным
risk-сигналом и не должны разбавляться хорошим освещением или качественной
картинкой до `genuine`.

## Stream Signatures

Launcher поддерживает три режима:

- `off` - подпись не используется.
- `log` - сервер проверяет подписи, логирует плохие, но принимает packets.
- `block` - сервер проверяет подписи и отбрасывает плохие packets.

Для `log` и `block` launcher спросит shared secret и key id. Эти значения
передаются stream client/server и virtualcam-check proxy.

## Локальные и кластерные зависимости

Есть два requirements-файла:

- `requirements-client.txt` - легкий локальный набор для stream client:
  `numpy`, `opencv-python`, `Pillow`, `sounddevice`.
- `requirements.txt` - общий кластерный/runtime набор, который включает
  inference requirements, RiskAPI requirements и editable workspace-пакеты.

Launcher сначала ищет Python в корневом `.venv`, затем в `.venv` конкретного
сабмодуля, затем использует текущий Python. Для локального stream client есть
preflight: если нет `numpy`, `cv2`, `sounddevice` или `PIL`, запуск остановится
до старта SSH/кластера.

## Пути по умолчанию

Локальный workspace:

```text
/home/pinfoxxx/work/alfa-deepfake
```

Кластерный workspace:

```text
/home/master/work/alfa-deepfake
```

Inference-сабмодуль:

```text
deepfake-audio-video-inference
```

## Работа с сабмодулями

Сабмодули имеют собственные git-репозитории. Если меняется код внутри
сабмодуля, порядок такой:

```bash
cd deepfake-virtualcam-check
git add ...
git commit -m "Small focused message"

cd ..
git add deepfake-virtualcam-check
git commit -m "Update virtualcam check submodule"
```

Пушить тоже нужно отдельно: сначала сабмодули, потом parent repo.

## Диагностика

Проверить состояние workspace:

```bash
git status --short
git submodule status
```

Проверить, какой Python выберет launcher:

```bash
python3 - <<'PY'
from pathlib import Path
root = Path.cwd()
for path in [root / ".venv/bin/python", root / "deepfake-audio-video-inference/.venv/bin/python"]:
    print(path, path.exists())
PY
```

Если preset `2` зависает на ожидании `13000`, проверьте planned command:
restart должен использовать безопасный pattern:

```text
pkill -f '[b]ackend.media_gateway.stream_server'
```

Если там старый `pkill -f 'backend.media_gateway.stream_server'`, значит
запущена неактуальная копия `main.py`.
