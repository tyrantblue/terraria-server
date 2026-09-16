#!/bin/bash

set -e

PROJECT_DIR="/opt/terraria"

GITHUB_USER="tyrantblue"
GITHUB_EMAIL="tyrantblue32@gmail.com"
REPO_NAME="terraria-server"

cd "$PROJECT_DIR"

echo "========================================"
echo " Terraria Server -> GitHub"
echo "========================================"
echo

echo "[1/7] Checking Git..."

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is not installed."
    exit 1
fi

echo "Git: $(git --version)"
echo

echo "[2/7] Checking GitHub CLI..."

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI (gh) is not installed."
    echo
    echo "Install it first, then run this script again."
    exit 1
fi

echo "GitHub CLI: $(gh --version | head -n 1)"
echo

echo "[3/7] Checking GitHub login..."

if ! gh auth status >/dev/null 2>&1; then
    echo "You are not logged in to GitHub."
    echo
    echo "Run:"
    echo
    echo "    gh auth login"
    echo
    exit 1
fi

echo "GitHub authentication OK."
echo

echo "[4/7] Creating .gitignore..."

cat > .gitignore <<'EOF'
# Terraria world data
worlds/

# Backups
backup/

# Runtime files
control/

# Runtime/application data
data/

# Server secrets / local configuration
config/serverconfig.txt

# Local backup files
docker-compose.yml.bak

# Python
__pycache__/
*.py[cod]
.venv/

# Environment / secrets
.env
.env.*

# OS/editor files
.DS_Store
Thumbs.db
.vscode/
.idea/
EOF

echo ".gitignore created."
echo

echo "[5/7] Initializing Git repository..."

if [ ! -d ".git" ]; then
    git init
else
    echo "Git repository already exists."
fi

git config user.name "$GITHUB_USER"
git config user.email "$GITHUB_EMAIL"

echo "Git user:"
echo "  name : $GITHUB_USER"
echo "  email: $GITHUB_EMAIL"
echo

echo "[6/7] Checking files..."

echo "Files that will NOT be uploaded:"
echo
echo "  worlds/"
echo "  backup/"
echo "  control/"
echo "  data/"
echo "  config/serverconfig.txt"
echo "  docker-compose.yml.bak"
echo

echo "Git status:"
git status --short
echo

echo "Adding files..."

git add .

echo
echo "Files staged for commit:"
git status --short
echo

echo "[7/7] Creating commit..."

if git diff --cached --quiet; then
    echo "Nothing new to commit."
else
    git commit -m "Initial Terraria server setup"
fi

echo
echo "Checking GitHub repository..."

if gh repo view "$GITHUB_USER/$REPO_NAME" >/dev/null 2>&1; then
    echo "Repository already exists:"
    echo "https://github.com/$GITHUB_USER/$REPO_NAME"
else
    echo "Creating private GitHub repository..."

    gh repo create "$GITHUB_USER/$REPO_NAME" \
        --private \
        --source=. \
        --remote=origin
fi

echo
echo "Pushing to GitHub..."

git branch -M main
git push -u origin main

echo
echo "========================================"
echo " Upload completed successfully!"
echo "========================================"
echo
echo "Repository:"
echo "https://github.com/$GITHUB_USER/$REPO_NAME"
echo
echo "Local directory:"
echo "$PROJECT_DIR"
echo
