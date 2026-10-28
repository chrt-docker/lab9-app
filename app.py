import os
import socket
import threading
import time

import redis
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# Имя этой реплики. По умолчанию — hostname контейнера (в Docker это короткий
# идентификатор контейнера, у каждой реплики он свой). Можно переопределить
# переменной INSTANCE_NAME.
INSTANCE_NAME = os.environ.get("INSTANCE_NAME") or socket.gethostname()

# Ключи в Redis.
TOTAL_KEY = "hits:total"
BY_INSTANCE_KEY = "hits:by_instance"

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Локальный счётчик живёт в памяти процесса ЭТОЙ реплики. Он нужен для
# демонстрации: у каждой реплики своя память, поэтому у разных реплик
# локальные счётчики расходятся, а счётчик в Redis — общий для всех.
_local_hits = 0
_local_lock = threading.Lock()


def register_hit():
    """Учитывает обращение: увеличивает локальный счётчик и счётчики в Redis."""
    global _local_hits
    with _local_lock:
        _local_hits += 1
        local = _local_hits

    pipe = redis_client.pipeline()
    pipe.incr(TOTAL_KEY)
    pipe.hincrby(BY_INSTANCE_KEY, INSTANCE_NAME, 1)
    total, _ = pipe.execute()
    return local, total


def get_stats():
    by_instance = {k: int(v) for k, v in redis_client.hgetall(BY_INSTANCE_KEY).items()}
    total = int(redis_client.get(TOTAL_KEY) or 0)
    return total, dict(sorted(by_instance.items()))


@app.route("/")
def index():
    local, total = register_hit()
    _, by_instance = get_stats()
    return render_template(
        "index.html",
        instance=INSTANCE_NAME,
        local_hits=local,
        total_hits=total,
        by_instance=by_instance,
        client_ip=request.remote_addr,
        forwarded_for=request.headers.get("X-Forwarded-For", "—"),
        real_ip=request.headers.get("X-Real-IP", "—"),
        host_header=request.headers.get("Host", "—"),
    )


@app.route("/whoami")
def whoami():
    """Короткий ответ в виде простого текста — удобно вызывать из curl в цикле."""
    register_hit()
    return INSTANCE_NAME + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/stats")
def stats():
    total, by_instance = get_stats()
    return jsonify(total=total, by_instance=by_instance, instances_seen=len(by_instance))


@app.route("/reset", methods=["POST"])
def reset():
    redis_client.delete(TOTAL_KEY, BY_INSTANCE_KEY)
    return jsonify(status="reset")


@app.route("/slow")
def slow():
    """Медленный запрос — имитирует долгую обработку (параметр seconds, максимум 10)."""
    try:
        seconds = min(float(request.args.get("seconds", "2")), 10.0)
    except ValueError:
        seconds = 2.0
    time.sleep(seconds)
    register_hit()
    return jsonify(instance=INSTANCE_NAME, slept=seconds)


@app.route("/health")
def health():
    try:
        redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        return jsonify(status="error", instance=INSTANCE_NAME, detail=str(exc)), 503
    return jsonify(status="ok", instance=INSTANCE_NAME)


if __name__ == "__main__":
    # threaded=True, чтобы одна реплика могла обрабатывать несколько запросов
    # одновременно (важно для проверки /slow).
    app.run(host="0.0.0.0", port=5000, threaded=True)
