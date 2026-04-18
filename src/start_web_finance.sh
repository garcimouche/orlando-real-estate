#!/bin/bash
# Serves the property_finance web app on http://localhost:8000
# The HTML loads property_finance.jsx and ../cache/scored_properties.json,
# so the server must be rooted at the project root (parent of src/).

set -e

# Resolve project root (parent of this script's directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT"

echo "🚀 Orlando STR — Finance Analyzer"
echo "📍 URL: http://localhost:${PORT}/src/property_finance.html"
echo "📂 Serving: $PROJECT_ROOT"
echo "🛑 Ctrl+C pour arrêter"
echo ""

# Check for scored_properties.json and warn if missing
if [ ! -f "$PROJECT_ROOT/cache/scored_properties.json" ]; then
    echo "⚠️  cache/scored_properties.json absent."
    echo "   Exécute d'abord : python3 src/property_finder.py"
    echo ""
fi

if command -v python3 &> /dev/null; then
    python3 -m http.server "$PORT"
elif command -v python &> /dev/null; then
    python -m http.server "$PORT"
else
    echo "❌ Python requis mais non trouvé."
    exit 1
fi
