#!/bin/bash

# Civitai Authentication Setup Script
# This script helps you set up automatic Civitai authentication

set -e

echo "==================================="
echo "Civitai Authentication Setup"
echo "==================================="
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating..."
    touch .env
    echo "Created .env file"
else
    echo "✅ .env file found"
fi

# Check if .env is in .gitignore
if ! grep -q "\.env" .gitignore 2>/dev/null; then
    echo ""
    echo "⚠️  .env is not in .gitignore. Adding it..."
    echo ".env" >> .gitignore
    echo ".env.local" >> .gitignore
    echo ".civitai_session" >> .gitignore
    echo "✅ Added to .gitignore"
fi

echo ""
echo "Would you like to set up Civitai credentials for automatic authentication?"
read -p "Enter 'y' for yes, any other key to skip: " setup_creds

if [ "$setup_creds" = "y" ] || [ "$setup_creds" = "Y" ]; then
    echo ""
    echo "--- Civitai Credentials Setup ---"
    read -p "Enter your Civitai email: " email
    read -sp "Enter your Civitai password: " password
    echo ""

    # Check if variables already exist in .env
    if grep -q "^CIVITAI_USERNAME=" .env; then
        # Update existing
        sed -i "s/^CIVITAI_USERNAME=.*/CIVITAI_USERNAME=$email/" .env
        sed -i "s/^CIVITAI_PASSWORD=.*/CIVITAI_PASSWORD=$password/" .env
    else
        # Add new
        echo "" >> .env
        echo "# Civitai credentials for automatic authentication" >> .env
        echo "CIVITAI_USERNAME=$email" >> .env
        echo "CIVITAI_PASSWORD=$password" >> .env
    fi

    echo "✅ Credentials saved to .env"
else
    echo ""
    echo "Skipped credential setup. You can add them manually to .env:"
    echo "  CIVITAI_USERNAME=your_email@example.com"
    echo "  CIVITAI_PASSWORD=your_password"
fi

echo ""
echo "--- Installing Playwright ---"
pip install playwright

echo ""
echo "--- Installing Chromium browser for Playwright ---"
playwright install chromium

echo ""
echo "==================================="
echo "✅ Setup Complete!"
echo "==================================="
echo ""
echo "You can now use automatic authentication:"
echo ""
echo "  python -c \"from civitai import CivitaiPrivateScraper; s = CivitaiPrivateScraper(auto_authenticate=True); print('Success!')\""
echo ""
echo "To test authentication interactively:"
echo "  python civitai_auth.py"
echo ""
