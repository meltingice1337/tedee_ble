#!/usr/bin/env bash
set -euo pipefail

MANIFEST="custom_components/tedee_ble/manifest.json"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get current version from manifest.json
CURRENT=$(grep -oP '"version":\s*"\K[^"]+' "$MANIFEST")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# Calculate bump options
NEXT_PATCH="$MAJOR.$MINOR.$((PATCH + 1))"
NEXT_MINOR="$MAJOR.$((MINOR + 1)).0"
NEXT_MAJOR="$((MAJOR + 1)).0.0"

# Gather commits since last tag
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -n "$LAST_TAG" ]; then
    COMMITS=$(git log "${LAST_TAG}..HEAD" --oneline --no-decorate 2>/dev/null)
    COMMIT_LOG=$(git log "${LAST_TAG}..HEAD" --pretty=format:"- %s" 2>/dev/null)
else
    COMMITS=$(git log --oneline --no-decorate 2>/dev/null)
    COMMIT_LOG=$(git log --pretty=format:"- %s" 2>/dev/null)
fi

# Recommend bump based on commit messages
RECOMMEND="patch"
if echo "$COMMITS" | grep -qiE 'break|!:|major'; then
    RECOMMEND="major"
elif echo "$COMMITS" | grep -qiE 'feat|feature|add|new'; then
    RECOMMEND="minor"
fi

# Show info
echo -e "${CYAN}Current version:${NC} v${CURRENT}"
echo ""
if [ -n "$LAST_TAG" ]; then
    echo -e "${CYAN}Changes since ${LAST_TAG}:${NC}"
else
    echo -e "${CYAN}All commits:${NC}"
fi
echo "$COMMIT_LOG"
echo ""

# Show options
echo -e "${CYAN}Bump options:${NC}"
echo -e "  1) ${GREEN}patch${NC}  → v${NEXT_PATCH}"
echo -e "  2) ${YELLOW}minor${NC}  → v${NEXT_MINOR}"
echo -e "  3) ${RED}major${NC}  → v${NEXT_MAJOR}"
echo -e "  4) custom"
echo ""
echo -e "${CYAN}Recommended:${NC} ${GREEN}${RECOMMEND}${NC}"
echo ""

# Get user choice (or accept as argument)
if [ -n "${1:-}" ]; then
    CHOICE="$1"
else
    read -rp "Choose bump type [1/2/3/4] (default: $RECOMMEND): " CHOICE
fi

case "$CHOICE" in
    1|patch)  NEW_VERSION="$NEXT_PATCH" ;;
    2|minor)  NEW_VERSION="$NEXT_MINOR" ;;
    3|major)  NEW_VERSION="$NEXT_MAJOR" ;;
    4|custom)
        read -rp "Enter version (without v prefix): " NEW_VERSION
        ;;
    "")
        case "$RECOMMEND" in
            patch) NEW_VERSION="$NEXT_PATCH" ;;
            minor) NEW_VERSION="$NEXT_MINOR" ;;
            major) NEW_VERSION="$NEXT_MAJOR" ;;
        esac
        ;;
    *)
        # Treat as version string if it matches X.Y.Z
        if [[ "$CHOICE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            NEW_VERSION="$CHOICE"
        else
            echo -e "${RED}Invalid choice${NC}"
            exit 1
        fi
        ;;
esac

echo ""
echo -e "${CYAN}Bumping:${NC} v${CURRENT} → v${NEW_VERSION}"
echo ""

# Update manifest.json version
sed -i "s/\"version\": \"${CURRENT}\"/\"version\": \"${NEW_VERSION}\"/" "$MANIFEST"

# Stage all changes and commit
git add -A
git commit -S -m "Release v${NEW_VERSION}"

# Build release notes
NOTES="## Changes since v${CURRENT}

${COMMIT_LOG}
"

# Create signed tag
git tag -s -m "v${NEW_VERSION}" "v${NEW_VERSION}"

# Push
echo -e "${CYAN}Pushing to origin...${NC}"
git push origin master --follow-tags

# Create GitHub release
echo -e "${CYAN}Creating GitHub release...${NC}"
gh release create "v${NEW_VERSION}" \
    --title "v${NEW_VERSION}" \
    --notes "$NOTES"

echo ""
echo -e "${GREEN}Released v${NEW_VERSION}${NC}"
echo -e "URL: https://github.com/meltingice1337/tedee_ble/releases/tag/v${NEW_VERSION}"
