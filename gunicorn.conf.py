import multiprocessing
import os

# Server socket
bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '5000')}"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = 'logs/access.log'
errorlog = 'logs/error.log'
loglevel = os.getenv('LOG_LEVEL', 'info').lower()

# Process naming
proc_name = 'github_data_fetcher'

# Server mechanics
daemon = False
pidfile = 'logs/gunicorn.pid'
user = None
group = None
umask = 0
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None