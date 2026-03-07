from celery import Celery
from app.config import settings

# Cria a instância do Celery.
# O primeiro argumento é o nome do módulo atual.
# O `broker` é a URL de conexão do Redis, onde o Celery envia as mensagens de tarefa.
# O `backend` também é o Redis, onde o Celery armazena os resultados das tarefas.
celery_app = Celery(
    "tasks",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"]  # Lista de módulos onde o Celery deve procurar por tarefas.
)

# Configurações adicionais do Celery
celery_app.conf.update(
    task_track_started=True,
)
