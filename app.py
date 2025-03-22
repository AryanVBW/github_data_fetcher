from flask import Flask, render_template, redirect, url_for, session, request, flash, jsonify
from functools import wraps
from github_data_fetcher import GitHubDataFetcher
import os
from datetime import datetime, timedelta
import secrets
import json

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# GitHub OAuth Configuration
app.config['GITHUB_CLIENT_ID'] = 'Ov23liDLs7SwLj0Mm5jS'
app.config['GITHUB_CLIENT_SECRET'] = 'afaad438409e8572764a243eaccda565a5a447e2'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session and 'access_token' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_github_fetcher():
    if 'access_token' not in session:
        return None
    return GitHubDataFetcher(token=session['access_token'])

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login')
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login/github')
def github_login():
    if not app.config['GITHUB_CLIENT_ID']:
        flash('GitHub client configuration is missing', 'danger')
        return redirect(url_for('login'))
    
    state = secrets.token_hex(16)
    session['oauth_state'] = state
    
    auth_url = f'https://github.com/login/oauth/authorize?client_id={app.config["GITHUB_CLIENT_ID"]}&state={state}&scope=repo,user'
    return redirect(auth_url)

@app.route('/login/github/callback')  # Updated route to match GitHub's callback URL
def github_callback():
    if 'error' in request.args:
        flash(f'Error during GitHub authentication: {request.args["error"]}', 'danger')
        return redirect(url_for('login'))
    
    if 'code' not in request.args or 'state' not in request.args:
        flash('Invalid GitHub callback parameters', 'danger')
        return redirect(url_for('login'))
    
    if request.args['state'] != session.get('oauth_state'):
        flash('Invalid OAuth state', 'danger')
        return redirect(url_for('login'))
    
    # Exchange code for access token
    try:
        import requests
        response = requests.post(
            'https://github.com/login/oauth/access_token',
            headers={'Accept': 'application/json'},
            data={
                'client_id': app.config['GITHUB_CLIENT_ID'],
                'client_secret': app.config['GITHUB_CLIENT_SECRET'],
                'code': request.args['code']
            }
        )
        data = response.json()
        
        if 'access_token' not in data:
            flash('Failed to get access token', 'danger')
            return redirect(url_for('login'))
        
        # Get user info
        user_response = requests.get(
            'https://api.github.com/user',
            headers={'Authorization': f'token {data["access_token"]}'}
        )
        user_data = user_response.json()
        
        session['access_token'] = data['access_token']
        session['user'] = user_data
        session.permanent = True
        
        # Initialize data fetcher
        fetcher = get_github_fetcher()
        if fetcher:
            fetcher.start_data_collection()
        
        flash('Successfully logged in with GitHub!', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        app.logger.error(f'Error during GitHub callback: {str(e)}')
        flash('Error processing GitHub login', 'danger')
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    fetcher = get_github_fetcher()
    if not fetcher:
        flash('GitHub connection error', 'danger')
        return redirect(url_for('login'))
    
    data = fetcher.get_collected_data()
    return render_template(
        'dashboard.html',
        active_page='dashboard',
        user=session['user'],
        data=data,
        show_refresh_button=True,
        auto_refresh=session.get('auto_refresh', False)
    )

@app.route('/repositories')
@login_required
def repositories():
    fetcher = get_github_fetcher()
    if not fetcher:
        flash('GitHub connection error', 'danger')
        return redirect(url_for('login'))
    
    data = fetcher.get_collected_data()
    return render_template(
        'repositories.html',
        active_page='repositories',
        user=session['user'],
        repositories=data.get('repositories', []),
        show_refresh_button=True,
        auto_refresh=session.get('auto_refresh', False)
    )

@app.route('/activity')
@login_required
def activity():
    fetcher = get_github_fetcher()
    if not fetcher:
        flash('GitHub connection error', 'danger')
        return redirect(url_for('login'))
    
    data = fetcher.get_collected_data()
    return render_template(
        'activity.html',
        active_page='activity',
        user=session['user'],
        recent_activity=data.get('recent_activity', []),
        show_refresh_button=True,
        auto_refresh=session.get('auto_refresh', False)
    )

@app.route('/refresh', methods=['POST'])
@login_required
def refresh_data():
    fetcher = get_github_fetcher()
    if not fetcher:
        return jsonify({'success': False, 'error': 'GitHub connection error'})
    
    try:
        if fetcher.refresh_data():
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to refresh data'})
    except Exception as e:
        app.logger.error(f'Error refreshing data: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/settings/auto-refresh', methods=['POST'])
@login_required
def toggle_auto_refresh():
    enabled = request.json.get('enabled', False)
    session['auto_refresh'] = enabled
    return jsonify({'success': True, 'auto_refresh': enabled})

@app.route('/demo')
def demo():
    # Load demo data
    try:
        with open('demo_data.json', 'r') as f:
            demo_data = json.load(f)
        session['demo'] = True
        session['demo_data'] = demo_data
        flash('Demo mode activated', 'info')
        return redirect(url_for('dashboard'))
    except Exception as e:
        app.logger.error(f'Error loading demo data: {str(e)}')
        flash('Error loading demo data', 'danger')
        return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)