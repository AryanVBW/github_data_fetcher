# Ubuntu x86 Installation Guide

## System Requirements
- Ubuntu 20.04 LTS or newer (x86_64)
- Python 3.9 or newer
- Git
- 2GB RAM minimum (4GB recommended)
- 10GB free disk space

## Step 1: System Preparation

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y python3-pip python3-venv git build-essential python3-dev
```

## Step 2: Clone the Repository

```bash
# Clone the repository
git clone <your-repository-url>
cd github_data_fetcher
```

## Step 3: Python Environment Setup

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

## Step 4: Environment Configuration

1. Create a .env file:
```bash
touch .env
```

2. Add the following configuration to .env:
```
FLASK_APP=app.py
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=<your-secret-key>
GITHUB_CLIENT_ID=Ov23liDLs7SwLj0Mm5jS
GITHUB_CLIENT_SECRET=afaad438409e8572764a243eaccda565a5a447e2
PORT=5000
HOST=0.0.0.0
LOG_LEVEL=INFO
```

## Step 5: Directory Setup

```bash
# Create necessary directories
mkdir -p logs
mkdir -p reports
chmod 755 logs reports
```

## Step 6: Running the Application

### Method 1: Using Gunicorn (Recommended for Production)

```bash
# Start the application with gunicorn
gunicorn --config gunicorn.conf.py app:app
```

### Method 2: Using Flask Development Server (For Testing)

```bash
# Start Flask development server
flask run --host=0.0.0.0
```

## Step 7: Setting Up as a System Service

1. Create a systemd service file:
```bash
sudo nano /etc/systemd/system/github-fetcher.service
```

2. Add the following content:
```ini
[Unit]
Description=GitHub Data Fetcher
After=network.target

[Service]
User=<your-username>
Group=<your-group>
WorkingDirectory=/path/to/github_data_fetcher
Environment="PATH=/path/to/github_data_fetcher/venv/bin"
ExecStart=/path/to/github_data_fetcher/venv/bin/gunicorn --config gunicorn.conf.py app:app

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:
```bash
sudo systemctl enable github-fetcher
sudo systemctl start github-fetcher
```

## Step 8: Nginx Setup (Optional, for Production)

1. Install Nginx:
```bash
sudo apt install -y nginx
```

2. Create Nginx configuration:
```bash
sudo nano /etc/nginx/sites-available/github-fetcher
```

3. Add the configuration:
```nginx
server {
    listen 80;
    server_name your_domain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

4. Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/github-fetcher /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## Health Check

Access `http://localhost:5000` or your domain to verify the installation.

## Troubleshooting

1. Check logs:
```bash
# Application logs
tail -f logs/app.log

# Gunicorn logs
tail -f logs/error.log
tail -f logs/access.log
```

2. Check service status:
```bash
sudo systemctl status github-fetcher
```

3. Common issues:
- Port already in use: `sudo lsof -i :5000`
- Permission issues: Check ownership of directories
- Virtual environment issues: Ensure activation is correct

## Maintenance

1. Update application:
```bash
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart github-fetcher
```

2. Backup:
```bash
# Backup configuration
cp .env .env.backup
cp -r logs logs_backup
```

## Security Recommendations

1. Use UFW firewall:
```bash
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
```

2. Set proper file permissions:
```bash
chmod 600 .env
chmod 644 *.py
chmod 755 scripts/*.sh
```

3. Regular updates:
```bash
sudo apt update && sudo apt upgrade -y
```
