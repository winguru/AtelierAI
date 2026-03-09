#!/bin/bash
# Clear Python cache and run civitai_trpc.py with verbose mode

echo "🧹 Clearing Python cache..."
find src -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find src -type f -name "*.pyc" -delete 2>/dev/null
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete 2>/dev/null

echo "✅ Cache cleared"
echo ""
echo "🚀 Running civitai_trpc.py with verbose mode..."
echo ""
python src/civitai_trpc.py --verbose
