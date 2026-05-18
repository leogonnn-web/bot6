# Script to upload code to GitHub
# Repository: https://github.com/leogonnn-web/bot6

Write-Host "Checking if Git is installed..." -ForegroundColor Yellow

try {
    git --version
    Write-Host "Git is installed!" -ForegroundColor Green
} catch {
    Write-Host "Git is not installed. Please install Git first:" -ForegroundColor Red
    Write-Host "1. Download Git from: https://git-scm.com/download/win" -ForegroundColor Cyan
    Write-Host "2. Run the installer and follow the instructions" -ForegroundColor Cyan
    Write-Host "3. Restart PowerShell and run this script again" -ForegroundColor Cyan
    Read-Host "Press Enter to exit"
    exit
}

Write-Host "`nInitializing Git repository..." -ForegroundColor Yellow
git init

Write-Host "Adding all files to Git..." -ForegroundColor Yellow
git add .

Write-Host "Creating initial commit..." -ForegroundColor Yellow
git commit -m "Initial commit - Upload bot4 project to GitHub"

Write-Host "Adding remote repository..." -ForegroundColor Yellow
git remote add origin https://github.com/leogonnn-web/bot6.git

Write-Host "Renaming branch to main..." -ForegroundColor Yellow
git branch -M main

Write-Host "Pushing to GitHub..." -ForegroundColor Yellow
Write-Host "You may be prompted for your GitHub username and password/token" -ForegroundColor Cyan
git push -u origin main

Write-Host "`nUpload complete!" -ForegroundColor Green
Write-Host "Your code is now available at: https://github.com/leogonnn-web/bot6" -ForegroundColor Cyan
