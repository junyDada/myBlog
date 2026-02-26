#!/usr/bin/env python3
"""
Google Doc to Blog Post Publisher
==================================
Converts a Google Doc to HTML using your template, downloads images,
updates the index page, and pushes to Git.

Usage:
    python publish.py "https://docs.google.com/document/d/DOC_ID/edit"
    python publish.py "https://docs.google.com/document/d/DOC_ID/edit" --no-push
    python publish.py "https://docs.google.com/document/d/DOC_ID/edit" --dry-run
    python publish.py --verify

Requirements:
    pip install requests beautifulsoup4 python-slugify
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import hashlib
import math

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString
    from slugify import slugify
except ImportError:
    print("❌ Missing dependencies. Install with:")
    print("   pip install requests beautifulsoup4 python-slugify")
    sys.exit(1)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Since publish.py lives at the blog root, use the script's directory
BLOG_ROOT = Path(__file__).parent.resolve()

# Relative paths within your blog
POSTS_DIR = "posts"                         # Where post HTML files go
IMAGES_DIR = "images/posts"                 # Where post images go  
TEMPLATE_FILE = "posts/post-template.html"  # Your template (in posts folder)
INDEX_FILE = "index.html"                   # Main index page

# Git settings
GIT_REMOTE = "origin"
GIT_BRANCH = "master"

# Template patterns - UPDATE THESE if you change your template's class names
# Format: 'description': (search_pattern, is_required)
TEMPLATE_PATTERNS = {
    'title_tag': (r'<title>.*?</title>', True),
    'meta_description': (r'<meta name="description"\s+content="[^"]*">', True),
    'post_title': (r'<h1 class="post-header__title">.*?</h1>', True),
    'post_subtitle': (r'<p class="post-header__subtitle">.*?</p>', True),
    'post_meta': (r'<p class="post-header__meta">.*?</p>', True),
    'post_body': (r'<div class="post-body">.*?</div>\s*\n\s*<div class="author-card">', True),
    'author_card': (r'<div class="author-card">', True),
}

# Author name for title tag (change if needed)
AUTHOR_NAME = "Junaid Akhtar"

# =============================================================================
# END CONFIGURATION
# =============================================================================


class PublishError(Exception):
    """Custom exception for publish errors."""
    pass


# =============================================================================
# VERIFICATION FUNCTIONS
# =============================================================================

def verify_environment() -> tuple[bool, list[str]]:
    """Verify the blog environment is correctly set up."""
    issues = []
    
    # Check blog root
    if not BLOG_ROOT.exists():
        issues.append(f"Blog root not found: {BLOG_ROOT}")
        return False, issues
    
    # Check required directories exist
    posts_dir = BLOG_ROOT / POSTS_DIR
    if not posts_dir.exists():
        issues.append(f"Posts directory not found: {posts_dir}")
    
    # Check template exists
    template_path = BLOG_ROOT / TEMPLATE_FILE
    if not template_path.exists():
        issues.append(f"Template not found: {template_path}")
    
    # Check index exists
    index_path = BLOG_ROOT / INDEX_FILE
    if not index_path.exists():
        issues.append(f"Index file not found: {index_path}")
    
    # Check if git is initialized
    git_dir = BLOG_ROOT / ".git"
    if not git_dir.exists():
        issues.append(f"Git not initialized in {BLOG_ROOT} (no .git folder)")
    
    return len(issues) == 0, issues


def verify_git() -> tuple[bool, list[str]]:
    """Verify Git is configured and ready."""
    issues = []
    
    try:
        os.chdir(BLOG_ROOT)
        
        # Check if git is available
        result = subprocess.run(['git', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            issues.append("Git is not installed or not in PATH")
            return False, issues
        
        # Check if this is a git repo
        result = subprocess.run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True)
        if result.returncode != 0:
            issues.append("Not a git repository")
            return False, issues
        
        # Check if remote exists
        result = subprocess.run(['git', 'remote', 'get-url', GIT_REMOTE], capture_output=True, text=True)
        if result.returncode != 0:
            issues.append(f"Git remote '{GIT_REMOTE}' not configured")
        
        # Check for uncommitted changes
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
        if result.stdout.strip():
            issues.append("You have uncommitted changes. Commit or stash them first.")
        
        # Check current branch
        result = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True)
        current_branch = result.stdout.strip()
        if current_branch != GIT_BRANCH:
            issues.append(f"On branch '{current_branch}', expected '{GIT_BRANCH}'")
        
    except FileNotFoundError:
        issues.append("Git is not installed")
    
    return len(issues) == 0, issues


def verify_template(template_path: Path) -> tuple[bool, list[str], dict[str, bool]]:
    """
    Verify the template has all required patterns.
    Returns (is_valid, issues, pattern_status).
    """
    if not template_path.exists():
        return False, [f"Template not found: {template_path}"], {}
    
    template = template_path.read_text(encoding='utf-8')
    issues = []
    pattern_status = {}
    
    for name, (pattern, required) in TEMPLATE_PATTERNS.items():
        found = bool(re.search(pattern, template, re.DOTALL))
        pattern_status[name] = found
        
        if required and not found:
            issues.append(f"Missing pattern '{name}': {pattern[:50]}...")
    
    # Additional structural checks
    if '<html' not in template:
        issues.append("Template doesn't appear to be valid HTML (no <html> tag)")
    
    if 'post-template' in template_path.name:
        # Make sure it has relative paths that work from posts/ folder
        if 'href="../' not in template and 'src="../' not in template:
            issues.append("Warning: Template may have incorrect relative paths for posts/ folder")
    
    return len(issues) == 0, issues, pattern_status


def verify_index(index_path: Path) -> tuple[bool, list[str]]:
    """Verify the index page has required structure."""
    if not index_path.exists():
        return False, [f"Index not found: {index_path}"]
    
    content = index_path.read_text(encoding='utf-8')
    issues = []
    
    if 'class="posts-list"' not in content:
        issues.append("Missing <ul class=\"posts-list\"> — new posts won't appear on homepage")
    
    if '<html' not in content:
        issues.append("Index doesn't appear to be valid HTML")
    
    return len(issues) == 0, issues


def run_full_verification(verbose: bool = True) -> bool:
    """Run all verification checks. Returns True if all pass."""
    all_ok = True
    
    if verbose:
        print("🔍 Running full verification...\n")
    
    # 1. Environment
    env_ok, env_issues = verify_environment()
    if verbose:
        if env_ok:
            print("✅ Environment: All paths exist")
        else:
            print("❌ Environment issues:")
            for issue in env_issues:
                print(f"   • {issue}")
            all_ok = False
    
    if not env_ok:
        # Can't continue if environment is broken
        return False
    
    # 2. Template
    template_path = BLOG_ROOT / TEMPLATE_FILE
    template_ok, template_issues, pattern_status = verify_template(template_path)
    if verbose:
        print()
        if template_ok:
            print("✅ Template: All patterns found")
            if pattern_status:
                for name, found in pattern_status.items():
                    status = "✓" if found else "✗"
                    print(f"      {status} {name}")
        else:
            print("❌ Template issues:")
            for issue in template_issues:
                print(f"   • {issue}")
            all_ok = False
    
    # 3. Index
    index_path = BLOG_ROOT / INDEX_FILE
    index_ok, index_issues = verify_index(index_path)
    if verbose:
        print()
        if index_ok:
            print("✅ Index: Structure valid")
        else:
            print("❌ Index issues:")
            for issue in index_issues:
                print(f"   • {issue}")
            all_ok = False
    
    # 4. Git
    git_ok, git_issues = verify_git()
    if verbose:
        print()
        if git_ok:
            print("✅ Git: Repository ready")
        else:
            print("⚠️  Git issues (publish will work, but won't auto-push):")
            for issue in git_issues:
                print(f"   • {issue}")
            # Git issues are warnings, not failures
    
    if verbose:
        print()
        if all_ok:
            print("🎉 All checks passed! Ready to publish.")
        else:
            print("❌ Fix the issues above before publishing.")
    
    return all_ok


# =============================================================================
# GOOGLE DOC FUNCTIONS
# =============================================================================

def extract_doc_id(url: str) -> str:
    """Extract Google Doc ID from various URL formats."""
    if not url:
        raise PublishError("No URL provided")
    
    # Handle /d/DOC_ID/ format
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    
    # Handle ?id=DOC_ID format
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if 'id' in params:
        return params['id'][0]
    
    raise PublishError(f"Could not extract document ID from URL: {url}")


def get_export_url(doc_id: str) -> str:
    """Get the HTML export URL for a Google Doc."""
    return f"https://docs.google.com/document/d/{doc_id}/export?format=html"


def download_google_doc(doc_id: str) -> str:
    """Download Google Doc as HTML."""
    url = get_export_url(doc_id)
    print(f"📥 Downloading Google Doc...")
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise PublishError("Download timed out. Check your internet connection.")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise PublishError("Document not found. Check the URL.")
        elif e.response.status_code == 403:
            raise PublishError("Access denied. Make sure the doc is set to 'Anyone with the link can view'.")
        else:
            raise PublishError(f"HTTP error: {e}")
    except requests.exceptions.RequestException as e:
        raise PublishError(f"Download failed: {e}")
    
    if len(response.text) < 100:
        raise PublishError("Downloaded content is too small. The document might be empty.")
    
    print(f"   ✅ Downloaded ({len(response.text):,} bytes)\n")
    return response.text, {}

def load_local_zip(zip_path: str) -> tuple:
    """
    Load a Google Doc exported as Web Page (.html, zipped).
    Returns (html_string, images_dict) where images_dict maps
    original src paths to image bytes.
    """
    import zipfile
    
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise PublishError(f"Zip file not found: {zip_path}")
    
    print(f"📦 Loading zip file: {zip_path.name}...")
    
    html = None
    images = {}
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            
            # Find the HTML file
            html_files = [n for n in names if n.endswith('.html')]
            if not html_files:
                raise PublishError("No HTML file found in zip. Make sure you exported as 'Web Page (.html, zipped)'.")
            
            html_file = html_files[0]
            html = zf.read(html_file).decode('utf-8')
            print(f"   ✅ Found HTML: {html_file}")
            
            # Load all images
            image_files = [n for n in names if any(
                n.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']
            )]
            
            for img_name in image_files:
                images[img_name] = zf.read(img_name)
            
            print(f"   ✅ Found {len(images)} images")
    
    except zipfile.BadZipFile:
        raise PublishError("File is not a valid zip file.")
    
    print()
    return html, images



# =============================================================================
# IMAGE HANDLING
# =============================================================================

def download_image(img_url: str, images_dir: Path, index: int) -> str | None:
    """Download an image and return the local filename, or None on failure."""
    try:
        response = requests.get(img_url, timeout=30)
        response.raise_for_status()
        
        # Determine extension from content type
        content_type = response.headers.get('content-type', 'image/png')
        ext_map = {
            'image/png': '.png',
            'image/jpeg': '.jpg',
            'image/gif': '.gif',
            'image/webp': '.webp',
            'image/svg+xml': '.svg',
        }
        ext = ext_map.get(content_type.split(';')[0], '.png')
        
        # Create unique filename using hash of URL
        url_hash = hashlib.md5(img_url.encode()).hexdigest()[:8]
        filename = f"img_{index:02d}_{url_hash}{ext}"
        
        filepath = images_dir / filename
        filepath.write_bytes(response.content)
        
        print(f"   📷 Downloaded: {filename}")
        return filename
        
    except Exception as e:
        print(f"   ⚠️  Failed to download image: {e}")
        return None


# =============================================================================
# CONTENT PARSING
# =============================================================================

def estimate_reading_time(text: str) -> int:
    """Estimate reading time in minutes (assuming 200 words per minute)."""
    words = len(text.split())
    minutes = max(1, math.ceil(words / 200))
    return minutes


def process_inline_formatting(elem) -> str:
    """Process inline formatting (bold, italic, links) within an element."""
    parts = []
    
    for child in elem.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name == 'a':
            href = child.get('href', '#')
            # Clean Google redirect URLs
            if 'google.com/url' in href:
                match = re.search(r'[?&]q=([^&]+)', href)
                if match:
                    href = unquote(match.group(1))
            text = child.get_text()
            parts.append(f'<a href="{href}">{text}</a>')
        elif child.name == 'span':
            style = child.get('style', '')
            text = child.get_text()
            
            is_bold = 'font-weight:700' in style or 'font-weight: 700' in style or 'bold' in style.lower()
            is_italic = 'font-style:italic' in style or 'font-style: italic' in style
            
            if is_bold and is_italic:
                text = f'<strong><em>{text}</em></strong>'
            elif is_bold:
                text = f'<strong>{text}</strong>'
            elif is_italic:
                text = f'<em>{text}</em>'
            
            parts.append(text)
        else:
            parts.append(child.get_text())
    
    return ''.join(parts)


def process_list(elem) -> str:
    """Process a list element."""
    tag = elem.name
    items = []
    
    for li in elem.find_all('li', recursive=False):
        text = li.get_text(strip=True)
        if text:
            items.append(f'<li>{text}</li>')
    
    if not items:
        return ''
    
    return f'<{tag}>\n            ' + '\n            '.join(items) + f'\n          </{tag}>'


def parse_google_doc_html(html: str, post_slug: str, blog_root: Path, local_images: dict = None) -> dict:
    """Parse Google Doc HTML and extract structured content."""
    soup = BeautifulSoup(html, 'html.parser')
    
    # Find the body content
    body = soup.find('body')
    if not body:
        raise PublishError("Could not find body in Google Doc HTML")
    
    # Extract title (first h1 or largest heading)
    title = None
    title_elem = body.find(['h1', 'h2', 'h3'])
    if title_elem:
        title = title_elem.get_text(strip=True)
        title_elem.decompose()  # Remove from content
    
    # Setup images directory
    images_dir = blog_root / IMAGES_DIR / post_slug
    images_dir.mkdir(parents=True, exist_ok=True)
    
    # Track processed image URLs to avoid duplicates
    processed_images = set()
    
    # Process content and build HTML
    content_parts = []
    plain_text_parts = []
    img_index = 0
    
    for elem in body.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'img', 'span']):
        # Skip empty elements
        text = elem.get_text(strip=True)
        
        if elem.name == 'img':
            src = elem.get('src', '')
            if src and src not in processed_images:
                processed_images.add(src)
                img_index += 1
                alt = elem.get('alt', f'Image {img_index}')
                
                if local_images:
                    # Find matching image in zip by filename
                    src_filename = Path(src).name.split('?')[0]
                    matched_key = next(
                        (k for k in local_images if Path(k).name == src_filename), None
                    )
                    if matched_key:
                        # Save image from zip
                        ext = Path(matched_key).suffix or '.png'
                        import hashlib
                        url_hash = hashlib.md5(src.encode()).hexdigest()[:8]
                        filename = f"img_{img_index:02d}_{url_hash}{ext}"
                        filepath = images_dir / filename
                        filepath.write_bytes(local_images[matched_key])
                        print(f"   📷 Saved from zip: {filename}")
                        img_path = f"../{IMAGES_DIR}/{post_slug}/{filename}"
                        content_parts.append(f'<figure><img src="{img_path}" alt="{alt}" loading="lazy"></figure>')
                    else:
                        print(f"   ⚠️  Image not found in zip: {src_filename}")
                else:
                    local_filename = download_image(src, images_dir, img_index)
                    if local_filename:
                        img_path = f"../{IMAGES_DIR}/{post_slug}/{local_filename}"
                        content_parts.append(f'<figure><img src="{img_path}" alt="{alt}" loading="lazy"></figure>')
            continue
        
        if not text:
            continue
        
        # Handle headings
        if elem.name in ['h1', 'h2']:
            content_parts.append(f'<h2>{text}</h2>')
            plain_text_parts.append(text)
        elif elem.name in ['h3', 'h4']:
            content_parts.append(f'<h3>{text}</h3>')
            plain_text_parts.append(text)
        
        # Handle paragraphs
        elif elem.name == 'p':
            # Check if it's a blockquote (Google Docs often uses specific styling)
            style = elem.get('style', '')
            parent_style = elem.parent.get('style', '') if elem.parent else ''
            
            is_blockquote = ('margin-left:' in style and 'pt' in style) or \
                           ('padding-left:' in style.replace(' ', '') and '40' in style) or \
                           ('margin-left:' in parent_style)
            
            if is_blockquote:
                content_parts.append(f'<blockquote>{text}</blockquote>')
            else:
                # Process inline formatting
                inner_html = process_inline_formatting(elem)
                if inner_html.strip():
                    content_parts.append(f'<p>{inner_html}</p>')
            plain_text_parts.append(text)
        
        # Handle lists
        elif elem.name in ['ul', 'ol']:
            list_html = process_list(elem)
            if list_html:
                content_parts.append(list_html)
            plain_text_parts.append(text)
    
    # Check for any remaining images we might have missed
    for img in body.find_all('img'):
        src = img.get('src', '')
        if src and src not in processed_images and 'googleusercontent' in src:
            processed_images.add(src)
            img_index += 1
            local_filename = download_image(src, images_dir, img_index)
            if local_filename:
                img_path = f"../{IMAGES_DIR}/{post_slug}/{local_filename}"
                alt = img.get('alt', f'Image {img_index}')
                content_parts.append(f'<figure><img src="{img_path}" alt="{alt}" loading="lazy"></figure>')
    
    # Clean up empty images folder if no images were downloaded
    if img_index == 0 and images_dir.exists():
        try:
            images_dir.rmdir()
        except OSError:
            pass  # Folder not empty or other issue, leave it
    
    plain_text = ' '.join(plain_text_parts)
    reading_time = estimate_reading_time(plain_text)
    
    return {
        'title': title,
        'content': '\n          '.join(content_parts),
        'reading_time': reading_time,
        'plain_text': plain_text,
        'image_count': img_index,
    }


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_post_html(template_path: Path, post_data: dict) -> tuple[str, list[str]]:
    """
    Generate the final post HTML from the template.
    Returns (html, list of warnings).
    """
    template = template_path.read_text(encoding='utf-8')
    warnings = []
    
    def safe_replace(pattern: str, replacement: str, flags: int = 0) -> None:
        """Replace pattern and track if it worked."""
        nonlocal template, warnings
        new_template = re.sub(pattern, replacement, template, flags=flags)
        if new_template == template:
            warnings.append(f"Pattern not found: {pattern[:60]}...")
        template = new_template
    
    # Escape special characters in content for regex replacement
    title = post_data['title'].replace('\\', '\\\\').replace('$', '\\$')
    subtitle = post_data['subtitle'].replace('\\', '\\\\').replace('$', '\\$')
    
    # Replace title tag
    safe_replace(
        r'<title>.*?</title>',
        f'<title>{title} - {AUTHOR_NAME}</title>'
    )
    
    # Replace meta description
    safe_replace(
        r'<meta name="description"\s+content="[^"]*">',
        f'<meta name="description"\n    content="{subtitle}">'
    )
    
    # Replace post header title
    safe_replace(
        r'<h1 class="post-header__title">.*?</h1>',
        f'<h1 class="post-header__title">{title}</h1>'
    )
    
    # Replace subtitle
    safe_replace(
        r'<p class="post-header__subtitle">.*?</p>',
        f'<p class="post-header__subtitle">{subtitle}</p>'
    )
    
    # Replace date and reading time
    date_html = f'''<p class="post-header__meta">
            <time datetime="{post_data["date_iso"]}">{post_data["date_formatted"]}</time> · {post_data["reading_time"]} min read
          </p>'''
    safe_replace(
        r'<p class="post-header__meta">.*?</p>',
        date_html,
        flags=re.DOTALL
    )
    
    # Replace post body content
    content = post_data['content'].replace('\\', '\\\\').replace('$', '\\$')
    safe_replace(
        r'<div class="post-body">.*?</div>\s*\n\s*<div class="author-card">',
        f'<div class="post-body">\n          {content}\n        </div>\n\n        <div class="author-card">',
        flags=re.DOTALL
    )
    
    return template, warnings


def update_index_page(index_path: Path, post_data: dict) -> tuple:
    """Add or update the post entry in the index page. Returns (success, message)."""
    content = index_path.read_text(encoding='utf-8')
    original = content

    # Create the new post entry
    new_entry = f'''<li class="post-item">
            <a href="posts/{post_data['filename']}" class="post-item__link">
              <article>
                <h3 class="post-item__title">{post_data['title']}</h3>
                <p class="post-item__excerpt">{post_data['subtitle']}</p>
                <p class="post-item__meta">
                  <time datetime="{post_data['date_iso']}">{post_data['date_formatted']}</time> · {post_data['reading_time']} min read
                </p>
              </article>
            </a>
          </li>'''

    # Check if this post already exists in the index
    existing_pattern = rf'<li class="post-item">.*?href="posts/{re.escape(post_data["filename"])}".*?</li>'
    if re.search(existing_pattern, content, re.DOTALL):
        # Update existing entry
        content = re.sub(existing_pattern, new_entry, content, flags=re.DOTALL)
        if content == original:
            return False, "Found existing entry but could not update it"
        backup_path = index_path.with_suffix('.html.bak')
        shutil.copy(index_path, backup_path)
        index_path.write_text(content, encoding='utf-8')
        return True, f"Updated existing entry in index.html (backup: {backup_path.name})"

    # Not found — insert as new entry
    pattern = r'(<ul class="posts-list">)\s*'
    if re.search(pattern, content):
        content = re.sub(
            pattern,
            f'\\1\n          {new_entry}\n          ',
            content
        )
    elif '<!-- Posts will be added here -->' in content:
        content = content.replace(
            '<!-- Posts will be added here -->',
            f'{new_entry}\n          <!-- Posts will be added here -->'
        )

    if content == original:
        return False, "Could not find insertion point in index.html"

    backup_path = index_path.with_suffix('.html.bak')
    shutil.copy(index_path, backup_path)
    index_path.write_text(content, encoding='utf-8')
    return True, f"Added new entry to index.html (backup: {backup_path.name})"

# =============================================================================
# GIT OPERATIONS
# =============================================================================

def git_push(blog_root: Path, post_title: str, dry_run: bool = False) -> bool:
    """Commit and push changes to Git. Returns True on success."""
    os.chdir(blog_root)
    
    if dry_run:
        print("\n🔍 Dry run - would execute:")
        print(f"   git add .")
        print(f"   git commit -m \"Add new post: {post_title}\"")
        print(f"   git push {GIT_REMOTE} {GIT_BRANCH}")
        return True
    
    print("\n📤 Pushing to Git...")
    
    try:
        # Add all changes
        result = subprocess.run(['git', 'add', '.'], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   ⚠️  git add failed: {result.stderr}")
            return False
        
        # Commit
        commit_msg = f"Add new post: {post_title}"
        result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
        if result.returncode != 0:
            if 'nothing to commit' in result.stdout:
                print("   ℹ️  Nothing to commit (no changes)")
                return True
            print(f"   ⚠️  git commit failed: {result.stderr}")
            return False
        
        # Push
        result = subprocess.run(['git', 'push', GIT_REMOTE, GIT_BRANCH], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   ⚠️  git push failed: {result.stderr}")
            print("\n   Your post was created locally. Push manually with:")
            print(f"   git push {GIT_REMOTE} {GIT_BRANCH}")
            return False
        
        print("   ✅ Successfully pushed to Git!")
        return True
        
    except FileNotFoundError:
        print("   ⚠️  Git is not installed or not in PATH")
        return False
    except Exception as e:
        print(f"   ⚠️  Git error: {e}")
        return False


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Convert a Google Doc to a blog post and publish it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python publish.py "https://docs.google.com/document/d/DOC_ID/edit"
  python publish.py "https://docs.google.com/document/d/DOC_ID/edit" --no-push
  python publish.py --verify
  python publish.py --dry-run "https://docs.google.com/document/d/DOC_ID/edit"
        """
    )
    parser.add_argument(
        'url',
        nargs='?',
        help="Google Doc URL"
    )
    parser.add_argument(
        '--no-push',
        action='store_true',
        help="Create files but don't push to Git"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help="Verify environment, template, and Git setup, then exit"
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help="Skip confirmation prompts"
    )
    
    args = parser.parse_args()
    
    # Handle --verify
    if args.verify:
        success = run_full_verification(verbose=True)
        sys.exit(0 if success else 1)
    
    # URL is required if not verifying
    if not args.url:
        parser.error("URL is required (use --verify to check setup without a URL)")
    
    # Quick environment check
    print("🔍 Checking environment...")
    env_ok, env_issues = verify_environment()
    if not env_ok:
        print("\n❌ Environment problems:")
        for issue in env_issues:
            print(f"   • {issue}")
        print("\nRun 'python publish.py --verify' for full diagnostics.")
        sys.exit(1)
    
    # Verify template
    template_path = BLOG_ROOT / TEMPLATE_FILE
    template_ok, template_issues, _ = verify_template(template_path)
    if not template_ok:
        print("\n❌ Template problems:")
        for issue in template_issues:
            print(f"   • {issue}")
        print("\nRun 'python publish.py --verify' for full diagnostics.")
        sys.exit(1)
    
    print("   ✅ Environment OK\n")
    
    # Detect if input is a zip file or a URL
    local_images = {}
    is_zip = args.url.endswith('.zip') or (Path(args.url).exists() and Path(args.url).suffix == '.zip')
    
    if is_zip:
        try:
            html, local_images = load_local_zip(args.url)
        except PublishError as e:
            print(f"❌ {e}")
            sys.exit(1)
    else:
        try:
            doc_id = extract_doc_id(args.url)
            print(f"📄 Document ID: {doc_id}")
        except PublishError as e:
            print(f"❌ {e}")
            sys.exit(1)
        
        try:
            html, _ = download_google_doc(doc_id)
        except PublishError as e:
            print(f"❌ {e}")
            sys.exit(1)
    
    # Parse to get title first (for slug generation)
    print("📝 Parsing document...")
    try:
        temp_data = parse_google_doc_html(html, "temp", BLOG_ROOT, local_images)
    except PublishError as e:
        print(f"❌ {e}")
        sys.exit(1)
        
    if not temp_data['title']:
        print("❌ Could not extract title from document.")
        print("   Make sure your document has a heading at the top (Heading 1 or 2).")
        sys.exit(1)
    
    print(f"   Title: {temp_data['title']}")
    
    # Generate slug from title
    post_slug = slugify(temp_data['title'], max_length=50)
    filename = f"{post_slug}.html"
    print(f"   Filename: {filename}")
    print(f"   Reading time: ~{temp_data['reading_time']} min")
    print(f"   Images: {temp_data['image_count']}")
    
    # Check if file already exists
    post_path = BLOG_ROOT / POSTS_DIR / filename
    if post_path.exists() and not args.force:
        print(f"\n⚠️  File already exists: {filename}")
        response = input("   Overwrite? (y/N): ").strip().lower()
        if response != 'y':
            print("   Aborted.")
            sys.exit(0)
    
    # Clean up temp images folder if it was created
    temp_images = BLOG_ROOT / IMAGES_DIR / "temp"
    if temp_images.exists():
        shutil.rmtree(temp_images, ignore_errors=True)
    
    # Now parse again with correct slug for images
    print(f"\n📥 Processing content and images...")
    parsed = parse_google_doc_html(html, post_slug, BLOG_ROOT, local_images)
    
    # Get subtitle from user
    print("\n" + "="*50)
    subtitle = input("📋 Enter subtitle/description for SEO:\n> ").strip()
    if not subtitle:
        # Generate a default subtitle from first paragraph
        first_para = parsed['plain_text'][:150].rsplit(' ', 1)[0] + '...' if len(parsed['plain_text']) > 150 else parsed['plain_text']
        subtitle = first_para or f"A post about {parsed['title']}"
        print(f"   Using: {subtitle[:60]}...")
    
    # Prepare post data
    now = datetime.now()
    post_data = {
        'title': parsed['title'],
        'subtitle': subtitle,
        'content': parsed['content'],
        'filename': filename,
        'slug': post_slug,
        'date_iso': now.strftime('%Y-%m-%d'),
        'date_formatted': now.strftime('%B %d, %Y'),
        'reading_time': parsed['reading_time'],
    }
    
    # Dry run stops here
    if args.dry_run:
        print("\n🔍 Dry run - would create:")
        print(f"   Post: {post_path}")
        print(f"   Images: {BLOG_ROOT / IMAGES_DIR / post_slug}/")
        print(f"   Index: Would add entry to {INDEX_FILE}")
        return
    
    # Generate the post HTML
    print(f"\n📄 Generating HTML...")
    post_html, generation_warnings = generate_post_html(template_path, post_data)
    
    if generation_warnings:
        print("   ⚠️  Warnings:")
        for warning in generation_warnings:
            print(f"      • {warning}")
        if not args.force:
            response = input("\n   Continue anyway? (y/N): ").strip().lower()
            if response != 'y':
                print("   Aborted.")
                sys.exit(1)
    
    # Save the post
    post_path.write_text(post_html, encoding='utf-8')
    print(f"   ✅ Created: {post_path}")
    
    # Update index page
    index_path = BLOG_ROOT / INDEX_FILE
    index_success, index_message = update_index_page(index_path, post_data)
    if index_success:
        print(f"   ✅ {index_message}")
    else:
        print(f"   ⚠️  {index_message}")
    
    # Push to Git
    if not args.no_push:
        git_push(BLOG_ROOT, post_data['title'], dry_run=args.dry_run)
    else:
        print("\n⏭️  Skipping Git push (--no-push flag)")
        print("   When ready, run:")
        print(f"   git add . && git commit -m \"Add: {post_data['title']}\" && git push")
    
    # Summary
    print("\n" + "="*50)
    print("🎉 Done!")
    print(f"   📍 Post: {post_path}")
    if parsed['image_count'] > 0:
        print(f"   🖼️  Images: {BLOG_ROOT / IMAGES_DIR / post_slug}/")
    if not args.no_push:
        print("   🌐 Check your website in a few minutes after Cloudflare deploys.")


if __name__ == '__main__':
    main()
