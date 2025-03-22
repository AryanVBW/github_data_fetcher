#!/bin/bash

# Exit on error
set -e

echo "Starting GitHub Data Fetcher installation..."

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    echo "Please run without sudo"
    exit 1
fi

# Update system
echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "Installing system dependencies..."
sudo apt install -y python3-pip python3-venv git build-essential python3-dev

# Create virtual environment
echo "Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
echo "Creating required directories..."
mkdir -p logs
mkdir -p reports
chmod 755 logs reports

# Setup environment file
echo "Creating .env file..."
if [ ! -f .env ]; then
    cat > .env << EOL
FLASK_APP=app.py
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
GITHUB_CLIENT_ID=Ov23liDLs7SwLj0Mm5jS
GITHUB_CLIENT_SECRET=afaad438409e8572764a243eaccda565a5a447e2
PORT=5000
HOST=0.0.0.0
LOG_LEVEL=INFO
EOL
    chmod 600 .env
fi

# Set file permissions
echo "Setting file permissions..."
chmod 644 *.py
chmod 755 scripts/*.sh

# Create systemd service
echo "Setting up systemd service..."
sudo bash -c "cat > /etc/systemd/system/github-fetcher.service << EOL
[Unit]
Description=GitHub Data Fetcher
After=network.target

[Service]
User=$USER
Group=$USER
WorkingDirectory=$PWD
Environment=\"PATH=$PWD/venv/bin\"
ExecStart=$PWD/venv/bin/gunicorn --config gunicorn.conf.py app:app

[Install]
WantedBy=multi-user.target
EOL"

# Enable and start service
echo "Enabling and starting service..."
sudo systemctl enable github-fetcher
sudo systemctl start github-fetcher

echo "Installation complete!"
echo "You can check the service status with: sudo systemctl status github-fetcher"
echo "Access the application at: http://localhost:5000"