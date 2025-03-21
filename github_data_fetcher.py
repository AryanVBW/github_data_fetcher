import requests
import datetime
import time
import os
from dateutil import parser
import base64
from collections import Counter
import concurrent.futures
import json
import logging
from tqdm import tqdm

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
        self.cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Set up logging
        self.setup_logging()
        self.logger = logging.getLogger('github_fetcher')

    def setup_logging(self):
        """Set up logging configuration."""
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f'github_fetcher_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        logging.info(f"Logging initialized. Log file: {log_file}")

    def _get_cache_path(self, key):
        """Get the file path for a cache item."""
        return os.path.join(self.cache_dir, f"{key.replace('/', '_')}.json")

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
                self.logger.error(f"Cache read error: {e}")
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
            self.logger.error(f"Cache write error: {e}")

    def check_rate_limit(self):
        """Check and handle GitHub API rate limits."""
        # First check if we already have a rate limit stored
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            wait_time = self.rate_limit_reset - time.time()
            if wait_time > 0:
                self.logger.warning(f"Rate limit almost reached. Waiting for {wait_time:.2f} seconds...")
                time.sleep(wait_time + 1)  # Add a buffer second

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
        response = requests.get(f"{self.base_url}/rate_limit", headers=self.headers)
        if response.status_code == 200:
            limits = response.json()
            self.rate_limit_remaining = limits['resources']['core']['remaining']
            self.rate_limit_reset = limits['resources']['core']['reset']
            self._cache_set('rate_limit', limits)
            self.logger.info(f"Rate limit remaining: {self.rate_limit_remaining}")
        else:
            self.logger.error(f"Failed to check rate limit: {response.status_code}")
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
        response = requests.get(
            f"{self.base_url}/{endpoint}",
            headers=self.headers,
            params=params
        )
        
        if response.status_code == 200:
            data = response.json()
            if use_cache:
                self._cache_set(cache_key, data)
            return data
        else:
            self.logger.error(f"Error fetching {endpoint}: {response.status_code}")
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
        self.logger.debug(f"Fetching activity for {repo_name}")
        # This endpoint might return 202 indicating data is being prepared
        activity = self._make_request(f"repos/{repo_name}/stats/contributors")
        
        if activity == 202:
            self.logger.info(f"Data for {repo_name} is being prepared. Waiting...")
            time.sleep(5)  # Wait and try again
            return self.get_repository_activity(repo_name)
            
        return activity or []

    def process_repository(self, repo):
        """Process a single repository to collect all its data."""
        repo_name = repo['full_name']
        self.logger.info(f"Processing repo: {repo_name}")
        
        # Use thread pool to fetch data in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # Submit all tasks
            futures = {
                'commits': executor.submit(self.get_commits, repo_name),
                'prs': executor.submit(self.get_pull_requests, repo_name),
                'languages': executor.submit(self.get_languages, repo_name),
                'comments': executor.submit(self.get_comments, repo_name),
                'stars': executor.submit(self.get_stargazers, repo_name),
                'activity': executor.submit(self.get_repository_activity, repo_name)
            }
            
            # Create a progress bar for this repository's data fetching
            with tqdm(total=len(futures), desc=f"Fetching data for {repo_name}", leave=False) as pbar:
                # Get results from futures as they complete
                for name, future in concurrent.futures.as_completed(futures.values()):
                    pbar.update(1)
            
            # Get all results
            commits = futures['commits'].result()
            pull_requests = futures['prs'].result()
            languages = futures['languages'].result()
            comments = futures['comments'].result()
            stars = futures['stars'].result()
            activity = futures['activity'].result()
        
        self.logger.info(f"Completed processing repo: {repo_name}")
        
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
        
        return {
            'repo_data': repo_data,
            'commits_count': len(commits),
            'prs_count': len(pull_requests),
            'stars_count': repo['stargazers_count'],
            'languages': languages
        }

    def collect_all_data(self):
        """Collect all GitHub data and return a structured dict."""
        self.logger.info("Starting data collection process")
        data = {}
        
        # Get user profile
        self.logger.info("Fetching user profile data...")
        user = self.get_user_data()
        if not user:
            self.logger.error("Failed to fetch user data")
            return None
            
        data['profile'] = {
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
        self.logger.info(f"Fetched profile for {data['profile']['username']}")
        
        # Get repositories
        repos = self.get_repositories()
        
        data['repositories'] = []
        data['language_stats'] = Counter()
        data['total_commits'] = 0
        data['total_prs'] = 0
        data['total_stars'] = 0
        
        # Process repositories in parallel to improve performance
        max_workers = min(20, len(repos))  # Limit workers to avoid excessive API calls
        self.logger.info(f"Processing {len(repos)} repositories with {max_workers} workers...")
        
        with tqdm(total=len(repos), desc="Processing repositories") as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Process repos in parallel
                future_to_repo = {executor.submit(self.process_repository, repo): repo for repo in repos}
                
                for future in concurrent.futures.as_completed(future_to_repo):
                    repo = future_to_repo[future]
                    try:
                        result = future.result()
                        data['repositories'].append(result['repo_data'])
                        
                        # Update counters
                        data['total_commits'] += result['commits_count']
                        data['total_prs'] += result['prs_count']
                        data['total_stars'] += result['stars_count']
                        
                        # Update language stats
                        for lang, bytes_count in result['languages'].items():
                            data['language_stats'][lang] += bytes_count
                            
                        pbar.update(1)
                        pbar.set_postfix({
                            "commits": data['total_commits'], 
                            "PRs": data['total_prs'],
                            "stars": data['total_stars']
                        })
                        
                    except Exception as e:
                        self.logger.error(f"Error processing repo {repo['full_name']}: {e}")
                        pbar.update(1)
        
        self.logger.info("Data collection completed successfully")
        return data

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
        token = "your_personal_access_token_here"  # Replace with your token

    if not token or token == "your_personal_access_token_here":
        token = input("Enter your GitHub personal access token: ")
    
    fetcher = GitHubDataFetcher(token)
    print("Collecting GitHub data... This may take a while depending on your repository count.")
    
    # Collect all data
    data = fetcher.collect_all_data()
    
    if data:
        # Generate and save report
        markdown = fetcher.generate_markdown_report(data)
        filename = fetcher.save_report(markdown)
        print(f"Successfully generated GitHub data report: {filename}")
    else:
        print("Failed to collect GitHub data.")


if __name__ == "__main__":
    main()
