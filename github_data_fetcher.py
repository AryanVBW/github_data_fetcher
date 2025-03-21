import requests
import datetime
import time
import os
import sys
import signal
import json
import logging
from dateutil import parser
from collections import Counter
import concurrent.futures
from tqdm import tqdm
import atexit
import threading
import flask
from flask import Flask, render_template, jsonify, send_from_directory
import socket
import webbrowser

class GitHubDataFetcher:
    def __init__(self, token):
        """Initialize with GitHub personal access token."""
        self.token = token
        self.headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        self.base_url = 'https://api.github.com'
        self.user_data = None
        self.rate_limit_remaining = None
        self.rate_limit_reset = None
        
        # Create directories
        self.cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        self.report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        self.templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        self.static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
        
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.report_dir, exist_ok=True)
        os.makedirs(self.templates_dir, exist_ok=True)
        os.makedirs(self.static_dir, exist_ok=True)
        
        # Set up logging
        self.setup_logging()
        self.logger = logging.getLogger('github_fetcher')
        
        # For incremental data collection
        self.collected_data = {
            'profile': None,
            'repositories': [],
            'language_stats': Counter(),
            'total_commits': 0,
            'total_prs': 0,
            'total_stars': 0,
            'processed_repos': set(),  # Keep track of processed repos
            'last_updated': time.time(),
            'current_status': 'Initializing',
            'progress': {
                'total_repos': 0,
                'processed_repos': 0,
                'percentage': 0,
                'current_repo': '',
                'rate_limit': 0,
                'eta': 'Unknown'
            }
        }
        
        # Load any existing data
        self.load_checkpoint()
        
        # Register signal handlers and exit handlers
        self.register_handlers()
        
        # Initialize web server
        self.web_server_thread = None
        self.web_server_port = self.find_available_port(5000, 5100)
        self.web_server_url = f"http://localhost:{self.web_server_port}"
        self.start_web_server()

    def find_available_port(self, start_port, end_port):
        """Find an available port in the given range."""
        for port in range(start_port, end_port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('localhost', port)) != 0:
                    return port
        return start_port  # Fallback to start_port if none are available

    def start_web_server(self):
        """Start the web server in a separate thread."""
        # Create Flask templates
        self.create_flask_templates()
        
        def run_server():
            app = Flask(__name__, 
                        template_folder=self.templates_dir,
                        static_folder=self.static_dir)
            
            @app.route('/')
            def home():
                return render_template('index.html')
            
            @app.route('/api/data')
            def get_data():
                # Update progress information
                repos_count = len(self.collected_data['repositories'])
                processed_count = len(self.collected_data['processed_repos'])
                total_count = self.collected_data['progress']['total_repos']
                
                if total_count > 0:
                    percentage = int((processed_count / total_count) * 100)
                else:
                    percentage = 0
                    
                # Format the data for JSON response
                response_data = {
                    'profile': self.collected_data['profile'],
                    'stats': {
                        'total_repos': repos_count,
                        'total_commits': self.collected_data['total_commits'],
                        'total_prs': self.collected_data['total_prs'],
                        'total_stars': self.collected_data['total_stars']
                    },
                    'progress': {
                        'total_repos': total_count,
                        'processed_repos': processed_count,
                        'percentage': percentage,
                        'current_repo': self.collected_data['progress']['current_repo'],
                        'status': self.collected_data['current_status'],
                        'rate_limit': self.rate_limit_remaining,
                        'eta': self.collected_data['progress']['eta']
                    },
                    'languages': [
                        {'name': lang, 'value': bytes_count}
                        for lang, bytes_count in self.collected_data['language_stats'].most_common(10)
                    ],
                    'recent_repos': [
                        {
                            'name': repo['name'],
                            'full_name': repo['full_name'],
                            'url': repo['url'],
                            'description': repo.get('description', ''),
                            'stars': repo['stargazers_count'],
                            'forks': repo['forks_count'],
                            'language': repo.get('language', '')
                        }
                        for repo in sorted(self.collected_data['repositories'], 
                                          key=lambda x: x['updated_at'], 
                                          reverse=True)[:10]
                    ]
                }
                
                return jsonify(response_data)
            
            @app.route('/reports/<path:filename>')
            def reports(filename):
                return send_from_directory(self.report_dir, filename)
            
            app.run(host='localhost', port=self.web_server_port, debug=False)
        
        # Start the server in a separate thread
        self.web_server_thread = threading.Thread(target=run_server, daemon=True)
        self.web_server_thread.start()
        
        # Open the web browser
        webbrowser.open(self.web_server_url)
        self.logger.info(f"Web server started at {self.web_server_url}")

    def create_flask_templates(self):
        """Create necessary Flask template files."""
        index_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Data Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            background-color: #f8f9fa;
        }
        .stats-card {
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            background-color: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.3s ease;
        }
        .stats-card:hover {
            transform: translateY(-5px);
        }
        .chart-container {
            position: relative;
            height: 250px;
            width: 100%;
        }
        .progress-card {
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            background-color: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .progress {
            height: 25px;
            border-radius: 15px;
            margin-bottom: 15px;
        }
        .progress-bar {
            background-color: #28a745;
        }
        .repo-card {
            border-radius: 10px;
            margin-bottom: 15px;
            background-color: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.2s ease;
        }
        .repo-card:hover {
            transform: translateY(-3px);
        }
        .icon-stat {
            font-size: 2rem;
            margin-bottom: 10px;
            color: #007bff;
        }
        .animate-pulse {
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% {
                opacity: 1;
            }
            50% {
                opacity: 0.5;
            }
            100% {
                opacity: 1;
            }
        }
        .status-badge {
            font-size: 0.9rem;
            padding: 5px 10px;
        }
    </style>
</head>
<body>
    <div class="container-fluid py-4">
        <div class="row mb-4">
            <div class="col-12">
                <div class="d-flex justify-content-between align-items-center">
                    <h1 class="display-5"><i class="bi bi-github me-2"></i>GitHub Data Dashboard</h1>
                    <span id="last-updated" class="text-muted">Updating...</span>
                </div>
                <p class="lead">Real-time GitHub data collection and analysis</p>
            </div>
        </div>
        
        <!-- Progress Section -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="progress-card">
                    <div class="d-flex justify-content-between">
                        <h3>Collection Progress</h3>
                        <span id="status-badge" class="badge bg-primary status-badge">Initializing</span>
                    </div>
                    <div class="progress mt-3">
                        <div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated" 
                             role="progressbar" style="width: 0%" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">0%</div>
                    </div>
                    <div class="d-flex justify-content-between mt-2">
                        <span id="current-repo" class="text-muted">Preparing...</span>
                        <span id="progress-count">0/0 repositories</span>
                    </div>
                    <div class="d-flex justify-content-between mt-2">
                        <span id="rate-limit" class="text-muted">Rate limit: checking...</span>
                        <span id="eta">ETA: calculating...</span>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Profile and Stats Section -->
        <div class="row mb-4">
            <!-- Profile Card -->
            <div class="col-md-4">
                <div class="stats-card h-100">
                    <h3>Profile</h3>
                    <div id="profile-content">
                        <div class="text-center py-4">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="mt-2">Loading profile data...</p>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Stats Cards -->
            <div class="col-md-8">
                <div class="row">
                    <div class="col-md-6 col-sm-6">
                        <div class="stats-card text-center">
                            <div class="icon-stat">
                                <i class="bi bi-git"></i>
                            </div>
                            <h3 id="repo-count">0</h3>
                            <p class="text-muted">Repositories</p>
                        </div>
                    </div>
                    <div class="col-md-6 col-sm-6">
                        <div class="stats-card text-center">
                            <div class="icon-stat">
                                <i class="bi bi-code-square"></i>
                            </div>
                            <h3 id="commit-count">0</h3>
                            <p class="text-muted">Commits</p>
                        </div>
                    </div>
                    <div class="col-md-6 col-sm-6">
                        <div class="stats-card text-center">
                            <div class="icon-stat">
                                <i class="bi bi-git-pull-request"></i>
                            </div>
                            <h3 id="pr-count">0</h3>
                            <p class="text-muted">Pull Requests</p>
                        </div>
                    </div>
                    <div class="col-md-6 col-sm-6">
                        <div class="stats-card text-center">
                            <div class="icon-stat">
                                <i class="bi bi-star"></i>
                            </div>
                            <h3 id="star-count">0</h3>
                            <p class="text-muted">Stars</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Charts Row -->
        <div class="row mb-4">
            <div class="col-md-12">
                <div class="stats-card">
                    <h3>Language Distribution</h3>
                    <div class="chart-container">
                        <canvas id="languageChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Recent Repositories -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="stats-card">
                    <h3>Recent Repositories</h3>
                    <div id="repo-list" class="row mt-3">
                        <div class="col-12 text-center py-4">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="mt-2">Loading repositories...</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="row">
            <div class="col-12 text-center mt-4 mb-3">
                <p class="text-muted">
                    <a href="/reports/live_report.html" target="_blank" class="btn btn-outline-primary">View Full HTML Report</a>
                    <a href="/reports/github_report.md" target="_blank" class="btn btn-outline-secondary ms-2">View Markdown Report</a>
                </p>
                <p class="text-muted small">
                    Made with <i class="bi bi-heart-fill text-danger"></i> using GitHub API
                </p>
            </div>
        </div>
    </div>
    
    <script>
        // Chart objects
        let languageChart = null;
        
        // Function to initialize charts
        function initializeCharts() {
            const ctx = document.getElementById('languageChart').getContext('2d');
            languageChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: [],
                    datasets: [{
                        data: [],
                        backgroundColor: [
                            '#ff6384', '#36a2eb', '#ffce56', '#4bc0c0', '#9966ff',
                            '#ff9f40', '#8ac926', '#1982c4', '#6a4c93', '#f94144'
                        ],
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '60%',
                    plugins: {
                        legend: {
                            position: 'right',
                        }
                    }
                }
            });
        }
        
        // Function to update the UI with data
        function updateUI(data) {
            // Update profile
            if (data.profile) {
                const profileContent = document.getElementById('profile-content');
                profileContent.innerHTML = `
                    <p><strong>Name:</strong> ${data.profile.name || 'Not set'}</p>
                    <p><strong>Username:</strong> ${data.profile.username}</p>
                    <p><strong>Bio:</strong> ${data.profile.bio || 'Not set'}</p>
                    <p><strong>Location:</strong> ${data.profile.location || 'Not set'}</p>
                    <p><strong>Company:</strong> ${data.profile.company || 'Not set'}</p>
                    <p><strong>Followers:</strong> ${data.profile.followers}</p>
                    <p><strong>Following:</strong> ${data.profile.following}</p>
                `;
            }
            
            // Update stats counters
            document.getElementById('repo-count').textContent = data.stats.total_repos.toLocaleString();
            document.getElementById('commit-count').textContent = data.stats.total_commits.toLocaleString();
            document.getElementById('pr-count').textContent = data.stats.total_prs.toLocaleString();
            document.getElementById('star-count').textContent = data.stats.total_stars.toLocaleString();
            
            // Update progress
            const progressBar = document.getElementById('progress-bar');
            const percentage = data.progress.percentage;
            progressBar.style.width = `${percentage}%`;
            progressBar.textContent = `${percentage}%`;
            progressBar.setAttribute('aria-valuenow', percentage);
            
            document.getElementById('current-repo').textContent = data.progress.current_repo || 'Not processing';
            document.getElementById('progress-count').textContent = `${data.progress.processed_repos} / ${data.progress.total_repos} repositories`;
            document.getElementById('rate-limit').textContent = `Rate limit: ${data.progress.rate_limit || 'Unknown'}`;
            document.getElementById('eta').textContent = `ETA: ${data.progress.eta || 'Unknown'}`;
            
            // Update status badge
            const statusBadge = document.getElementById('status-badge');
            statusBadge.textContent = data.progress.status;
            
            // Change badge color based on status
            if (data.progress.status === 'Completed') {
                statusBadge.className = 'badge bg-success status-badge';
            } else if (data.progress.status === 'Error') {
                statusBadge.className = 'badge bg-danger status-badge';
            } else if (data.progress.status === 'Paused' || data.progress.status === 'Rate Limited') {
                statusBadge.className = 'badge bg-warning status-badge';
            } else {
                statusBadge.className = 'badge bg-primary status-badge';
            }
            
            // Update language chart
            if (data.languages && data.languages.length > 0) {
                // Prepare data
                const labels = data.languages.map(l => l.name);
                const values = data.languages.map(l => l.value);
                
                // Update chart
                languageChart.data.labels = labels;
                languageChart.data.datasets[0].data = values;
                languageChart.update();
            }
            
            // Update repo list
            if (data.recent_repos && data.recent_repos.length > 0) {
                const repoList = document.getElementById('repo-list');
                repoList.innerHTML = '';
                
                data.recent_repos.forEach(repo => {
                    const repoCard = document.createElement('div');
                    repoCard.className = 'col-md-6 mb-3';
                    repoCard.innerHTML = `
                        <div class="repo-card p-3">
                            <h5><a href="${repo.url}" target="_blank">${repo.name}</a></h5>
                            <p class="text-muted small">${repo.full_name}</p>
                            <p>${repo.description || 'No description'}</p>
                            <div class="d-flex justify-content-between">
                                <span><i class="bi bi-star-fill text-warning"></i> ${repo.stars}</span>
                                <span><i class="bi bi-diagram-2"></i> ${repo.forks}</span>
                                <span>${repo.language || 'Not specified'}</span>
                            </div>
                        </div>
                    `;
                    repoList.appendChild(repoCard);
                });
            }
            
            // Update last updated timestamp
            document.getElementById('last-updated').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
        }
        
        // Function to fetch data from API
        function fetchData() {
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    updateUI(data);
                    
                    // If not completed, schedule next update
                    if (data.progress.status !== 'Completed' && data.progress.status !== 'Error') {
                        setTimeout(fetchData, 2000);
                    } else {
                        // For completed or error, slow down updates
                        setTimeout(fetchData, 10000);
                    }
                })
                .catch(error => {
                    console.error('Error fetching data:', error);
                    setTimeout(fetchData, 5000); // Retry after 5 seconds on error
                });
        }
        
        // Initialize when page loads
        document.addEventListener('DOMContentLoaded', function() {
            initializeCharts();
            fetchData();
        });
    </script>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""
        
        # Write the template file
        with open(os.path.join(self.templates_dir, 'index.html'), 'w', encoding='utf-8') as f:
            f.write(index_html)

    def setup_logging(self):
        """Set up logging configuration."""
        log_file = os.path.join(self.log_dir, f'github_fetcher_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        logging.info(f"Logging initialized. Log file: {log_file}")

    def register_handlers(self):
        """Register signal and exit handlers for graceful shutdown."""
        # Handle keyboard interrupt and termination signals
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Register a function to run on normal exit
        atexit.register(self.save_checkpoint)
        
    def signal_handler(self, sig, frame):
        """Handle termination signals by saving progress."""
        self.logger.warning(f"Received signal {sig}. Saving progress and generating report...")
        self.collected_data['current_status'] = 'Interrupted'
        self.save_checkpoint()
        self.generate_live_html_report()
        sys.exit(0)
        
    def save_checkpoint(self):
        """Save current progress to a checkpoint file."""
        checkpoint_path = os.path.join(self.cache_dir, 'checkpoint.json')
        
        # Convert Counter to dict for JSON serialization
        data_to_save = self.collected_data.copy()
        data_to_save['language_stats'] = dict(data_to_save['language_stats'])
        data_to_save['processed_repos'] = list(data_to_save['processed_repos'])
        
        try:
            with open(checkpoint_path, 'w') as f:
                json.dump(data_to_save, f)
            self.logger.info(f"Progress saved to checkpoint file: {checkpoint_path}")
        except Exception as e:
            self.logger.error(f"Failed to save checkpoint: {e}")
    
    def load_checkpoint(self):
        """Load progress from a checkpoint file if it exists."""
        checkpoint_path = os.path.join(self.cache_dir, 'checkpoint.json')
        
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, 'r') as f:
                    data = json.load(f)
                
                # Convert dict back to Counter
                data['language_stats'] = Counter(data['language_stats'])
                data['processed_repos'] = set(data['processed_repos'])
                
                self.collected_data = data
                self.logger.info(f"Loaded checkpoint with {len(data['repositories'])} repositories")
                
                # Generate an initial HTML report from loaded data
                self.generate_live_html_report()
                
            except Exception as e:
                self.logger.error(f"Failed to load checkpoint: {e}")

    def _get_cache_path(self, key):
        """Get the file path for a cache item."""
        # Sanitize key for use as a filename
        safe_key = "".join([c if c.isalnum() else "_" for c in key])
        return os.path.join(self.cache_dir, f"{safe_key}.json")

    def _cache_get(self, key):
        """Get item from cache if it exists and is not expired."""
        cache_path = self._get_cache_path(key)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cached_data = json.load(f)
                    # Check if cache is less than 1 hour old
                    if time.time() - cached_data.get('timestamp', 0) < 3600:
                        return cached_data.get('data')
            except Exception as e:
                self.logger.warning(f"Cache read error: {e}")
        return None

    def _cache_set(self, key, data):
        """Save item to cache."""
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, 'w') as f:
                json.dump({
                    'timestamp': time.time(),
                    'data': data
                }, f)
        except Exception as e:
            self.logger.warning(f"Cache write error: {e}")

    def check_rate_limit(self):
        """Check and handle GitHub API rate limits."""
        # First check if we already have a rate limit stored
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            wait_time = self.rate_limit_reset - time.time()
            if wait_time > 0:
                self.collected_data['current_status'] = 'Rate Limited'
                self.logger.warning(f"Rate limit almost reached. Waiting for {wait_time:.2f} seconds...")
                # Update the web interface
                self.collected_data['progress']['eta'] = f"{wait_time:.2f} seconds until reset"
                time.sleep(wait_time + 1)  # Add a buffer second
                self.collected_data['current_status'] = 'Active'

        # Get cached rate limit if available
        cached_limit = self._cache_get('rate_limit')
        if cached_limit:
            self.rate_limit_remaining = cached_limit['resources']['core']['remaining']
            self.rate_limit_reset = cached_limit['resources']['core']['reset']
            
            # Only make a new request if the cached value is getting low
            if self.rate_limit_remaining > 20:
                self.logger.debug(f"Rate limit remaining (cached): {self.rate_limit_remaining}")
                return self.rate_limit_remaining

        # Make a new request to check rate limit
        try:
            response = requests.get(
                f"{self.base_url}/rate_limit", 
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                limits = response.json()
                self.rate_limit_remaining = limits['resources']['core']['remaining']
                self.rate_limit_reset = limits['resources']['core']['reset']
                self._cache_set('rate_limit', limits)
                self.logger.info(f"Rate limit remaining: {self.rate_limit_remaining}")
                
                # Update the rate limit in progress data
                self.collected_data['progress']['rate_limit'] = self.rate_limit_remaining
                
                # If rate limit is very low, update status
                if self.rate_limit_remaining < 20:
                    reset_time = datetime.datetime.fromtimestamp(self.rate_limit_reset).strftime('%H:%M:%S')
                    self.collected_data['progress']['eta'] = f"Rate limit resets at {reset_time}"
            else:
                self.logger.error(f"Failed to check rate limit: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed when checking rate limit: {e}")
            
        return self.rate_limit_remaining

    def _make_request(self, endpoint, params=None, use_cache=True):
        """Make a request with caching and rate limit handling."""
        cache_key = f"{endpoint}_{str(params)}"
        if use_cache:
            cached_data = self._cache_get(cache_key)
            if cached_data:
                self.logger.debug(f"Cache hit for {endpoint}")
                return cached_data

        self.check_rate_limit()
        self.logger.debug(f"Requesting: {endpoint} with params {params}")
        
        try:
            response = requests.get(
                f"{self.base_url}/{endpoint}",
                headers=self.headers,
                params=params,
                timeout=30  # Add timeout to prevent hanging requests
            )
            
            if response.status_code == 200:
                data = response.json()
                if use_cache:
                    self._cache_set(cache_key, data)
                return data
            elif response.status_code == 202:
                # For stats endpoints that return 202, don't treat as error
                self.logger.info(f"Server is preparing data for {endpoint} (202)")
                return response.status_code
            else:
                self.logger.error(f"Error fetching {endpoint}: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed for {endpoint}: {e}")
            return None

    def get_user_data(self):
        """Fetch authenticated user's profile data."""
        if self.user_data:
            return self.user_data
        
        data = self._make_request("user")
        if data:
            self.user_data = data
        return self.user_data

    def get_repositories(self):
        """Fetch all repositories (public and private)."""
        self.logger.info("Fetching repositories...")
        self.collected_data['current_status'] = 'Fetching Repositories'
        all_repos = []
        page = 1
        
        with tqdm(desc="Fetching repositories", unit="page") as pbar:
            while True:
                repos = self._make_request(
                    "user/repos",
                    params={
                        'per_page': 100,
                        'page': page,
                        'sort': 'updated',
                        'affiliation': 'owner,collaborator,organization_member'
                    }
                )
                
                if not repos or len(repos) == 0:
                    break
                    
                all_repos.extend(repos)
                page += 1
                pbar.update(1)
                pbar.set_postfix({"total_repos": len(all_repos)})
                
                # Update the progress tracker
                self.collected_data['progress']['total_repos'] = len(all_repos)
                    
        self.logger.info(f"Fetched {len(all_repos)} repositories")
        return all_repos

    def get_pull_requests(self, repo_name):
        """Fetch all pull requests for a repository."""
        self.logger.debug(f"Fetching PRs for {repo_name}")
        all_prs = []
        page = 1
        while True:
            prs = self._make_request(
                f"repos/{repo_name}/pulls",
                params={
                    'state': 'all',
                    'per_page': 100,
                    'page': page
                }
            )
            
            if not prs or len(prs) == 0:
                break
                
            all_prs.extend(prs)
            page += 1
                
        self.logger.debug(f"Fetched {len(all_prs)} PRs for {repo_name}")
        return all_prs

    def get_commits(self, repo_name):
        """Fetch all commits for a repository."""
        self.logger.debug(f"Fetching commits for {repo_name}")
        all_commits = []
        page = 1
        while True:
            commits = self._make_request(
                f"repos/{repo_name}/commits",
                params={
                    'per_page': 100,
                    'page': page
                }
            )
            
            if not commits or len(commits) == 0:
                break
                
            all_commits.extend(commits)
            page += 1
                
        self.logger.debug(f"Fetched {len(all_commits)} commits for {repo_name}")
        return all_commits

    def get_languages(self, repo_name):
        """Get languages used in a repository."""
        self.logger.debug(f"Fetching languages for {repo_name}")
        return self._make_request(f"repos/{repo_name}/languages") or {}

    def get_comments(self, repo_name):
        """Get recent comments in a repository."""
        self.logger.debug(f"Fetching comments for {repo_name}")
        return self._make_request(
            f"repos/{repo_name}/comments",
            params={'per_page': 50}
        ) or []

    def get_stargazers(self, repo_name):
        """Get users who starred the repository."""
        self.logger.debug(f"Fetching stargazers for {repo_name}")
        all_stars = []
        page = 1
        while True:
            stars = self._make_request(
                f"repos/{repo_name}/stargazers",
                params={
                    'per_page': 100,
                    'page': page
                }
            )
            
            if not stars or len(stars) == 0:
                break
                
            all_stars.extend(stars)
            page += 1
                
        self.logger.debug(f"Fetched {len(all_stars)} stargazers for {repo_name}")
        return all_stars

    def get_repository_activity(self, repo_name):
        """Get activity data for a repository."""
        activity = self._make_request(f"repos/{repo_name}/stats/contributors")
        
        # Handle the 202 status code specially (data being prepared)
        if activity == 202:
            self.logger.info(f"Data for {repo_name} is being prepared. Will use empty data.")
            return []  # Return empty list instead of retrying
            
        return activity or []

    def process_repository(self, repo):
        """Process a single repository to collect all its data."""
        repo_name = repo['full_name']
        
        # Skip if already processed
        if repo_name in self.collected_data['processed_repos']:
            self.logger.info(f"Skipping already processed repo: {repo_name}")
            return None
            
        self.logger.info(f"Processing repo: {repo_name}")
        # Update current repository in progress
        self.collected_data['progress']['current_repo'] = repo_name
        
        try:
            # Use thread pool to fetch data in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                # Submit all tasks
                commits_future = executor.submit(self.get_commits, repo_name)
                prs_future = executor.submit(self.get_pull_requests, repo_name)
                languages_future = executor.submit(self.get_languages, repo_name)
                comments_future = executor.submit(self.get_comments, repo_name)
                stars_future = executor.submit(self.get_stargazers, repo_name)
                activity_future = executor.submit(self.get_repository_activity, repo_name)
                
                # Create a progress bar for this repository's data fetching
                with tqdm(total=6, desc=f"Fetching data for {repo_name}", leave=False) as pbar:
                    # Get results from futures with exception handling
                    try:
                        commits = commits_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching commits for {repo_name}: {e}")
                        commits = []
                        pbar.update(1)
                        
                    try:
                        pull_requests = prs_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching PRs for {repo_name}: {e}")
                        pull_requests = []
                        pbar.update(1)
                        
                    try:
                        languages = languages_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching languages for {repo_name}: {e}")
                        languages = {}
                        pbar.update(1)
                        
                    try:
                        comments = comments_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching comments for {repo_name}: {e}")
                        comments = []
                        pbar.update(1)
                        
                    try:
                        stars = stars_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching stars for {repo_name}: {e}")
                        stars = []
                        pbar.update(1)
                        
                    try:
                        activity = activity_future.result()
                        pbar.update(1)
                    except Exception as e:
                        self.logger.error(f"Error fetching activity for {repo_name}: {e}")
                        activity = []
                        pbar.update(1)
            
            # Create the repository data structure
            repo_data = {
                'name': repo['name'],
                'full_name': repo['full_name'],
                'description': repo.get('description', ''),
                'url': repo['html_url'],
                'created_at': repo['created_at'],
                'updated_at': repo['updated_at'],
                'pushed_at': repo['pushed_at'],
                'size': repo['size'],
                'stargazers_count': repo['stargazers_count'],
                'watchers_count': repo['watchers_count'],
                'language': repo.get('language', ''),
                'languages': languages,
                'forks_count': repo['forks_count'],
                'open_issues_count': repo['open_issues_count'],
                'is_private': repo['private'],
                'commits': [
                    {
                        'sha': commit['sha'],
                        'author': commit['commit']['author']['name'],
                        'email': commit['commit']['author']['email'],
                        'date': commit['commit']['author']['date'],
                        'message': commit['commit']['message']
                    }
                    for commit in commits
                ],
                'pull_requests': [
                    {
                        'id': pr['id'],
                        'number': pr['number'],
                        'title': pr['title'],
                        'state': pr['state'],
                        'created_at': pr['created_at'],
                        'updated_at': pr['updated_at'],
                        'closed_at': pr.get('closed_at', ''),
                        'merged_at': pr.get('merged_at', ''),
                        'user': pr['user']['login']
                    }
                    for pr in pull_requests
                ],
                'comments': [
                    {
                        'id': comment['id'],
                        'user': comment['user']['login'],
                        'created_at': comment['created_at'],
                        'updated_at': comment['updated_at'],
                        'body': comment['body']
                    }
                    for comment in comments
                ],
                'activity': activity
            }
            
            # Mark repository as processed
            self.collected_data['processed_repos'].add(repo_name)
            
            self.logger.info(f"Completed processing repo: {repo_name}")
            
            # Generate updated HTML report periodically (every 10 minutes or after 5 new repos)
            current_time = time.time()
            if (current_time - self.collected_data['last_updated'] > 600 or 
                len(self.collected_data['repositories']) % 5 == 0):
                self.generate_live_html_report()
                self.collected_data['last_updated'] = current_time
                self.save_checkpoint()
            
            return {
                'repo_data': repo_data,
                'commits_count': len(commits),
                'prs_count': len(pull_requests),
                'stars_count': repo['stargazers_count'],
                'languages': languages
            }
            
        except Exception as e:
            self.logger.error(f"Error processing repo {repo_name}: {e}")
            return None

    def collect_all_data(self):
        """Collect all GitHub data and return a structured dict."""
        self.logger.info("Starting data collection process")
        self.collected_data['current_status'] = 'Starting'
        
        # Get user profile if not already loaded
        if not self.collected_data['profile']:
            self.logger.info("Fetching user profile data...")
            user = self.get_user_data()
            if not user:
                self.logger.error("Failed to fetch user data")
                self.collected_data['current_status'] = 'Error'
                return None
                
            self.collected_data['profile'] = {
                'username': user['login'],
                'name': user.get('name', ''),
                'bio': user.get('bio', ''),
                'company': user.get('company', ''),
                'location': user.get('location', ''),
                'email': user.get('email', ''),
                'public_repos': user['public_repos'],
                'followers': user['followers'],
                'following': user['following'],
                'created_at': user['created_at'],
            }
            self.logger.info(f"Fetched profile for {self.collected_data['profile']['username']}")
            
            # Generate an initial HTML report with just the profile
            self.generate_live_html_report()
        
        # Get repositories
        self.collected_data['current_status'] = 'Fetching Repository List'
        repos = self.get_repositories()
        
        if not repos:
            self.logger.error("Failed to fetch repositories")
            self.collected_data['current_status'] = 'Error'
            return self.collected_data
        
        # Process repositories in parallel to improve performance
        max_workers = min(10, len(repos))  # Limit workers to avoid excessive API calls
        self.logger.info(f"Processing {len(repos)} repositories with {max_workers} workers...")
        
        # Count repos that still need processing
        remaining_repos = [r for r in repos if r['full_name'] not in self.collected_data['processed_repos']]
        
        # Update progress information
        self.collected_data['progress']['total_repos'] = len(repos)
        self.collected_data['progress']['processed_repos'] = len(self.collected_data['processed_repos'])
        
        # Calculate an estimated completion time
        if remaining_repos:
            # Estimate 30 seconds per repository as a rough guess
            estimated_seconds = len(remaining_repos) * 30
            estimated_completion = datetime.datetime.now() + datetime.timedelta(seconds=estimated_seconds)
            self.collected_data['progress']['eta'] = estimated_completion.strftime('%H:%M:%S')
        
        self.collected_data['current_status'] = 'Processing Repositories'
        
        with tqdm(total=len(remaining_repos), desc="Processing repositories") as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Process repos in parallel
                futures = {
                    executor.submit(self.process_repository, repo): repo['full_name'] 
                    for repo in remaining_repos
                }
                
                completed_count = 0
                for future in concurrent.futures.as_completed(futures):
                    repo_name = futures[future]
                    try:
                        result = future.result()
                        if result:
                            self.collected_data['repositories'].append(result['repo_data'])
                            
                            # Update counters
                            self.collected_data['total_commits'] += result['commits_count']
                            self.collected_data['total_prs'] += result['prs_count']
                            self.collected_data['total_stars'] += result['stars_count']
                            
                            # Update language stats
                            for lang, bytes_count in result['languages'].items():
                                self.collected_data['language_stats'][lang] += bytes_count
                        
                        # Always update progress bar
                        pbar.update(1)
                        pbar.set_postfix({
                            "commits": self.collected_data['total_commits'], 
                            "PRs": self.collected_data['total_prs'],
                            "stars": self.collected_data['total_stars']
                        })
                        
                        # Update progress information
                        completed_count += 1
                        self.collected_data['progress']['processed_repos'] = len(self.collected_data['processed_repos'])
                        
                        if len(remaining_repos) > 0:
                            progress_percentage = int((completed_count / len(remaining_repos)) * 100)
                            self.collected_data['progress']['percentage'] = progress_percentage
                            
                            # Update ETA
                            if completed_count > 0:
                                avg_time_per_repo = (time.time() - self.collected_data['last_updated']) / completed_count
                                remaining_time = avg_time_per_repo * (len(remaining_repos) - completed_count)
                                
                                if remaining_time > 3600:
                                    eta = f"{remaining_time/3600:.1f} hours"
                                elif remaining_time > 60:
                                    eta = f"{remaining_time/60:.1f} minutes"
                                else:
                                    eta = f"{remaining_time:.0f} seconds"
                                    
                                self.collected_data['progress']['eta'] = eta
                        
                    except Exception as e:
                        self.logger.error(f"Error in future for repo {repo_name}: {e}")
                        pbar.update(1)
        
        # Final save and report generation
        self.collected_data['current_status'] = 'Completed'
        self.save_checkpoint()
        self.generate_live_html_report()
        
        self.logger.info("Data collection completed successfully")
        return self.collected_data

    def generate_live_html_report(self):
        """Generate a live HTML report that can be viewed during collection."""
        data = self.collected_data
        
        if not data['profile']:
            self.logger.warning("No profile data available for HTML report")
            return
            
        html_path = os.path.join(self.report_dir, 'live_report.html')
        
        # Format the timestamps
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate language percentages
        language_data = []
        total_bytes = sum(data['language_stats'].values())
        
        if total_bytes > 0:
            for language, bytes_count in data['language_stats'].most_common(10):
                percentage = (bytes_count / total_bytes) * 100
                language_data.append({
                    'language': language,
                    'bytes': bytes_count,
                    'percentage': percentage
                })
        
        # Generate repository HTML
        repos_html = ""
        for repo in data['repositories']:
            # Format dates
            created_at = parser.parse(repo['created_at']).strftime('%Y-%m-%d')
            updated_at = parser.parse(repo['updated_at']).strftime('%Y-%m-%d')
            
            # Format languages
            lang_html = ""
            if repo['languages']:
                lang_html = "<ul>"
                for lang, bytes_count in repo['languages'].items():
                    lang_html += f"<li>{lang}: {bytes_count:,} bytes</li>"
                lang_html += "</ul>"
            
            # Format commits
            commits_html = ""
            if repo['commits']:
                commits_html = "<h4>Recent Commits</h4><table class='table table-sm'>"
                commits_html += "<thead><tr><th>Date</th><th>Author</th><th>Message</th></tr></thead><tbody>"
                
                # Sort commits by date
                sorted_commits = sorted(repo['commits'], key=lambda x: x['date'], reverse=True)
                for commit in sorted_commits[:5]:  # Show only 5 most recent
                    date = parser.parse(commit['date']).strftime('%Y-%m-%d')
                    message = commit['message'].replace('\n', ' ')
                    if len(message) > 50:
                        message = message[:47] + "..."
                    
                    commits_html += f"<tr><td>{date}</td><td>{commit['author']}</td><td>{message}</td></tr>"
                
                commits_html += "</tbody></table>"
            
            # Add repository card to HTML
            repos_html += f"""
            <div class="col-md-6 mb-4">
                <div class="card h-100">
                    <div class="card-header">
                        <h3 class="card-title">{repo['name']}</h3>
                    </div>
                    <div class="card-body">
                        <p><strong>URL:</strong> <a href="{repo['url']}" target="_blank">{repo['full_name']}</a></p>
                        <p><strong>Description:</strong> {repo.get('description', 'No description')}</p>
                        <p><strong>Created:</strong> {created_at} | <strong>Updated:</strong> {updated_at}</p>
                        <p><strong>Stars:</strong> {repo['stargazers_count']} | <strong>Forks:</strong> {repo['forks_count']} | <strong>Issues:</strong> {repo['open_issues_count']}</p>
                        <p><strong>Visibility:</strong> {'Private' if repo['is_private'] else 'Public'}</p>
                        
                        <h4>Languages</h4>
                        {lang_html}
                        
                        {commits_html}
                    </div>
                </div>
            </div>
            """
        
        # Create HTML report
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>GitHub Data Report - {data['profile']['username']}</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <meta http-equiv="refresh" content="60"> <!-- Auto-refresh every minute -->
            <style>
                .stats-card {{
                    border-radius: 10px;
                    padding: 20px;
                    margin-bottom: 20px;
                    background-color: #f8f9fa;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .chart-container {{
                    position: relative;
                    height: 200px;
                    width: 100%;
                }}
            </style>
        </head>
        <body>
            <div class="container mt-5">
                <div class="row mb-4">
                    <div class="col-md-12">
                        <h1 class="display-4">GitHub Profile Report for {data['profile']['username']}</h1>
                        <p class="lead">Generated on: {current_time} (Auto-refreshes every minute)</p>
                        <div class="alert alert-info">
                            Data collection in progress. Processed {len(data['processed_repos'])} repositories so far.
                        </div>
                    </div>
                </div>
                
                <div class="row mb-4">
                    <div class="col-md-4">
                        <div class="stats-card">
                            <h3>Profile</h3>
                            <p><strong>Name:</strong> {data['profile']['name']}</p>
                            <p><strong>Username:</strong> {data['profile']['username']}</p>
                            <p><strong>Bio:</strong> {data['profile']['bio']}</p>
                            <p><strong>Location:</strong> {data['profile']['location']}</p>
                            <p><strong>Company:</strong> {data['profile']['company']}</p>
                            <p><strong>Followers:</strong> {data['profile']['followers']}</p>
                            <p><strong>Following:</strong> {data['profile']['following']}</p>
                        </div>
                    </div>
                    
                    <div class="col-md-4">
                        <div class="stats-card">
                            <h3>Summary Statistics</h3>
                            <p><strong>Total Repositories:</strong> {len(data['repositories'])}</p>
                            <p><strong>Total Commits:</strong> {data['total_commits']}</p>
                            <p><strong>Total Pull Requests:</strong> {data['total_prs']}</p>
                            <p><strong>Total Stars Received:</strong> {data['total_stars']}</p>
                        </div>
                    </div>
                    
                    <div class="col-md-4">
                        <div class="stats-card">
                            <h3>Language Distribution</h3>
                            <div class="chart-container">
                                <canvas id="languageChart"></canvas>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="row mb-4">
                    <div class="col-md-12">
                        <h2>Repository List</h2>
                        <div class="row">
                            {repos_html}
                        </div>
                    </div>
                </div>
            </div>
            
            <script>
                // Language chart
                const ctx = document.getElementById('languageChart').getContext('2d');
                const languageChart = new Chart(ctx, {{
                    type: 'pie',
                    data: {{
                        labels: [{', '.join([f'"{lang["language"]}"' for lang in language_data])}],
                        datasets: [{{
                            label: 'Language Distribution',
                            data: [{', '.join([str(lang["percentage"]) for lang in language_data])}],
                            backgroundColor: [
                                '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
                                '#FF9F40', '#8AC926', '#1982C4', '#6A4C93', '#F94144'
                            ],
                            borderWidth: 1
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                    }}
                }});
            </script>
            
            <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        </body>
        </html>
        """
        
        try:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            self.logger.info(f"Live HTML report updated: {html_path}")
        except Exception as e:
            self.logger.error(f"Failed to write HTML report: {e}")

    def generate_markdown_report(self, data):
        """Generate a detailed Markdown report from the collected data."""
        self.logger.info("Generating Markdown report...")
        if not data:
            return "Failed to collect GitHub data."
            
        md = f"# GitHub Profile Report for {data['profile']['username']}\n\n"
        md += f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Profile section
        md += "## Profile Information\n\n"
        md += f"- **Name**: {data['profile']['name']}\n"
        md += f"- **Username**: {data['profile']['username']}\n"
        if data['profile']['bio']:
            md += f"- **Bio**: {data['profile']['bio']}\n"
        if data['profile']['company']:
            md += f"- **Company**: {data['profile']['company']}\n"
        if data['profile']['location']:
            md += f"- **Location**: {data['profile']['location']}\n"
        if data['profile']['email']:
            md += f"- **Email**: {data['profile']['email']}\n"
        md += f"- **Followers**: {data['profile']['followers']}\n"
        md += f"- **Following**: {data['profile']['following']}\n"
        md += f"- **Public Repositories**: {data['profile']['public_repos']}\n"
        md += f"- **Account Created**: {parser.parse(data['profile']['created_at']).strftime('%Y-%m-%d')}\n\n"
        
        # Summary statistics
        md += "## Summary Statistics\n\n"
        md += f"- **Total Repositories**: {len(data['repositories'])}\n"
        md += f"- **Total Commits**: {data['total_commits']}\n"
        md += f"- **Total Pull Requests**: {data['total_prs']}\n"
        md += f"- **Total Stars Received**: {data['total_stars']}\n\n"
        
        # Language statistics
        md += "## Language Statistics\n\n"
        total_bytes = sum(data['language_stats'].values())
        
        if total_bytes > 0:
            md += "| Language | Bytes | Percentage |\n"
            md += "|----------|-------|------------|\n"
            
            # Sort languages by bytes (descending)
            sorted_languages = sorted(data['language_stats'].items(), key=lambda x: x[1], reverse=True)
            
            for language, bytes_count in sorted_languages:
                percentage = (bytes_count / total_bytes) * 100
                md += f"| {language} | {bytes_count:,} | {percentage:.2f}% |\n"
        else:
            md += "No language data available.\n"
        
        md += "\n"
        
        # Repositories section
        md += "## Repositories\n\n"
        
        # Sort repositories by updated_at (most recent first)
        sorted_repos = sorted(data['repositories'], key=lambda x: x['updated_at'], reverse=True)
        
        for repo in sorted_repos:
            md += f"### {repo['name']}\n\n"
            md += f"- **URL**: [{repo['full_name']}]({repo['url']})\n"
            if repo['description']:
                md += f"- **Description**: {repo['description']}\n"
            md += f"- **Visibility**: {'Private' if repo['is_private'] else 'Public'}\n"
            md += f"- **Created**: {parser.parse(repo['created_at']).strftime('%Y-%m-%d')}\n"
            md += f"- **Last Updated**: {parser.parse(repo['updated_at']).strftime('%Y-%m-%d')}\n"
            md += f"- **Last Push**: {parser.parse(repo['pushed_at']).strftime('%Y-%m-%d')}\n"
            md += f"- **Stars**: {repo['stargazers_count']}\n"
            md += f"- **Forks**: {repo['forks_count']}\n"
            md += f"- **Open Issues**: {repo['open_issues_count']}\n"
            md += f"- **Size**: {repo['size']} KB\n"
            
            # Languages in this repo
            if repo['languages']:
                md += "- **Languages**:\n"
                for lang, bytes_count in repo['languages'].items():
                    md += f"  - {lang}: {bytes_count:,} bytes\n"
            
            # Show recent commits
            if repo['commits']:
                md += f"\n#### Recent Commits ({len(repo['commits'])})\n\n"
                
                # Sort commits by date (most recent first)
                sorted_commits = sorted(repo['commits'], key=lambda x: x['date'], reverse=True)
                recent_commits = sorted_commits[:10]  # Show only the 10 most recent commits
                
                md += "| Date | Author | Message |\n"
                md += "|------|--------|----------|\n"
                
                for commit in recent_commits:
                    # Truncate long commit messages and escape pipes
                    message = commit['message'].replace('\n', ' ').replace('|', '\\|')
                    if len(message) > 50:
                        message = message[:47] + "..."
                    
                    date = parser.parse(commit['date']).strftime('%Y-%m-%d')
                    md += f"| {date} | {commit['author']} | {message} |\n"
                
                if len(sorted_commits) > 10:
                    md += f"\n*... and {len(sorted_commits) - 10} more commits*\n"
            
            # Show pull requests
            if repo['pull_requests']:
                md += f"\n#### Pull Requests ({len(repo['pull_requests'])})\n\n"
                
                # Sort PRs by creation date (most recent first)
                sorted_prs = sorted(repo['pull_requests'], key=lambda x: x['created_at'], reverse=True)
                recent_prs = sorted_prs[:5]  # Show only the 5 most recent PRs
                
                md += "| Number | Title | State | Created | Author |\n"
                md += "|--------|-------|-------|---------|--------|\n"
                
                for pr in recent_prs:
                    # Escape pipes in titles
                    title = pr['title'].replace('|', '\\|')
                    if len(title) > 40:
                        title = title[:37] + "..."
                    
                    date = parser.parse(pr['created_at']).strftime('%Y-%m-%d')
                    md += f"| #{pr['number']} | {title} | {pr['state']} | {date} | {pr['user']} |\n"
                
                if len(sorted_prs) > 5:
                    md += f"\n*... and {len(sorted_prs) - 5} more pull requests*\n"
            
            # Show recent comments
            if repo['comments']:
                md += f"\n#### Recent Comments ({len(repo['comments'])})\n\n"
                
                # Sort comments by creation date (most recent first)
                sorted_comments = sorted(repo['comments'], key=lambda x: x['created_at'], reverse=True)
                recent_comments = sorted_comments[:3]  # Show only the 3 most recent comments
                
                for comment in recent_comments:
                    date = parser.parse(comment['created_at']).strftime('%Y-%m-%d')
                    md += f"**{comment['user']}** on {date}:\n"
                    
                    # Truncate long comments
                    body = comment['body']
                    if len(body) > 200:
                        body = body[:197] + "..."
                    
                    md += f"> {body}\n\n"
                
                if len(sorted_comments) > 3:
                    md += f"*... and {len(sorted_comments) - 3} more comments*\n"
            
            md += "\n---\n\n"
        
        return md

    def save_report(self, markdown, filename="github_report.md"):
        """Save the Markdown report to a file."""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(markdown)
        self.logger.info(f"Report saved to {filename}")
        return filename


def main():
    # Get GitHub token from environment, hardcoded value, or user input
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        # Option to hardcode the token here
        token = "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"  # Replace with your token

    if not token or token == "your_personal_access_token_here":
        token = input("Enter your GitHub personal access token: ")
    
    fetcher = GitHubDataFetcher(token)
    print("Collecting GitHub data... This may take a while depending on your repository count.")
    print(f"Live web dashboard available at: {fetcher.web_server_url}")
    
    # Collect all data
    data = fetcher.collect_all_data()
    
    if data:
        # Generate and save markdown report
        markdown = fetcher.generate_markdown_report(data)
        filename = fetcher.save_report(markdown)
        print(f"Successfully generated GitHub data report: {filename}")
        print(f"Live HTML report available at: {os.path.join(fetcher.report_dir, 'live_report.html')}")
        
        # Keep the main thread running to allow the web server to continue
        print("\nPress Ctrl+C to exit...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Exiting...")
    else:
        print("Failed to collect GitHub data.")


if __name__ == "__main__":
    main()
